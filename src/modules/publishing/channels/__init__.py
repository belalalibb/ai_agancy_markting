"""``publishing.channels`` — channel registry + per-channel definitions (SPRINT-08 B-1, M5).

:func:`get_registry` builds the :class:`ChannelRegistry` once from the five definitions.
Meta channels are *active* and wrap the SPRINT-07 ``MetaPublisher``; LinkedIn / X / TikTok
are *structural* (``not_configured``): declared, listable, draftable, but ``schedule`` is
refused with 422 ``channel_not_activated`` and their publisher never performs a call.
"""

from __future__ import annotations

from functools import lru_cache

from src.modules.publishing.channels.definition import (
    ActivationRequirement,
    ActivationStatus,
    ChannelCapabilities,
    ChannelDefinition,
    MediaRule,
    MediaRuleError,
    PublisherDeps,
    RequirementOwner,
    RequirementStatus,
)
from src.modules.publishing.channels.registry import (
    ChannelRegistry,
    UnknownChannelError,
    missing_publish_scopes,
)


@lru_cache(maxsize=1)
def get_registry() -> ChannelRegistry:
    """The process-wide registry (built once; definitions are frozen)."""
    from src.modules.publishing.channels.linkedin import LINKEDIN
    from src.modules.publishing.channels.meta_facebook import FACEBOOK_PAGE
    from src.modules.publishing.channels.meta_instagram import INSTAGRAM
    from src.modules.publishing.channels.tiktok import TIKTOK
    from src.modules.publishing.channels.x import X

    return ChannelRegistry((FACEBOOK_PAGE, INSTAGRAM, LINKEDIN, X, TIKTOK))


__all__ = [
    "ActivationRequirement",
    "ActivationStatus",
    "ChannelCapabilities",
    "ChannelDefinition",
    "ChannelRegistry",
    "MediaRule",
    "MediaRuleError",
    "PublisherDeps",
    "RequirementOwner",
    "RequirementStatus",
    "UnknownChannelError",
    "get_registry",
    "missing_publish_scopes",
]
