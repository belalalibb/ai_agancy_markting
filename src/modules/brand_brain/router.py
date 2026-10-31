"""Protected Brand Brain routes (SPRINT-10 B-1) under ``/brands/{brand_id}/brain``.

Roles (§2-0 و): profile read / revisions / competitors read / evidence read ⇒ every brand
role (VIEWER gets the reduced projections — D-066); profile write / competitors write /
evidence refresh ⇒ OWNER/MANAGER; ``GET /cost`` ⇒ OWNER only. Same policy as ``content``:
no access ⇒ 404 ``brand_not_found``; wrong role ⇒ 403 ``insufficient_role``.
"""

from __future__ import annotations

import base64
import binascii
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db_session
from src.modules.auth.dependencies import CurrentUser, CurrentUserFresh, get_rate_limiter
from src.modules.auth.domain.rate_limit import RateLimiter
from src.modules.brand_brain.domain.repository import BrandBrainRepository, EvidenceCursor
from src.modules.brand_brain.exceptions import (
    BlockedPlatformHostError,
    BrandBrainError,
    CompetitorExistsError,
    CompetitorLimitError,
    CompetitorNotFoundError,
    InvalidHostError,
    InvalidIfMatchError,
    ProfileVersionConflictError,
    RefreshRateLimitedError,
    UnknownMetricError,
)
from src.modules.brand_brain.infrastructure.internal_metrics import PgInternalMetricsReader
from src.modules.brand_brain.infrastructure.postgres_repository import (
    PostgresBrandBrainRepository,
)
from src.modules.brand_brain.schemas import (
    CompetitorCreate,
    CompetitorPublic,
    CompetitorsPage,
    CostDayPublic,
    CostReport,
    EvidencePage,
    ProfilePublic,
    ProfileUpdate,
    RefreshResult,
    RevisionPublic,
    RevisionsPage,
    evidence_to_public,
)
from src.modules.brand_brain.service import (
    BrandBrainService,
    EvidenceCollector,
    InternalMetricsReader,
)
from src.modules.tenancy.dependencies import get_brand_access_resolver
from src.modules.tenancy.domain.access import (
    CONNECTION_WRITE_ROLES,
    GRANT_ROLES,
    READ_ROLES,
    BrandAccessResolver,
    BrandRole,
)
from src.modules.tenancy.exceptions import BrandNotFoundError, InsufficientRoleError, TenancyError
from src.modules.tenancy.router import to_http as tenancy_to_http

router = APIRouter(tags=["Brand Brain (M7)"])

BRAIN_PREFIX = "/brands/{brand_id}/brain"
MAX_LIST_LIMIT = 100
PRIVATE_ROLES = CONNECTION_WRITE_ROLES  # OWNER/MANAGER see confidence / source_ref / banned


# ------------------------------------------------------------ dependencies --
def get_brain_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> BrandBrainRepository:
    return PostgresBrandBrainRepository(session)


def get_internal_metrics_reader(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> InternalMetricsReader:
    return PgInternalMetricsReader(session)


Repo = Annotated[BrandBrainRepository, Depends(get_brain_repository)]
MetricsReader = Annotated[InternalMetricsReader, Depends(get_internal_metrics_reader)]
Resolver = Annotated[BrandAccessResolver, Depends(get_brand_access_resolver)]
Limiter = Annotated[RateLimiter, Depends(get_rate_limiter)]
LimitQuery = Annotated[int, Query(ge=1, le=MAX_LIST_LIMIT)]
CursorQuery = Annotated[str | None, Query(max_length=128)]
IfMatch = Annotated[str | None, Header(alias="If-Match")]


def get_brain_service(repo: Repo, reader: MetricsReader, limiter: Limiter) -> BrandBrainService:
    return BrandBrainService(repo, limiter=limiter, collector=EvidenceCollector(repo, reader))


Service = Annotated[BrandBrainService, Depends(get_brain_service)]


async def _authorise(
    resolver: BrandAccessResolver, tenant_id: UUID, brand_id: UUID, allowed: frozenset[BrandRole]
) -> BrandRole:
    try:
        role = await resolver.resolve(tenant_id, brand_id)
        if role is None:
            raise BrandNotFoundError()
        if role not in allowed:
            raise InsufficientRoleError(f"role {role.value!r} may not perform this action")
    except TenancyError as exc:
        raise tenancy_to_http(exc) from exc
    return role


def to_http(exc: BrandBrainError) -> HTTPException:
    detail: dict[str, object] = {"error": exc.error}
    headers: dict[str, str] | None = None
    if isinstance(exc, ProfileVersionConflictError):
        code = status.HTTP_412_PRECONDITION_FAILED
        detail["current_version"] = exc.current_version
    elif isinstance(exc, InvalidIfMatchError):
        code = status.HTTP_428_PRECONDITION_REQUIRED
    elif isinstance(exc, CompetitorLimitError | CompetitorExistsError):
        code = status.HTTP_409_CONFLICT
    elif isinstance(exc, CompetitorNotFoundError):
        code = status.HTTP_404_NOT_FOUND
    elif isinstance(exc, InvalidHostError | BlockedPlatformHostError | UnknownMetricError):
        code = status.HTTP_422_UNPROCESSABLE_CONTENT
    elif isinstance(exc, RefreshRateLimitedError):
        code = status.HTTP_429_TOO_MANY_REQUESTS
        headers = {"Retry-After": str(int(exc.retry_after_seconds))}
    else:
        code = status.HTTP_400_BAD_REQUEST
    return HTTPException(code, detail=detail, headers=headers)


def parse_if_match(value: str | None) -> int:
    """``If-Match: 3`` or ``If-Match: "3"`` ⇒ 3; missing / malformed ⇒ 428."""
    if value is None:
        raise InvalidIfMatchError()
    raw = value.strip().strip('"')
    if not raw.isdigit() or int(raw) < 1:
        raise InvalidIfMatchError()
    return int(raw)


def encode_cursor(at: datetime, item_id: UUID) -> str:
    raw = f"{at.isoformat()}|{item_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str | None) -> EvidenceCursor | None:
    if cursor is None:
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        at, item_id = base64.urlsafe_b64decode(padded).decode().split("|", 1)
        return datetime.fromisoformat(at), UUID(item_id)
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"error": "invalid_cursor", "message": "cursor is not valid"},
        ) from exc


# ------------------------------------------------------------------- profile --
@router.get(
    f"{BRAIN_PREFIX}/profile",
    response_model=ProfilePublic,
    response_model_exclude_none=True,
    summary="Brand profile — all roles (VIEWER without banned_phrases)",
)
async def get_profile(
    brand_id: UUID, actor: CurrentUser, resolver: Resolver, service: Service
) -> ProfilePublic:
    role = await _authorise(resolver, actor.tenant_id, brand_id, READ_ROLES)
    profile = await service.get_profile(brand_id)
    return ProfilePublic.from_domain(profile, private=role in PRIVATE_ROLES)


@router.put(
    f"{BRAIN_PREFIX}/profile",
    response_model=ProfilePublic,
    summary="Replace the brand profile — OWNER/MANAGER, If-Match: <version>",
    responses={
        412: {"description": "profile_version_conflict — body carries current_version"},
        428: {"description": "if_match_invalid — the header is required"},
    },
)
async def put_profile(
    brand_id: UUID,
    payload: ProfileUpdate,
    actor: CurrentUser,
    resolver: Resolver,
    service: Service,
    response: Response,
    if_match: IfMatch = None,
) -> ProfilePublic:
    await _authorise(resolver, actor.tenant_id, brand_id, CONNECTION_WRITE_ROLES)
    try:
        profile = await service.update_profile(
            brand_id,
            payload,
            expected_version=parse_if_match(if_match),
            actor_user_id=actor.user_id,
        )
    except BrandBrainError as exc:
        raise to_http(exc) from exc
    response.headers["ETag"] = f'"{profile.version}"'
    return ProfilePublic.from_domain(profile, private=True)


@router.get(
    f"{BRAIN_PREFIX}/profile/revisions",
    response_model=RevisionsPage,
    summary="Profile revisions (newest first) — all roles",
)
async def list_revisions(
    brand_id: UUID, actor: CurrentUser, resolver: Resolver, service: Service
) -> RevisionsPage:
    await _authorise(resolver, actor.tenant_id, brand_id, READ_ROLES)
    revisions = await service.list_revisions(brand_id)
    return RevisionsPage(items=[RevisionPublic.from_domain(r) for r in revisions])


# --------------------------------------------------------------- competitors --
@router.get(
    f"{BRAIN_PREFIX}/competitors",
    response_model=CompetitorsPage,
    summary="Competitors (≤3) — all roles",
)
async def list_competitors(
    brand_id: UUID, actor: CurrentUser, resolver: Resolver, service: Service
) -> CompetitorsPage:
    await _authorise(resolver, actor.tenant_id, brand_id, READ_ROLES)
    items = await service.list_competitors(brand_id)
    return CompetitorsPage(items=[CompetitorPublic.from_domain(c) for c in items], limit=3)


@router.post(
    f"{BRAIN_PREFIX}/competitors",
    response_model=CompetitorPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Add a competitor (legal public sources only) — OWNER/MANAGER",
    responses={
        409: {"description": "competitor_limit_reached / competitor_exists"},
        422: {"description": "host_invalid / host_blocked_platform"},
    },
)
async def add_competitor(
    brand_id: UUID,
    payload: CompetitorCreate,
    actor: CurrentUser,
    resolver: Resolver,
    service: Service,
) -> CompetitorPublic:
    await _authorise(resolver, actor.tenant_id, brand_id, CONNECTION_WRITE_ROLES)
    try:
        competitor = await service.add_competitor(brand_id, payload, actor_user_id=actor.user_id)
    except BrandBrainError as exc:
        raise to_http(exc) from exc
    return CompetitorPublic.from_domain(competitor)


@router.delete(
    f"{BRAIN_PREFIX}/competitors/{{competitor_id}}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a competitor — OWNER/MANAGER",
)
async def delete_competitor(
    brand_id: UUID,
    competitor_id: UUID,
    actor: CurrentUserFresh,
    resolver: Resolver,
    service: Service,
) -> Response:
    await _authorise(resolver, actor.tenant_id, brand_id, CONNECTION_WRITE_ROLES)
    try:
        await service.delete_competitor(brand_id, competitor_id)
    except BrandBrainError as exc:
        raise to_http(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ------------------------------------------------------------------ evidence --
@router.get(
    f"{BRAIN_PREFIX}/evidence",
    response_model=EvidencePage,
    summary="Graded evidence, keyset observed_at DESC — all roles (VIEWER: summary only)",
)
async def list_evidence(
    brand_id: UUID,
    actor: CurrentUser,
    resolver: Resolver,
    service: Service,
    subject: Annotated[Literal["brand", "competitor"] | None, Query()] = None,
    subject_id: Annotated[UUID | None, Query()] = None,
    metric: Annotated[str | None, Query(max_length=64)] = None,
    kind: Annotated[Literal["measured", "inferred", "estimated"] | None, Query()] = None,
    limit: LimitQuery = 50,
    cursor: CursorQuery = None,
) -> EvidencePage:
    role = await _authorise(resolver, actor.tenant_id, brand_id, READ_ROLES)
    try:
        rows, has_more = await service.list_evidence(
            brand_id,
            subject_kind=subject,
            subject_id=subject_id,
            metric=metric,
            evidence_kind=kind,
            limit=limit,
            cursor=decode_cursor(cursor),
        )
    except BrandBrainError as exc:
        raise to_http(exc) from exc
    private = role in PRIVATE_ROLES
    next_cursor = encode_cursor(rows[-1].observed_at, rows[-1].id) if has_more and rows else None
    return EvidencePage(
        items=[evidence_to_public(r, private=private) for r in rows],
        next_cursor=next_cursor,
        limit=limit,
    )


@router.post(
    f"{BRAIN_PREFIX}/evidence/refresh",
    response_model=RefreshResult,
    summary="Measure the internal metrics now (60/hour/brand) — OWNER/MANAGER",
    responses={429: {"description": "refresh_rate_limited (Retry-After)"}},
)
async def refresh_evidence(
    brand_id: UUID, actor: CurrentUser, resolver: Resolver, service: Service
) -> RefreshResult:
    await _authorise(resolver, actor.tenant_id, brand_id, CONNECTION_WRITE_ROLES)
    try:
        rows = await service.refresh_internal_evidence(brand_id)
    except BrandBrainError as exc:
        raise to_http(exc) from exc
    return RefreshResult(
        collected=len(rows), metrics=[r.metric for r in rows], observed_at=rows[0].observed_at
    )


# ---------------------------------------------------------------------- cost --
@router.get(
    f"{BRAIN_PREFIX}/cost",
    response_model=CostReport,
    summary="AI cost log, daily for 30 days — OWNER only",
)
async def get_cost(
    brand_id: UUID, actor: CurrentUser, resolver: Resolver, service: Service
) -> CostReport:
    await _authorise(resolver, actor.tenant_id, brand_id, GRANT_ROLES)
    summary = await service.cost_report(brand_id)
    costs = [d.estimated_cost_usd for d in summary.days if d.estimated_cost_usd is not None]
    return CostReport(
        days=summary.window_days,
        items=[CostDayPublic.from_domain(d) for d in summary.days],
        total_calls=sum(d.calls for d in summary.days),
        total_prompt_tokens=sum(d.prompt_tokens for d in summary.days),
        total_completion_tokens=sum(d.completion_tokens for d in summary.days),
        total_estimated_cost_usd=sum(costs) if costs else None,
    )
