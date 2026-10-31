"""``Publisher`` port (SPRINT-07 B5).

The scheduler (B6) owns *all* persistence; a publisher only talks to the provider and
reports a :class:`PublishOutcome` — it never raises for provider failures and never sees
the database session. Page tokens are resolved through a :class:`PageTokenProvider` so the
plaintext token lives only inside ``publish()`` (deleted in ``finally``).
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.modules.content.domain.models import ContentRevision
from src.modules.publishing.domain.models import PublishJob, PublishOutcome


class ResolvedPageTarget(BaseModel):
    """A *selected*, token-bearing Page of an ``active`` connection (B5)."""

    model_config = ConfigDict(frozen=True)

    connection_id: UUID
    page_id: str
    ig_user_id: str | None = None
    page_token: str = Field(repr=False)


class PageTokenProvider(Protocol):
    async def resolve(self, brand_id: UUID, page_id: str) -> ResolvedPageTarget | None:
        """Return the decrypted Page token for ``page_id`` or ``None`` when no *active*
        connection of the brand has that Page selected (⇒ ``connection_unavailable``)."""
        ...


class Publisher(Protocol):
    async def publish(
        self,
        job: PublishJob,
        revision: ContentRevision,
        page_token_provider: PageTokenProvider,
        *,
        last_creation_id: str | None = None,
    ) -> PublishOutcome:
        """Publish ``revision`` to ``job.target_page_id`` on ``job.channel``.

        ``last_creation_id`` is the Instagram container saved by a previous attempt
        (``publish_attempts.external_post_id``): when given, step 1 is skipped and only
        ``media_publish`` runs (B4).
        """
        ...
