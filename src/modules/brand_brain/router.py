"""Protected Brand Brain routes (SPRINT-10 B-1) under ``/brands/{brand_id}/brain``.

Roles (§2-0 و): profile read / revisions / competitors read / evidence read ⇒ every brand
role (VIEWER gets the reduced projections — D-066); profile write / competitors write /
evidence refresh ⇒ OWNER/MANAGER; ``GET /cost`` ⇒ OWNER only. Same policy as ``content``:
no access ⇒ 404 ``brand_not_found``; wrong role ⇒ 403 ``insufficient_role``.
"""

from __future__ import annotations

import base64
import binascii
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import Settings, get_settings
from src.core.database import get_db_session
from src.core.http import HttpClient
from src.modules.ai_gateway.budget import BudgetUsageReader, DailyBudget
from src.modules.ai_gateway.contract import AIError
from src.modules.ai_gateway.cost_logging import CostLoggingGateway, PriceTable
from src.modules.ai_gateway.dependencies import get_ai_gateway, get_budget_usage_reader
from src.modules.ai_gateway.exceptions import AIProviderNotConfiguredError
from src.modules.ai_gateway.facades import HttpOpenAICompatibleProvider
from src.modules.ai_gateway.gateway import AIGateway, CompletionRequest, CompletionResult
from src.modules.auth.dependencies import CurrentUser, CurrentUserFresh, get_rate_limiter
from src.modules.auth.domain.rate_limit import RateLimiter
from src.modules.brand_brain.advisor import BrandAdvisor
from src.modules.brand_brain.domain.repository import (
    BrandBrainRepository,
    CostLogWriter,
    EvidenceCursor,
)
from src.modules.brand_brain.exceptions import (
    BlockedPlatformHostError,
    BrainBudgetExceededError,
    BrainRateLimitedError,
    BrandBrainError,
    CompetitorExistsError,
    CompetitorLimitError,
    CompetitorNotFoundError,
    InvalidHostError,
    InvalidIfMatchError,
    InvalidModelOutputError,
    ProfileConflictError,
    ProfileVersionConflictError,
    RecommendationAlreadyDecidedError,
    RecommendationNotFoundError,
    RefreshRateLimitedError,
    UnknownMetricError,
)
from src.modules.brand_brain.infrastructure.internal_metrics import PgInternalMetricsReader
from src.modules.brand_brain.infrastructure.postgres_repository import (
    PgCostLogWriter,
    PostgresBrandBrainRepository,
)
from src.modules.brand_brain.public_fetch import PublicPageFetcher
from src.modules.brand_brain.schemas import (
    CompetitorCreate,
    CompetitorPublic,
    CompetitorsPage,
    ContentPlanItemPublic,
    ContentPlanPublic,
    CostDayPublic,
    CostReport,
    DiagnosisPointPublic,
    DiagnosisPublic,
    EvidencePage,
    EvidenceUsedPublic,
    ProfilePublic,
    ProfileUpdate,
    RecommendationPublic,
    RecommendationsPage,
    RefreshResult,
    RevisionPublic,
    RevisionsPage,
    evidence_to_public,
)
from src.modules.brand_brain.service import (
    BrandBrainService,
    CompetitorRefresher,
    EvidenceCollector,
    GraphPageSource,
    InternalMetricsReader,
    PublicPageSource,
)
from src.modules.content.router import get_content_service
from src.modules.content.service import ContentService
from src.modules.social_connect.meta_client import MetaGraphClient
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


def get_graph_page_source(
    settings: Annotated[Settings, Depends(get_settings)], http_client: HttpClient
) -> GraphPageSource | None:
    """B-2: the Graph app token (``app_id|app_secret``) — ``None`` when Meta is not
    configured, so a competitor refresh records ``unavailable`` instead of failing."""
    if not settings.meta_app_id or not settings.meta_app_secret.get_secret_value():
        return None
    return MetaGraphClient(settings=settings, http_client=http_client)


def get_public_page_source(http_client: HttpClient) -> PublicPageSource:
    return PublicPageFetcher(http_client)


def get_cost_log_writer() -> CostLogWriter:
    return PgCostLogWriter()


Repo = Annotated[BrandBrainRepository, Depends(get_brain_repository)]
MetricsReader = Annotated[InternalMetricsReader, Depends(get_internal_metrics_reader)]
Resolver = Annotated[BrandAccessResolver, Depends(get_brand_access_resolver)]
Limiter = Annotated[RateLimiter, Depends(get_rate_limiter)]
GraphSource = Annotated[GraphPageSource | None, Depends(get_graph_page_source)]
WebSource = Annotated[PublicPageSource, Depends(get_public_page_source)]
CostWriter = Annotated[CostLogWriter, Depends(get_cost_log_writer)]
LimitQuery = Annotated[int, Query(ge=1, le=MAX_LIST_LIMIT)]
CursorQuery = Annotated[str | None, Query(max_length=128)]
IfMatch = Annotated[str | None, Header(alias="If-Match")]
# B-2 (§2-0 هـ): the optional BYOK header. The value is never bound to a model, never logged
# and never stored — it lives in this request's locals and is handed to the provider only.
AIKeyHeader = Annotated[str | None, Header(alias="X-AI-Key", min_length=16, max_length=512)]


def get_brain_service(
    repo: Repo, reader: MetricsReader, limiter: Limiter, graph: GraphSource, web: WebSource
) -> BrandBrainService:
    return BrandBrainService(
        repo,
        limiter=limiter,
        collector=EvidenceCollector(repo, reader),
        refresher=CompetitorRefresher(repo, graph=graph, web=web),
    )


Service = Annotated[BrandBrainService, Depends(get_brain_service)]


class _NoPlatformGateway:
    """Placeholder used when a user key is present — the platform key is never touched."""

    name = "none"
    model = "none"

    async def complete(self, req: CompletionRequest) -> CompletionResult:  # pragma: no cover
        raise AIProviderNotConfiguredError()


_NO_PLATFORM: AIGateway = _NoPlatformGateway()


def build_brain_gateway(
    settings: Settings,
    *,
    platform: AIGateway,
    writer: CostLogWriter,
    user_key: str | None,
    http_client: object | None,
) -> AIGateway:
    """B-2: ``X-AI-Key`` ⇒ a per-request ``openai_compatible`` provider on the **user's** key
    (``credential_mode=user_key``, memory only); otherwise the platform gateway. Either way
    the call is wrapped by :class:`CostLoggingGateway`. **No fallback** from ``user_key`` to
    ``platform``: a failure is returned as is, with ``credential_mode=user_key``."""
    if user_key is None:
        inner = platform
    else:
        inner = HttpOpenAICompatibleProvider(
            base_url=settings.ai_provider_base_url,
            api_key=SecretStr(user_key),
            model=settings.ai_default_model,
            http_client=http_client,  # type: ignore[arg-type]
            timeout_seconds=settings.ai_http_timeout_seconds,
        )
    return CostLoggingGateway(
        inner, writer, price_table=PriceTable.from_json(settings.ai_price_table_json)
    )


def get_brain_gateway(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    writer: CostWriter,
    x_ai_key: AIKeyHeader = None,
) -> AIGateway:
    """``X-AI-Key`` present ⇒ the platform gateway is **not even built**; absent ⇒ the
    configured platform gateway (503 ``ai_provider_not_configured`` when none)."""
    http_client = getattr(request.app.state, "http_client", None)
    platform = _NO_PLATFORM if x_ai_key is not None else get_ai_gateway(request, settings)
    return build_brain_gateway(
        settings, platform=platform, writer=writer, user_key=x_ai_key, http_client=http_client
    )


BrainGateway = Annotated[AIGateway, Depends(get_brain_gateway)]
BudgetReader = Annotated[BudgetUsageReader, Depends(get_budget_usage_reader)]
Content = Annotated[ContentService, Depends(get_content_service)]
RecommendationStatusQuery = Annotated[
    Literal["proposed", "accepted", "dismissed", "materialized"] | None, Query(alias="status")
]


def get_brain_advisor(
    repo: Repo,
    gateway: BrainGateway,
    limiter: Limiter,
    budget_reader: BudgetReader,
    content: Content,
    settings: Annotated[Settings, Depends(get_settings)],
) -> BrandAdvisor:
    """B-3: the same request session for recommendations + content drafts; the gateway is
    the B-2 cost-logging one (platform or ``X-AI-Key``); the budget is the shared A-4 one."""
    return BrandAdvisor(
        repo,
        gateway=gateway,
        limiter=limiter,
        budget=DailyBudget(settings.ai_daily_token_budget_per_brand),
        budget_reader=budget_reader,
        content=content,
    )


Advisor = Annotated[BrandAdvisor, Depends(get_brain_advisor)]


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
    elif isinstance(exc, RefreshRateLimitedError | BrainRateLimitedError):
        code = status.HTTP_429_TOO_MANY_REQUESTS
        headers = {"Retry-After": str(int(exc.retry_after_seconds))}
    elif isinstance(exc, BrainBudgetExceededError):
        code = status.HTTP_429_TOO_MANY_REQUESTS
        detail.update(budget=exc.budget, used=exc.used, resets_in_seconds=exc.resets_in_seconds)
        headers = {"Retry-After": str(int(exc.resets_in_seconds))}
    elif isinstance(exc, InvalidModelOutputError):
        code = status.HTTP_502_BAD_GATEWAY
        detail["category"] = "invalid_output"
    elif isinstance(exc, RecommendationNotFoundError):
        code = status.HTTP_404_NOT_FOUND
    elif isinstance(exc, RecommendationAlreadyDecidedError):
        code = status.HTTP_409_CONFLICT
        detail["status"] = exc.status
    elif isinstance(exc, ProfileConflictError):
        code = status.HTTP_409_CONFLICT  # A-6: retry — the profile was created concurrently
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
    summary="Profile revisions (newest first) — all roles (VIEWER without banned_phrases)",
)
async def list_revisions(
    brand_id: UUID, actor: CurrentUser, resolver: Resolver, service: Service
) -> RevisionsPage:
    role = await _authorise(resolver, actor.tenant_id, brand_id, READ_ROLES)
    private = role in PRIVATE_ROLES  # A-5: same projection as GET /profile
    return RevisionsPage(
        items=[
            RevisionPublic.from_domain(r, private=private)
            for r in await service.list_revisions(brand_id)
        ]
    )


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


@router.post(
    f"{BRAIN_PREFIX}/competitors/{{competitor_id}}/refresh",
    response_model=RefreshResult,
    summary="Refresh a competitor from legal public sources (10/hour/brand) — OWNER/MANAGER",
    responses={
        404: {"description": "competitor_not_found"},
        429: {"description": "refresh_rate_limited (Retry-After)"},
    },
)
async def refresh_competitor(
    brand_id: UUID,
    competitor_id: UUID,
    actor: CurrentUser,
    resolver: Resolver,
    service: Service,
) -> RefreshResult:
    """B-2 (D-074): Graph app token (public fields, no ``/posts``) + the allow-listed public
    page (robots first, 512 KiB, GET only) ⇒ ``estimated`` evidence; refusals are recorded
    as ``{"unavailable": reason}``; ``competitor_refreshed_at`` is bumped."""
    await _authorise(resolver, actor.tenant_id, brand_id, CONNECTION_WRITE_ROLES)
    try:
        outcome = await service.refresh_competitor(brand_id, competitor_id)
    except BrandBrainError as exc:
        raise to_http(exc) from exc
    rows = outcome.rows
    observed = rows[0].observed_at if rows else datetime.now(tz=UTC)
    return RefreshResult(
        collected=len(rows),
        metrics=[r.metric for r in rows],
        observed_at=observed,
        status=outcome.status,  # type: ignore[arg-type]
    )


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
        prompt_tokens=sum(d.prompt_tokens for d in summary.days),
        completion_tokens=sum(d.completion_tokens for d in summary.days),
        total_estimated_cost_usd=sum(costs) if costs else None,
    )


# ------------------------------------------------------------ brain (B-3) --
def _credential_mode(x_ai_key: str | None) -> str:
    return "user_key" if x_ai_key is not None else "platform"


def _ai_to_http(exc: AIError) -> HTTPException:
    """Same table as ``content`` (D-067 twelve categories ⇒ HTTP)."""
    from src.modules.content.router import _ai_error_to_http

    return _ai_error_to_http(exc)


@router.post(
    f"{BRAIN_PREFIX}/diagnose",
    response_model=DiagnosisPublic,
    summary="Diagnose the brand from graded evidence ⇒ proposed recommendations (5/hour)",
    responses={
        429: {"description": "brain_rate_limited / generation_budget_exceeded (Retry-After)"},
        502: {"description": "ai_invalid_output / ai_upstream_unavailable"},
        503: {"description": "ai_provider_not_configured"},
    },
)
async def diagnose(
    brand_id: UUID,
    actor: CurrentUser,
    resolver: Resolver,
    advisor: Advisor,
    x_ai_key: AIKeyHeader = None,
) -> DiagnosisPublic:
    """B-3: ≤60 evidence rows + profile ⇒ ``brand_diagnosis_v1`` ⇒ ≤10 ``proposed``
    recommendations. Grade tags are appended by the **server** per cited evidence row; a
    recommendation without a valid evidence id is dropped (``recommendations_dropped``)."""
    role = await _authorise(resolver, actor.tenant_id, brand_id, CONNECTION_WRITE_ROLES)
    try:
        result = await advisor.diagnose(
            brand_id, tenant_id=actor.tenant_id, credential_mode=_credential_mode(x_ai_key)
        )
    except BrandBrainError as exc:
        raise to_http(exc) from exc
    except AIError as exc:
        raise _ai_to_http(exc) from exc
    private = role in PRIVATE_ROLES
    return DiagnosisPublic(
        summary=result.summary,
        strengths=[DiagnosisPointPublic(text=t, evidence_ids=ids) for t, ids in result.strengths],
        gaps=[DiagnosisPointPublic(text=t, evidence_ids=ids) for t, ids in result.gaps],
        evidence_used=[
            EvidenceUsedPublic(id=e.id, kind=e.evidence_kind) for e in result.evidence_used
        ],
        recommendations=[
            RecommendationPublic.from_domain(r, private=private) for r in result.recommendations
        ],
        recommendations_dropped=result.recommendations_dropped,
        dropped=result.recommendations_dropped,
        prompt_version=result.prompt_version,
    )


@router.post(
    f"{BRAIN_PREFIX}/content-plan",
    response_model=ContentPlanPublic,
    summary="Plan ≤7 posts ⇒ content_items(status=draft) under approval (5/hour)",
    responses={
        429: {"description": "brain_rate_limited / generation_budget_exceeded (Retry-After)"},
        502: {"description": "ai_invalid_output / ai_upstream_unavailable"},
    },
)
async def content_plan(
    brand_id: UUID,
    actor: CurrentUser,
    resolver: Resolver,
    advisor: Advisor,
    x_ai_key: AIKeyHeader = None,
) -> ContentPlanPublic:
    """B-3: ``content_plan_v1`` ⇒ every item is a ``draft`` with
    ``provenance.kind="ai_generated"`` + ``provenance.recommendation_id`` — never
    ``pending_approval``, zero approval events."""
    await _authorise(resolver, actor.tenant_id, brand_id, CONNECTION_WRITE_ROLES)
    try:
        result = await advisor.content_plan(
            brand_id,
            tenant_id=actor.tenant_id,
            actor_user_id=actor.user_id,
            credential_mode=_credential_mode(x_ai_key),
        )
    except BrandBrainError as exc:
        raise to_http(exc) from exc
    except AIError as exc:
        raise _ai_to_http(exc) from exc
    return ContentPlanPublic(
        items=[
            ContentPlanItemPublic(
                recommendation_id=rec.id,
                item_id=item.id,
                channel=item.channel.value,
                title=rec.title,
                status=item.status.value,
            )
            for rec, item in result.items
        ],
        items_dropped=result.items_dropped,
        prompt_version=result.prompt_version,
    )


@router.get(
    f"{BRAIN_PREFIX}/recommendations",
    response_model=RecommendationsPage,
    summary="Recommendations, newest first — all roles (VIEWER: public provenance only)",
)
async def list_recommendations(
    brand_id: UUID,
    actor: CurrentUser,
    resolver: Resolver,
    advisor: Advisor,
    status_filter: RecommendationStatusQuery = None,
    limit: LimitQuery = 50,
) -> RecommendationsPage:
    role = await _authorise(resolver, actor.tenant_id, brand_id, READ_ROLES)
    rows = await advisor.list_recommendations(brand_id, status=status_filter, limit=limit)
    private = role in PRIVATE_ROLES
    return RecommendationsPage(
        items=[RecommendationPublic.from_domain(r, private=private) for r in rows], limit=limit
    )


@router.post(
    f"{BRAIN_PREFIX}/recommendations/{{recommendation_id}}/accept",
    response_model=RecommendationPublic,
    summary="Accept a proposed recommendation — OWNER/MANAGER, fresh session",
    responses={404: {"description": "recommendation_not_found"}, 409: {"description": "decided"}},
)
async def accept_recommendation(
    brand_id: UUID,
    recommendation_id: UUID,
    actor: CurrentUserFresh,
    resolver: Resolver,
    advisor: Advisor,
) -> RecommendationPublic:
    """``reply_template`` ⇒ stored in ``brand_profiles.tone.reply_templates[]`` (≤10) **only
    now**; every decision writes a ``learning_signals`` row (no text)."""
    await _authorise(resolver, actor.tenant_id, brand_id, CONNECTION_WRITE_ROLES)
    try:
        decided = await advisor.accept(brand_id, recommendation_id, actor_user_id=actor.user_id)
    except BrandBrainError as exc:
        raise to_http(exc) from exc
    return RecommendationPublic.from_domain(decided.recommendation, private=True)


@router.post(
    f"{BRAIN_PREFIX}/recommendations/{{recommendation_id}}/dismiss",
    response_model=RecommendationPublic,
    summary="Dismiss a proposed recommendation — OWNER/MANAGER, fresh session",
    responses={404: {"description": "recommendation_not_found"}, 409: {"description": "decided"}},
)
async def dismiss_recommendation(
    brand_id: UUID,
    recommendation_id: UUID,
    actor: CurrentUserFresh,
    resolver: Resolver,
    advisor: Advisor,
) -> RecommendationPublic:
    await _authorise(resolver, actor.tenant_id, brand_id, CONNECTION_WRITE_ROLES)
    try:
        decided = await advisor.dismiss(brand_id, recommendation_id, actor_user_id=actor.user_id)
    except BrandBrainError as exc:
        raise to_http(exc) from exc
    return RecommendationPublic.from_domain(decided.recommendation, private=True)
