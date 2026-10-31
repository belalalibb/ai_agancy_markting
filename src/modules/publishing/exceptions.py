"""Domain exceptions of ``publishing`` (SPRINT-07 B1)."""

from __future__ import annotations


class PublishingError(Exception):
    """Base class."""


class ActiveJobExistsError(PublishingError):
    """The item already has a ``queued``/``leased`` publish job
    (``uq_publish_jobs_active_item``)."""

    def __init__(self) -> None:
        super().__init__("item already has an active publish job")
