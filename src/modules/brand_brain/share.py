"""SPRINT-12 C-1 — the public diagnosis link ``/d/{token}``.

Anchor (§5-5 deviation, recorded in §6): there is no ``brand_diagnoses`` table — a diagnosis
persists only as its ``proposed`` recommendations — so ``{diag_id}`` **is a recommendation
id** of the brand and the share stamp lives on ``brand_recommendations.share_token_hash``
(``sha256`` hex, UNIQUE) + ``shared_at`` (migration 0016). The public page shows that
recommendation's title plus up to five short recommendations of the same brand and the
aggregated evidence grade — **never** competitor names, links, numbers, PII, rationale text
or private provenance. No AI call, no session identity: a hash lookup and a projection.

* ``POST  /brands/{brand_id}/brain/diagnoses/{diag_id}/share`` — OWNER only; 32 random bytes
  ⇒ the link is returned **once** (the token is never stored; re-sharing rotates it).
* ``DELETE …/share`` — OWNER only; clears the hash ⇒ the old link is 404 from now on.
* ``GET /api/v1/d/{token}`` — public (7th public route); 60/min/IP via the existing
  ``RateLimiter``; ``Cache-Control: public, max-age=300``; unknown/revoked ⇒ 404.

SPRINT-12.1 A-1 (review S-1): a recommendation the owner **dismissed** is never part of the
public surface — it is filtered out of the card (``PUBLIC_STATUSES``), sharing it answers
**409 ``recommendation_dismissed``** (a local HTTP error, no new ``ErrorCategory``), and a
link whose anchor was dismissed *after* sharing answers 404 like a revoked one.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from src.core.config import Settings, get_settings
from src.modules.auth.dependencies import CurrentUserFresh, client_ip, get_rate_limiter
from src.modules.auth.domain.rate_limit import RateLimiter
from src.modules.brand_brain.domain.models import Recommendation
from src.modules.brand_brain.domain.repository import BrandBrainRepository
from src.modules.brand_brain.exceptions import RecommendationNotFoundError
from src.modules.brand_brain.router import (
    BRAIN_PREFIX,
    Repo,
    Resolver,
    _authorise,
    get_brain_repository,
    to_http,
)
from src.modules.tenancy.domain.access import GRANT_ROLES

TOKEN_BYTES = 32
SHARE_PATH_RE = r"^[A-Za-z0-9_-]{40,64}$"  # urlsafe-base64 of 32 bytes is 43 chars
PUBLIC_BUCKET = "public:diagnosis"
PUBLIC_LIMIT = 60
PUBLIC_WINDOW_SECONDS = 60
CACHE_CONTROL = "public, max-age=300"
MIN_RECOMMENDATIONS = 3
MAX_RECOMMENDATIONS = 5
MAX_PUBLIC_TITLE = 80
EVIDENCE_GRADES = ("measured", "inferred", "estimated")
# A-1 (S-1): the only recommendation statuses an anonymous visitor may ever see.
PUBLIC_STATUSES = frozenset({"proposed", "accepted", "materialized"})
# Anything that could leak a link, a number, a handle or an address out of a title.
_SCRUB = re.compile(r"(https?://\S+|www\.\S+|\S+@\S+|@\w+|\+?\d[\d\s().-]{5,}\d|\d+)")

router = APIRouter(tags=["Brand Brain (M7)"])
public_router = APIRouter(tags=["Public diagnosis link"])

Limiter = Annotated[RateLimiter, Depends(get_rate_limiter)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


# ---------------------------------------------------------------- schemas --
class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ShareLink(_Strict):
    """Returned **once** by ``POST …/share`` — the token is not recoverable afterwards."""

    url: str
    shared_at: datetime


class DiagnosisShareRecommendation(_Strict):
    kind: str
    title: str = Field(max_length=MAX_PUBLIC_TITLE)
    evidence_grade: str


class DiagnosisPublicShare(_Strict):
    """What an anonymous visitor sees: a title, 3–5 short recommendations, the evidence
    grade, a watermark and a call to action. No PII, no raw evidence, no provenance."""

    title: str = Field(max_length=MAX_PUBLIC_TITLE)
    recommendations: list[DiagnosisShareRecommendation] = Field(
        min_length=1, max_length=MAX_RECOMMENDATIONS
    )
    evidence_grade: str
    generated_at: datetime
    watermark: str
    cta_url: str


# ---------------------------------------------------------------- helpers --
def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def scrub_title(title: str) -> str:
    """Drop links / handles / numbers from a title and cap its length (public surface)."""
    cleaned = " ".join(_SCRUB.sub("", title).split())
    return (cleaned or "Recommendation")[:MAX_PUBLIC_TITLE]


def evidence_grade(rec: Recommendation) -> str:
    """The **strongest** grade cited by the recommendation (D-074 order)."""
    summary = rec.evidence_kinds_summary
    for grade in EVIDENCE_GRADES:
        if getattr(summary, grade) > 0:
            return grade
    return "estimated"


def overall_grade(rows: list[Recommendation]) -> str:
    """Best grade across the shown recommendations — ``measured`` wins over ``inferred``…"""
    grades = {evidence_grade(r) for r in rows}
    for grade in EVIDENCE_GRADES:
        if grade in grades:
            return grade
    return "estimated"


def build_public_share(
    anchor: Recommendation, siblings: list[Recommendation], *, app_name: str, cta_url: str
) -> DiagnosisPublicShare:
    """Anchor first, then the newest siblings of the same brand until five are shown.
    Dismissed siblings are dropped here (A-1) whatever the repository returned."""
    rows = [anchor] + [r for r in siblings if r.id != anchor.id and r.status in PUBLIC_STATUSES]
    rows = rows[:MAX_RECOMMENDATIONS]
    return DiagnosisPublicShare(
        title=scrub_title(anchor.title),
        recommendations=[
            DiagnosisShareRecommendation(
                kind=r.kind, title=scrub_title(r.title), evidence_grade=evidence_grade(r)
            )
            for r in rows
        ],
        evidence_grade=overall_grade(rows),
        generated_at=anchor.created_at,
        watermark=f"Generated by {app_name}",
        cta_url=cta_url,
    )


def _not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, detail={"error": "diagnosis_not_found"})


def _dismissed() -> HTTPException:
    """A-1: refusing to publish what the owner rejected — 409, codes only (D-069)."""
    return HTTPException(
        status.HTTP_409_CONFLICT,
        detail={
            "error": "recommendation_dismissed",
            "message": "a dismissed recommendation cannot be shared publicly",
        },
    )


# ---------------------------------------------------------- owner routes --
@router.post(
    f"{BRAIN_PREFIX}/diagnoses/{{diag_id}}/share",
    response_model=ShareLink,
    status_code=status.HTTP_201_CREATED,
    summary="Create (or rotate) the public link of a diagnosis — OWNER only, fresh session",
    responses={
        404: {"description": "recommendation_not_found"},
        409: {"description": "recommendation_dismissed"},
    },
)
async def share_diagnosis(
    brand_id: UUID,
    diag_id: UUID,
    request: Request,
    actor: CurrentUserFresh,
    resolver: Resolver,
    repo: Repo,
    settings: SettingsDep,
) -> ShareLink:
    """32 random bytes ⇒ only ``sha256(token)`` is stored; the URL is returned this once.
    A-1: the row is read (locked) first — unknown ⇒ 404, ``dismissed`` ⇒ 409."""
    await _authorise(resolver, actor.tenant_id, brand_id, GRANT_ROLES)
    current = await repo.get_recommendation_for_update(brand_id, diag_id)
    if current is None:
        raise to_http(RecommendationNotFoundError())
    if current.status not in PUBLIC_STATUSES:
        raise _dismissed()
    token = secrets.token_urlsafe(TOKEN_BYTES)
    now = datetime.now(UTC)
    changed = await repo.set_recommendation_share(
        brand_id, diag_id, token_hash=hash_token(token), shared_at=now
    )
    if not changed:
        raise to_http(RecommendationNotFoundError())
    base = str(request.base_url).rstrip("/")
    return ShareLink(url=f"{base}{settings.api_v1_prefix}/d/{token}", shared_at=now)


@router.delete(
    f"{BRAIN_PREFIX}/diagnoses/{{diag_id}}/share",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke the public link of a diagnosis — OWNER only, fresh session",
    responses={404: {"description": "recommendation_not_found"}},
)
async def unshare_diagnosis(
    brand_id: UUID,
    diag_id: UUID,
    actor: CurrentUserFresh,
    resolver: Resolver,
    repo: Repo,
) -> Response:
    await _authorise(resolver, actor.tenant_id, brand_id, GRANT_ROLES)
    changed = await repo.set_recommendation_share(
        brand_id, diag_id, token_hash=None, shared_at=None
    )
    if not changed:
        raise to_http(RecommendationNotFoundError())
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------- public route --
@public_router.get(
    "/d/{token}",
    response_model=DiagnosisPublicShare,
    summary="Public, read-only diagnosis card (watermarked) — no auth, 60/min/IP",
    responses={404: {"description": "diagnosis_not_found"}, 429: {"description": "rate_limited"}},
)
async def public_diagnosis(
    token: Annotated[str, Path(pattern=SHARE_PATH_RE)],
    request: Request,
    response: Response,
    limiter: Limiter,
    settings: SettingsDep,
    repo: Annotated[BrandBrainRepository, Depends(get_brain_repository)],
) -> DiagnosisPublicShare:
    retry = limiter.hit(PUBLIC_BUCKET, client_ip(request), PUBLIC_LIMIT, PUBLIC_WINDOW_SECONDS)
    if retry is not None:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"error": "rate_limited"},
            headers={"Retry-After": str(int(retry))},
        )
    anchor = await repo.get_recommendation_by_share_hash(hash_token(token))
    if anchor is None or anchor.status not in PUBLIC_STATUSES:
        raise _not_found()  # unknown, revoked, or dismissed after sharing (A-1)
    # ``status=None`` keeps one query; ``build_public_share`` drops dismissed rows (A-1). The
    # over-fetch covers up to ``MAX_RECOMMENDATIONS`` dismissed siblings on top of the shown ones.
    siblings = await repo.list_recommendations(
        anchor.brand_id, status=None, limit=2 * MAX_RECOMMENDATIONS + 1
    )
    response.headers["Cache-Control"] = CACHE_CONTROL
    base = str(request.base_url).rstrip("/")
    return build_public_share(anchor, siblings, app_name=settings.app_name, cta_url=f"{base}/")
