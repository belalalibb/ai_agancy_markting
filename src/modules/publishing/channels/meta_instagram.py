"""``instagram`` definition — Meta Graph (shares ``MetaPublisher`` and the same
``PageTokenProvider`` with ``facebook_page``), **active**."""

from __future__ import annotations

from src.modules.content.domain.models import Channel
from src.modules.publishing.channels.definition import (
    ActivationStatus,
    ChannelCapabilities,
    ChannelDefinition,
    MediaRule,
)
from src.modules.publishing.channels.meta_facebook import (
    META_ACTIVATION_REQUIREMENTS,
    build_meta_publisher,
)

INSTAGRAM = ChannelDefinition(
    channel=Channel.INSTAGRAM,
    display_name="Instagram",
    provider="meta",
    activation_status=ActivationStatus.ACTIVE,
    capabilities=ChannelCapabilities(
        text_limit=2200,
        requires_media=True,
        max_media=1,
        media_kinds=frozenset({"image"}),
        supports_link=False,
        supports_scheduling=True,
    ),
    required_scopes=("pages_manage_posts", "instagram_content_publish"),
    activation_requirements=META_ACTIVATION_REQUIREMENTS,
    # SPRINT-07 B2 (ب): one mandatory https jpg/jpeg/png image.
    media_rule=MediaRule(required=True, kind="image", https_image=True, max_items=1),
    publisher_factory=build_meta_publisher,
)
