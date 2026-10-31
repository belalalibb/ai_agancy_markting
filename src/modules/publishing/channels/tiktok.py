"""``tiktok`` — **structural** definition (``not_configured``; documentary values from the
public TikTok Content Posting API docs, to be reviewed at activation). No client, no network."""

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
from src.modules.publishing.channels.structural import StructuralPublisher
from src.modules.publishing.domain.publisher import Publisher


def _structural(_: PublisherDeps) -> Publisher:
    return StructuralPublisher(Channel.TIKTOK)


TIKTOK = ChannelDefinition(
    channel=Channel.TIKTOK,
    display_name="TikTok",
    provider="tiktok",
    activation_status=ActivationStatus.NOT_CONFIGURED,
    capabilities=ChannelCapabilities(
        text_limit=2200,
        requires_media=True,
        max_media=1,
        media_kinds=frozenset({"video"}),
        supports_link=False,
        supports_scheduling=True,
    ),
    required_scopes=("video.publish",),
    activation_requirements=(
        ActivationRequirement(
            key="developer_app",
            title="TikTok developer app created",
            owner=RequirementOwner.PLATFORM_OWNER,
            status=RequirementStatus.NOT_STARTED,
            note="TikTok for Developers; client key/secret as environment variables.",
        ),
        ActivationRequirement(
            key="content_posting_api_audit",
            title="Content Posting API audit (unaudited apps post private-only)",
            owner=RequirementOwner.PLATFORM_OWNER,
            status=RequirementStatus.NOT_STARTED,
            note="Audit by TikTok; required for public posts.",
        ),
        ActivationRequirement(
            key="brand_oauth",
            title="Brand authorises the app (video.publish)",
            owner=RequirementOwner.BRAND_OWNER,
            status=RequirementStatus.NOT_STARTED,
            note="Per brand; needs the connection flow of a later sprint.",
        ),
    ),
    # TikTok posts are video-first: exactly one ``kind=video`` ref is required.
    media_rule=MediaRule(required=True, kind="video", https_image=False, max_items=1),
    publisher_factory=_structural,
)
