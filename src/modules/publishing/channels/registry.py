"""``ChannelRegistry`` — the only place that answers "what does channel X allow?" (SPRINT-08).

The registry is an immutable mapping ``Channel ⇒ ChannelDefinition`` built once by
:func:`src.modules.publishing.channels.get_registry`. ``content/`` and the scheduler read
limits, scopes, media rules, activation state and publishers from here — never from module
constants (directive §2-0 ب, B1-U5).
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping

from src.modules.content.domain.models import Channel
from src.modules.publishing.channels.definition import ActivationStatus, ChannelDefinition
from src.modules.social_connect.domain.models import ConnectedAccount


class UnknownChannelError(KeyError):
    """``registry.get`` with a value that is not a registered channel (⇒ 404 at the API)."""

    def __init__(self, channel: object) -> None:
        self.channel = channel
        super().__init__(f"unknown channel: {channel!r}")


class ChannelRegistry(Mapping[Channel, ChannelDefinition]):
    def __init__(self, definitions: tuple[ChannelDefinition, ...]) -> None:
        self._by_channel: dict[Channel, ChannelDefinition] = {}
        for definition in definitions:
            if definition.channel in self._by_channel:
                raise ValueError(f"duplicate channel definition: {definition.channel}")
            self._by_channel[definition.channel] = definition

    # ---------------------------------------------------------------- Mapping ---
    def __getitem__(self, channel: Channel) -> ChannelDefinition:
        return self.get(channel)

    def __iter__(self) -> Iterator[Channel]:
        return iter(self._by_channel)

    def __len__(self) -> int:
        return len(self._by_channel)

    # -------------------------------------------------------------------- API ---
    def get(self, channel: Channel | str) -> ChannelDefinition:  # type: ignore[override]
        """The definition for ``channel`` (enum or raw value); ``UnknownChannelError`` else."""
        try:
            key = channel if isinstance(channel, Channel) else Channel(channel)
        except ValueError as exc:
            raise UnknownChannelError(channel) from exc
        try:
            return self._by_channel[key]
        except KeyError as exc:
            raise UnknownChannelError(channel) from exc

    def all(self) -> tuple[ChannelDefinition, ...]:
        """Every definition in enum order."""
        return tuple(self._by_channel[c] for c in Channel if c in self._by_channel)

    def activatable(self) -> tuple[ChannelDefinition, ...]:
        """Channels that exist in the registry but are **not** activated yet."""
        return tuple(d for d in self.all() if d.activation_status is not ActivationStatus.ACTIVE)

    def active(self) -> tuple[ChannelDefinition, ...]:
        return tuple(d for d in self.all() if d.activation_status is ActivationStatus.ACTIVE)


def missing_publish_scopes(account: ConnectedAccount, definition: ChannelDefinition) -> list[str]:
    """The publishing scopes the connection cannot *prove* it holds for ``definition``.
    Unverified scopes count as missing (unknown ≠ confirmed, SPRINT-06 A-1). Moved verbatim
    from ``content/service.py`` (SPRINT-07 B2) — now keyed by the definition."""
    required = definition.required_scopes
    if not account.scopes_verified:
        return list(required)
    granted = set(account.granted_scopes)
    return [scope for scope in required if scope not in granted]
