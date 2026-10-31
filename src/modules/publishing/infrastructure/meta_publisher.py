"""``MetaPublisher`` — the Meta Graph implementation of the ``Publisher`` port (SPRINT-07 B5).

* Resolves the *selected* Page (with its encrypted token) of an **active** connection of the
  brand through :class:`PostgresPageTokenProvider`; any other state ⇒
  ``permanent_failure(connection_unavailable)`` (no Graph call).
* Facebook Page: ``POST /{page_id}/feed``. Instagram: two-step container flow; the
  ``creation_id`` of step 1 is returned in ``external_post_id`` even on a step-2 failure so a
  retry runs ``media_publish`` only (B4).
* Graph errors are classified by :func:`classify_graph_error` (B4 map) — never Meta's
  message text; ``token_invalid`` additionally asks the scheduler to mark the connection
  ``reauth_required`` *outside* the result transaction (``connection_id_to_mark``).
* The plaintext Page token is dropped in ``finally`` (``del``) — it never reaches logs.
"""

from __future__ import annotations

import logging
from typing import Final
from uuid import UUID

from src.core.security import TokenEncryptionService
from src.modules.content.domain.models import Channel, ContentRevision
from src.modules.publishing.domain.models import ErrorClass, PublishJob, PublishOutcome
from src.modules.publishing.domain.publisher import PageTokenProvider, ResolvedPageTarget
from src.modules.social_connect.domain.models import AccountStatus
from src.modules.social_connect.domain.repository import ConnectedAccountRepository
from src.modules.social_connect.exceptions import (
    MetaAmbiguousError,
    MetaGraphError,
    MetaNetworkError,
    MetaOAuthError,
    MetaPermissionError,
    MetaRateLimitError,
    MetaTransientError,
)
from src.modules.social_connect.meta_client import MetaGraphClient

logger = logging.getLogger(__name__)

_TOKEN_INVALID_CODES: Final[frozenset[int]] = frozenset({190, 102})
_RATE_LIMIT_CODES: Final[frozenset[int]] = frozenset({4, 17, 32, 613})
_TRANSIENT_CODES: Final[frozenset[int]] = frozenset({1, 2})
_PERMISSION_CODES: Final[frozenset[int]] = frozenset({10, *range(200, 300)})
_INVALID_REQUEST_CODE: Final[int] = 100


def classify_graph_error(exc: MetaGraphError) -> ErrorClass:
    """B4 error map (codes win over exception type; type is the fallback for code-less errors).

    190/102 ⇒ ``token_invalid``; 200–299/10 ⇒ ``permission_denied``; 4/17/32/613 ⇒
    ``rate_limited``; 1/2, HTTP 5xx, network ⇒ ``transient``; 100 ⇒ ``invalid_request``;
    anything else ⇒ ``unknown``.
    """
    code = exc.code
    if code in _TOKEN_INVALID_CODES:
        return ErrorClass.TOKEN_INVALID
    if code in _PERMISSION_CODES:
        return ErrorClass.PERMISSION_DENIED
    if code in _RATE_LIMIT_CODES:
        return ErrorClass.RATE_LIMITED
    if code in _TRANSIENT_CODES:
        return ErrorClass.TRANSIENT
    if code == _INVALID_REQUEST_CODE:
        return ErrorClass.INVALID_REQUEST
    if isinstance(exc, MetaNetworkError) or (
        exc.http_status is not None and exc.http_status >= 500
    ):
        return ErrorClass.TRANSIENT
    if code is not None:
        # A documented-but-unlisted Graph code (e.g. IG 9007 "media not ready") is *unknown*,
        # never token_invalid — that label marks the connection reauth_required (B6-I5).
        return ErrorClass.UNKNOWN
    # No code at all (HTTP 401/403/429 without a Graph body): fall back on the exception type.
    if isinstance(exc, MetaTransientError):
        return ErrorClass.TRANSIENT
    if isinstance(exc, MetaRateLimitError):
        return ErrorClass.RATE_LIMITED
    if isinstance(exc, MetaPermissionError):
        return ErrorClass.PERMISSION_DENIED
    if isinstance(exc, MetaOAuthError):
        return ErrorClass.TOKEN_INVALID
    return ErrorClass.UNKNOWN


class PostgresPageTokenProvider:
    """Resolves ``(brand_id, page_id)`` ⇒ decrypted Page token of an *active* connection."""

    def __init__(
        self, connections: ConnectedAccountRepository, encryption: TokenEncryptionService
    ) -> None:
        self._connections = connections
        self._encryption = encryption

    async def resolve(self, brand_id: UUID, page_id: str) -> ResolvedPageTarget | None:
        for account in await self._connections.list_by_brand(brand_id):
            for page in account.pages:
                if page.page_id != page_id or not page.is_selected:
                    continue
                if account.status is not AccountStatus.ACTIVE or not page.encrypted_page_token:
                    return None
                return ResolvedPageTarget(
                    connection_id=account.id,
                    page_id=page.page_id,
                    ig_user_id=page.instagram.ig_user_id if page.instagram else None,
                    page_token=self._encryption.decrypt_token(page.encrypted_page_token),
                )
        return None


class MetaPublisher:
    def __init__(self, meta_client: MetaGraphClient) -> None:
        self._client = meta_client

    async def publish(
        self,
        job: PublishJob,
        revision: ContentRevision,
        page_token_provider: PageTokenProvider,
        *,
        last_creation_id: str | None = None,
    ) -> PublishOutcome:
        target = await page_token_provider.resolve(job.brand_id, job.target_page_id)
        if target is None:
            return PublishOutcome.failure(ErrorClass.CONNECTION_UNAVAILABLE)
        page_token = target.page_token
        creation_id = last_creation_id
        try:
            if job.channel == Channel.INSTAGRAM:
                if target.ig_user_id is None:
                    return PublishOutcome.failure(ErrorClass.CONNECTION_UNAVAILABLE)
                image_url = _image_url(revision)
                if image_url is None:
                    return PublishOutcome.failure(ErrorClass.INVALID_REQUEST)
                if creation_id is None:
                    creation_id = await self._client.create_instagram_container(
                        page_token, target.ig_user_id, image_url, revision.body
                    )
                result = await self._client.publish_instagram_container(
                    page_token, target.ig_user_id, creation_id
                )
            else:
                result = await self._client.publish_page_post(
                    page_token, target.page_id, revision.body
                )
        except MetaAmbiguousError as exc:
            # D-070 (SPRINT-09 A-2): the POST was sent exactly once; Meta may have applied
            # it. Scheduler records attempt ``ambiguous`` + job ``failed`` — no auto-retry.
            logger.info(
                "publish provider ambiguous: job=%s code=%s http=%s",
                job.id,
                exc.code,
                exc.http_status,
            )
            return PublishOutcome.ambiguous(
                graph_error_code=exc.code,
                graph_error_subcode=exc.subcode,
                graph_trace_id=exc.fbtrace_id,
                external_post_id=creation_id,
            )
        except MetaGraphError as exc:
            error_class = classify_graph_error(exc)
            logger.info(
                "publish provider failure: job=%s class=%s code=%s subcode=%s",
                job.id,
                error_class,
                exc.code,
                exc.subcode,
            )
            return PublishOutcome.failure(
                error_class,
                graph_error_code=exc.code,
                graph_error_subcode=exc.subcode,
                graph_trace_id=exc.fbtrace_id,
                external_post_id=creation_id,
                connection_id_to_mark=(
                    target.connection_id if error_class is ErrorClass.TOKEN_INVALID else None
                ),
            )
        finally:
            del page_token
        return PublishOutcome.success(external_post_id=result.post_id, permalink=result.permalink)


def _image_url(revision: ContentRevision) -> str | None:
    for ref in revision.media_refs:
        url = ref.get("url")
        if isinstance(url, str) and url:
            return url
    return None
