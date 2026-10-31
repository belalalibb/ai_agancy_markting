"""``StructuralPublisher`` — the adapter of a channel that is **not activated** (SPRINT-08).

It implements the :class:`Publisher` port so the scheduler can resolve *any* registry
channel, but it never performs a call: no HTTP client, no token lookup, no provider import.
``publish()`` returns ``PublishOutcome.failure(ErrorClass.CHANNEL_NOT_ACTIVATED)`` at once
(a **final** failure — the class is not retryable) and logs one ``WARNING`` without any
post text. In practice the content service already refuses ``schedule`` for such channels
(422 ``channel_not_activated``); this is the second guard for a job that pre-dates a
channel being disabled.
"""

from __future__ import annotations

import logging

from src.modules.content.domain.models import Channel, ContentRevision
from src.modules.publishing.domain.models import ErrorClass, PublishJob, PublishOutcome
from src.modules.publishing.domain.publisher import PageTokenProvider

logger = logging.getLogger(__name__)


class StructuralPublisher:
    def __init__(self, channel: Channel) -> None:
        self._channel = channel

    @property
    def channel(self) -> Channel:
        return self._channel

    async def publish(
        self,
        job: PublishJob,
        revision: ContentRevision,
        page_token_provider: PageTokenProvider,
        *,
        last_creation_id: str | None = None,
    ) -> PublishOutcome:
        del revision, page_token_provider, last_creation_id  # never touched (B2-U1)
        logger.warning(
            "publish: channel not activated channel=%s job=%s item=%s",
            self._channel.value,
            job.id,
            job.item_id,
        )
        return PublishOutcome.failure(ErrorClass.CHANNEL_NOT_ACTIVATED)
