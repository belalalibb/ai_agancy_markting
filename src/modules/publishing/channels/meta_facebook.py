"""``facebook_page`` definition — Meta Graph, **active** (SPRINT-07 behaviour, unchanged)."""

from __future__ import annotations

from src.modules.content.domain.models import Channel
from src.modules.publishing.channels.definition import (
    ActivationRequirement,
    ActivationStatus,
    ChannelCapabilities,
    ChannelDefinition,
    MediaRule,
    PublisherDeps,
    RequirementOwner,
    RequirementStatus,
)
from src.modules.publishing.domain.publisher import Publisher

# Shared by facebook_page and instagram (one Meta developer app, one App Review).
META_ACTIVATION_REQUIREMENTS: tuple[ActivationRequirement, ...] = (
    ActivationRequirement(
        key="developer_app",
        title="Meta developer app created (app credentials in settings)",
        owner=RequirementOwner.PLATFORM_OWNER,
        status=RequirementStatus.DONE,
        note="SPRINT-02 onboarding; credentials live only in environment variables.",
    ),
    ActivationRequirement(
        key="app_review_publishing",
        title="Meta App Review for pages_manage_posts / instagram_content_publish",
        owner=RequirementOwner.PLATFORM_OWNER,
        status=RequirementStatus.PENDING,
        note="Owner item: until approved, publishing works for app roles/test pages only.",
    ),
    ActivationRequirement(
        key="brand_oauth",
        title="Brand connects its Meta account and selects the target Page",
        owner=RequirementOwner.BRAND_OWNER,
        status=RequirementStatus.NOT_STARTED,
        note="Per brand, via /social-connections OAuth; verified scopes required to schedule.",
    ),
)


def build_meta_publisher(deps: PublisherDeps) -> Publisher:
    """``MetaPublisher(MetaGraphClient(settings, http_client))`` — the SPRINT-07 wiring.
    Imported lazily so that importing the registry never pulls the Graph client."""
    from src.modules.publishing.infrastructure.meta_publisher import MetaPublisher
    from src.modules.social_connect.meta_client import MetaGraphClient

    return MetaPublisher(MetaGraphClient(deps.settings, deps.http_client))


FACEBOOK_PAGE = ChannelDefinition(
    channel=Channel.FACEBOOK_PAGE,
    display_name="Facebook Page",
    provider="meta",
    activation_status=ActivationStatus.ACTIVE,
    capabilities=ChannelCapabilities(
        text_limit=5000,
        requires_media=False,
        max_media=1,
        media_kinds=frozenset({"image"}),
        supports_link=True,
        supports_scheduling=True,
    ),
    required_scopes=("pages_manage_posts",),
    activation_requirements=META_ACTIVATION_REQUIREMENTS,
    # SPRINT-07: at most one optional image; no https/suffix rule on Facebook.
    media_rule=MediaRule(required=False, kind="image", https_image=False, max_items=1),
    publisher_factory=build_meta_publisher,
)
