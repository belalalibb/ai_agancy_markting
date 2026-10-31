"""SPRINT-08 B-1 — channel registry + definitions (B1-U1..B1-U5) and, in B-2, the structural
adapter / banned-api guards (B2-U1, B2-U5). No database, no network."""

from __future__ import annotations

import re
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import get_args
from uuid import uuid4

import pytest
from src.modules.content.domain.models import Channel, ContentRevision
from src.modules.content.schemas import ChannelLiteral
from src.modules.publishing.channels import (
    ActivationStatus,
    MediaRuleError,
    RequirementStatus,
    UnknownChannelError,
    get_registry,
)
from src.modules.publishing.channels.structural import StructuralPublisher
from src.modules.publishing.domain.models import (
    RETRYABLE_ERROR_CLASSES,
    AttemptOutcome,
    ErrorClass,
    PublishJob,
)

ROOT = Path(__file__).resolve().parents[3]
CONTENT_DIR = ROOT / "src" / "modules" / "content"
CHANNELS_DIR = ROOT / "src" / "modules" / "publishing" / "channels"
FORBIDDEN_IN_CONTENT = ("CHANNEL_LIMITS", "REQUIRED_PUBLISH_SCOPES", "instagram_image_url")
HTTPS_LITERAL = "https:" + "//"  # built so this file itself never contains the literal


# ---------------------------------------------------------------------- B1-U1 ---
def test_registry_has_exactly_five_channels_and_meta_only_active() -> None:
    registry = get_registry()
    assert {d.channel for d in registry.all()} == {
        Channel.FACEBOOK_PAGE,
        Channel.INSTAGRAM,
        Channel.LINKEDIN,
        Channel.X,
        Channel.TIKTOK,
    }
    assert len(registry) == 5
    assert {d.channel for d in registry.active()} == {Channel.FACEBOOK_PAGE, Channel.INSTAGRAM}
    assert {d.channel for d in registry.activatable()} == {
        Channel.LINKEDIN,
        Channel.X,
        Channel.TIKTOK,
    }
    for definition in registry.activatable():
        assert definition.activation_status is ActivationStatus.NOT_CONFIGURED
    assert registry.get("facebook_page").provider == registry.get("instagram").provider == "meta"
    assert registry.get(Channel.X).provider == "x"
    assert get_registry() is registry  # built once
    with pytest.raises(UnknownChannelError):
        registry.get("myspace")
    with pytest.raises(KeyError):
        registry["threads"]  # type: ignore[index]


# ---------------------------------------------------------------------- B1-U2 ---
def test_channel_literal_derived_from_enum() -> None:
    assert set(get_args(ChannelLiteral)) == {c.value for c in Channel}
    assert len(get_args(ChannelLiteral)) == 5


# ---------------------------------------------------------------------- B1-U3 ---
def test_capabilities_frozen_and_documented() -> None:
    registry = get_registry()
    expected = {
        Channel.FACEBOOK_PAGE: (5000, False, 1, {"image"}, True, True),
        Channel.INSTAGRAM: (2200, True, 1, {"image"}, False, True),
        Channel.LINKEDIN: (3000, False, 1, {"image"}, True, True),
        Channel.X: (280, False, 1, {"image"}, True, True),
        Channel.TIKTOK: (2200, True, 1, {"video"}, False, True),
    }
    for channel, (limit, requires, max_media, kinds, link, sched) in expected.items():
        caps = registry.get(channel).capabilities
        assert (
            caps.text_limit,
            caps.requires_media,
            caps.max_media,
            set(caps.media_kinds),
            caps.supports_link,
            caps.supports_scheduling,
        ) == (limit, requires, max_media, kinds, link, sched)
        assert registry.get(channel).limits == limit
        with pytest.raises(Exception, match="frozen"):
            caps.text_limit = 1  # type: ignore[misc]
        with pytest.raises(Exception, match="frozen"):
            registry.get(channel).display_name = "x"  # type: ignore[misc]

    assert registry.get(Channel.FACEBOOK_PAGE).required_scopes == ("pages_manage_posts",)
    assert registry.get(Channel.INSTAGRAM).required_scopes == (
        "pages_manage_posts",
        "instagram_content_publish",
    )
    assert registry.get(Channel.LINKEDIN).required_scopes == ("w_member_social",)
    assert registry.get(Channel.X).required_scopes == (
        "tweet.write",
        "users.read",
        "offline.access",
    )
    assert registry.get(Channel.TIKTOK).required_scopes == ("video.publish",)

    for definition in registry.activatable():
        reqs = definition.activation_requirements
        assert len(reqs) >= 3
        assert all(r.status is not RequirementStatus.DONE for r in reqs)
        assert {r.owner.value for r in reqs} == {"platform_owner", "brand_owner"}
        assert definition.incomplete_requirements() == reqs
    for channel in (Channel.FACEBOOK_PAGE, Channel.INSTAGRAM):
        by_key = {r.key: r for r in registry.get(channel).activation_requirements}
        assert by_key["app_review_publishing"].status is RequirementStatus.PENDING
        assert by_key["developer_app"].status is RequirementStatus.DONE
        assert "developer_app" not in {
            r.key for r in registry.get(channel).incomplete_requirements()
        }
    assert {r.key for r in registry.get(Channel.X).activation_requirements} == {
        "developer_account",
        "app_oauth2",
        "brand_oauth",
    }
    assert {r.key for r in registry.get(Channel.LINKEDIN).activation_requirements} == {
        "developer_app",
        "marketing_api_access",
        "brand_oauth",
    }
    assert {r.key for r in registry.get(Channel.TIKTOK).activation_requirements} == {
        "developer_app",
        "content_posting_api_audit",
        "brand_oauth",
    }


# ---------------------------------------------------------------------- B1-U4 ---
_IMG = HTTPS_LITERAL + "cdn.example.com/media/offer"


@pytest.mark.parametrize(
    ("channel", "media_refs", "ok"),
    [
        # instagram — SPRINT-07 B2 (ب) verbatim
        (Channel.INSTAGRAM, [], False),
        (Channel.INSTAGRAM, [{"url": "http://cdn.example.com/a.jpg"}], False),
        (Channel.INSTAGRAM, [{"url": _IMG + ".gif"}], False),
        (Channel.INSTAGRAM, [{"url": _IMG + ".jpg?x=1&y=2"}], True),
        (Channel.INSTAGRAM, [{"url": _IMG + ".PNG#frag", "kind": "image"}], True),
        (Channel.INSTAGRAM, [{"url": _IMG + ".jpeg"}], True),
        (Channel.INSTAGRAM, [{"url": _IMG + ".mp4", "kind": "video"}], False),
        # facebook_page — optional single image, no suffix rule
        (Channel.FACEBOOK_PAGE, [], True),
        (Channel.FACEBOOK_PAGE, [{"url": "http://cdn.example.com/a.webp"}], True),
        (Channel.FACEBOOK_PAGE, [{"url": _IMG + ".jpg"}, {"url": _IMG + ".png"}], False),
        # structural (documentary)
        (Channel.TIKTOK, [{"url": _IMG + ".jpg"}], False),
        (Channel.TIKTOK, [], False),
        (Channel.TIKTOK, [{"url": _IMG + ".mp4", "kind": "video"}], True),
        (Channel.LINKEDIN, [], True),
        (Channel.X, [{"url": _IMG + ".png"}], True),
    ],
)
def test_validate_media_matches_sprint07_behaviour(
    channel: Channel, media_refs: list[dict[str, str]], ok: bool
) -> None:
    definition = get_registry().get(channel)
    if ok:
        assert definition.validate_media(media_refs) is None
    else:
        with pytest.raises(MediaRuleError) as exc_info:
            definition.validate_media(media_refs)
        assert re.fullmatch(r"[a-z_]+", exc_info.value.reason)


# ---------------------------------------------------------------------- B1-U5 ---
def test_no_content_module_knows_channel_constants() -> None:
    offenders: list[str] = []
    for path in sorted(CONTENT_DIR.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for needle in (*FORBIDDEN_IN_CONTENT, HTTPS_LITERAL):
            if needle in text:
                offenders.append(f"{path.relative_to(ROOT)}: {needle}")
    assert offenders == []
    src_offenders = [
        f"{p.relative_to(ROOT)}: {n}"
        for p in sorted((ROOT / "src").rglob("*.py"))
        for n in FORBIDDEN_IN_CONTENT
        if n in p.read_text(encoding="utf-8")
    ]
    assert src_offenders == []


def test_channel_not_activated_is_final_not_retryable() -> None:
    assert ErrorClass.CHANNEL_NOT_ACTIVATED.value == "channel_not_activated"
    assert ErrorClass.CHANNEL_NOT_ACTIVATED not in RETRYABLE_ERROR_CLASSES


# ---------------------------------------------------------------------- B2-U1 ---
class _ExplodingTokenProvider:
    async def resolve(self, brand_id: object, page_id: str) -> None:
        raise AssertionError("StructuralPublisher must never resolve a page token")


@pytest.mark.anyio
async def test_structural_publisher_never_calls_token_provider_or_network(
    caplog: pytest.LogCaptureFixture,
) -> None:
    job = PublishJob(
        item_id=uuid4(),
        brand_id=uuid4(),
        revision_id=uuid4(),
        channel="linkedin",
        target_page_id="1001",
        run_at=datetime.now(tz=UTC),
        scheduled_by_tenant_id=uuid4(),
        scheduled_by_user_id=uuid4(),
    )
    revision = ContentRevision(
        item_id=job.item_id,
        revision_no=1,
        body="hello",
        provenance={"kind": "human"},
        created_by_tenant_id=job.scheduled_by_tenant_id,
    )
    publisher = StructuralPublisher(Channel.LINKEDIN)
    with caplog.at_level("WARNING", logger="src.modules.publishing.channels.structural"):
        outcome = await publisher.publish(
            job, revision, _ExplodingTokenProvider(), last_creation_id="ignored"
        )
    assert outcome.kind is AttemptOutcome.PERMANENT_FAILURE
    assert outcome.error_class is ErrorClass.CHANNEL_NOT_ACTIVATED
    assert outcome.external_post_id is None and outcome.connection_id_to_mark is None
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    assert warnings[0].getMessage().startswith("publish: channel not activated channel=linkedin")
    assert "hello" not in warnings[0].getMessage()
    # §2-0 أ / §3-4: structural adapters own no client — no ``httpx`` import, no URL literal
    # outside the Meta definitions; Meta definitions never import httpx either (§3-5).
    for path in sorted(CHANNELS_DIR.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        assert "import httpx" not in text, path.name
        if not path.name.startswith("meta_"):
            assert HTTPS_LITERAL not in text, path.name


# ---------------------------------------------------------------------- B2-U5 ---
def test_banned_api_extended() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    banned = pyproject["tool"]["ruff"]["lint"]["flake8-tidy-imports"]["banned-api"]
    for module in ("linkedin", "tweepy", "tiktok", "facebook_business", "requests"):
        assert module in banned, module
    assert banned["requests"]["msg"] == "Use src.core.http shared client"
    assert banned["httpx"]["msg"] == "Use AIGateway (D-068)"
