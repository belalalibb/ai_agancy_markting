"""``x`` — **structural** definition (``not_configured``; documentary values from the public
X API v2 docs, to be reviewed at activation). No client, no network."""

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
    return StructuralPublisher(Channel.X)


X = ChannelDefinition(
    channel=Channel.X,
    display_name="X",
    provider="x",
    activation_status=ActivationStatus.NOT_CONFIGURED,
    capabilities=ChannelCapabilities(
        text_limit=280,
        requires_media=False,
        max_media=1,
        media_kinds=frozenset({"image"}),
        supports_link=True,
        supports_scheduling=True,
    ),
    required_scopes=("tweet.write", "users.read", "offline.access"),
    activation_requirements=(
        ActivationRequirement(
            key="developer_account",
            title="X developer account on a paid tier with write access",
            owner=RequirementOwner.PLATFORM_OWNER,
            status=RequirementStatus.NOT_STARTED,
            note="Posting requires a paid API tier (owner decision: cost).",
        ),
        ActivationRequirement(
            key="app_oauth2",
            title="App configured for OAuth 2.0 with PKCE (user context)",
            owner=RequirementOwner.PLATFORM_OWNER,
            status=RequirementStatus.NOT_STARTED,
            note="Client id/secret as environment variables.",
        ),
        ActivationRequirement(
            key="brand_oauth",
            title="Brand authorises the app (tweet.write users.read offline.access)",
            owner=RequirementOwner.BRAND_OWNER,
            status=RequirementStatus.NOT_STARTED,
            note="Per brand; needs the connection flow of a later sprint.",
        ),
    ),
    media_rule=MediaRule(required=False, kind="image", https_image=False, max_items=1),
    publisher_factory=_structural,
)
