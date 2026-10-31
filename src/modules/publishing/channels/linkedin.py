"""``linkedin`` — **structural** definition (``not_configured``; documentary values from the
public LinkedIn Marketing API docs, to be reviewed at activation). No client, no network."""

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
    return StructuralPublisher(Channel.LINKEDIN)


LINKEDIN = ChannelDefinition(
    channel=Channel.LINKEDIN,
    display_name="LinkedIn",
    provider="linkedin",
    activation_status=ActivationStatus.NOT_CONFIGURED,
    capabilities=ChannelCapabilities(
        text_limit=3000,
        requires_media=False,
        max_media=1,
        media_kinds=frozenset({"image"}),
        supports_link=True,
        supports_scheduling=True,
    ),
    required_scopes=("w_member_social",),
    activation_requirements=(
        ActivationRequirement(
            key="developer_app",
            title="LinkedIn developer app created",
            owner=RequirementOwner.PLATFORM_OWNER,
            status=RequirementStatus.NOT_STARTED,
            note="LinkedIn Developer Portal; app credentials live only in environment variables.",
        ),
        ActivationRequirement(
            key="marketing_api_access",
            title="Marketing Developer Platform access review",
            owner=RequirementOwner.PLATFORM_OWNER,
            status=RequirementStatus.NOT_STARTED,
            note="Review by LinkedIn; required before w_member_social can be granted.",
        ),
        ActivationRequirement(
            key="brand_oauth",
            title="Brand authorises the app (OAuth 2.0, 3-legged)",
            owner=RequirementOwner.BRAND_OWNER,
            status=RequirementStatus.NOT_STARTED,
            note="Per brand; needs the connection flow of a later sprint.",
        ),
    ),
    media_rule=MediaRule(required=False, kind="image", https_image=False, max_items=1),
    publisher_factory=_structural,
)
