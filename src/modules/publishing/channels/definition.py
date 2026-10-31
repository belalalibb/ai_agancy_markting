"""Channel definitions — the single description of *what a channel is* (SPRINT-08 B-1, M5).

A :class:`ChannelDefinition` is a frozen, declarative record: capabilities (text limit, media
rules), the publishing scopes a connection must prove, the **activation matrix** (what the
platform owner / brand owner still has to do before real publishing is possible) and the
factory that builds the channel's :class:`Publisher`. Nothing here talks to a network: the
definitions of non-activated channels are *documentary* (public platform docs) and are
reviewed when the channel is actually activated in a later sprint.

Design rules (directive §2-0):

* ``content/`` never knows channel constants — it asks ``registry.get(channel)``.
* ``activation_status`` is read from the definition, **not** from settings, in M5.
* A ``MediaRule`` failure is reported through :class:`MediaRuleError` with a stable
  ``reason`` code; the content service maps it to the SPRINT-07 HTTP error unchanged.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from src.modules.content.domain.models import Channel

if TYPE_CHECKING:  # pragma: no cover - typing only
    from src.core.config import Settings
    from src.modules.publishing.domain.publisher import Publisher

Provider = Literal["meta", "linkedin", "x", "tiktok"]
IMAGE_SUFFIXES: Final[tuple[str, ...]] = (".jpg", ".jpeg", ".png")


class ActivationStatus(StrEnum):
    ACTIVE = "active"
    NOT_CONFIGURED = "not_configured"


class RequirementOwner(StrEnum):
    PLATFORM_OWNER = "platform_owner"
    BRAND_OWNER = "brand_owner"


class RequirementStatus(StrEnum):
    DONE = "done"
    PENDING = "pending"
    NOT_STARTED = "not_started"


class ActivationRequirement(BaseModel):
    """One row of the activation matrix (who must do what before the channel goes live)."""

    model_config = ConfigDict(frozen=True)

    key: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=120)
    owner: RequirementOwner
    status: RequirementStatus
    note: str = Field(default="", max_length=400)


class ChannelCapabilities(BaseModel):
    model_config = ConfigDict(frozen=True)

    text_limit: int = Field(ge=1)
    requires_media: bool
    max_media: int = Field(ge=0)
    media_kinds: frozenset[str]
    supports_link: bool
    supports_scheduling: bool


class MediaRuleError(ValueError):
    """``validate_media`` rejection; ``reason`` is a stable snake_case code."""

    def __init__(self, reason: str, message: str | None = None) -> None:
        self.reason = reason
        super().__init__(message or reason)


class MediaRule:
    """Per-channel media validation over ``revision.media_refs`` (list of ``{url, kind}``).

    * ``required`` — at least one ref must be present (Instagram / TikTok).
    * ``kind`` — every ref must carry that ``kind`` (``image`` default, ``video`` for TikTok).
    * ``https_image`` — ``url`` must use the ``https`` scheme and end in ``jpg|jpeg|png``
      (ignoring query/fragment) — the SPRINT-07 Instagram image rule, verbatim.
    * ``max_items`` — upper bound on the number of refs.
    """

    def __init__(
        self,
        *,
        required: bool,
        kind: str = "image",
        https_image: bool = False,
        max_items: int = 1,
    ) -> None:
        self.required = required
        self.kind = kind
        self.https_image = https_image
        self.max_items = max_items

    def validate(self, media_refs: list[dict[str, Any]]) -> None:
        refs = list(media_refs or [])
        if not refs:
            if self.required:
                raise MediaRuleError("media_required")
            return
        if len(refs) > self.max_items:
            raise MediaRuleError("too_many_media")
        for ref in refs:
            kind = ref.get("kind", "image")
            if kind != self.kind:
                raise MediaRuleError("media_kind_not_supported")
            url = ref.get("url")
            if not isinstance(url, str) or not url:
                raise MediaRuleError("media_url_invalid")
            if self.https_image and not _is_https_image(url):
                raise MediaRuleError("media_not_https_image")


def _is_https_image(url: str) -> bool:
    if not url.startswith("https:" + "//"):
        return False
    path = url.split("?", 1)[0].split("#", 1)[0].lower()
    return path.endswith(IMAGE_SUFFIXES)


class PublisherDeps(BaseModel):
    """What a ``publisher_factory`` may receive — the shared client, never a new one."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    settings: Any
    http_client: Any = None


PublisherFactory = Callable[[PublisherDeps], "Publisher"]


class ChannelDefinition(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    channel: Channel
    display_name: str = Field(min_length=1, max_length=64)
    provider: Provider
    activation_status: ActivationStatus
    capabilities: ChannelCapabilities
    required_scopes: tuple[str, ...]
    activation_requirements: tuple[ActivationRequirement, ...]
    media_rule: MediaRule = Field(exclude=True, repr=False)
    publisher_factory: PublisherFactory = Field(exclude=True, repr=False)

    # ------------------------------------------------------------ helpers ---
    @property
    def limits(self) -> int:
        """Hard text limit (``capabilities.text_limit``) — what ``content/`` asks for."""
        return self.capabilities.text_limit

    @property
    def is_active(self) -> bool:
        return self.activation_status is ActivationStatus.ACTIVE

    def validate_media(self, media_refs: list[dict[str, Any]]) -> None:
        """``None`` when ``media_refs`` satisfy the channel; raises :class:`MediaRuleError`."""
        self.media_rule.validate(media_refs)

    def incomplete_requirements(self) -> tuple[ActivationRequirement, ...]:
        """Requirements that are ``pending`` / ``not_started`` (the 422 payload)."""
        return tuple(
            r for r in self.activation_requirements if r.status is not RequirementStatus.DONE
        )

    def build_publisher(self, settings: Settings, http_client: Any = None) -> Publisher:
        """``http_client`` is the app-wide shared client (``src.core.http``) or ``None``."""
        return self.publisher_factory(PublisherDeps(settings=settings, http_client=http_client))
