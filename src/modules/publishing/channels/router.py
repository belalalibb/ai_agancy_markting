"""``GET /api/v1/channels`` — the public view of the channel registry (SPRINT-08 B-2).

Any authenticated user (``CurrentUser``, no database round-trip) may read it: the payload is
purely declarative (capabilities, documentary scopes, the activation matrix). It carries
**no secret and no brand data** — no tokens, no connections, no settings values.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict

from src.modules.auth.dependencies import CurrentUser
from src.modules.publishing.channels import (
    ActivationRequirement,
    ActivationStatus,
    ChannelCapabilities,
    ChannelDefinition,
    UnknownChannelError,
    get_registry,
)
from src.modules.publishing.channels.definition import Provider

router = APIRouter(prefix="/channels", tags=["channels"])


class ActivationRequirementPublic(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    title: str
    owner: str
    status: str
    note: str


class ChannelPublic(BaseModel):
    model_config = ConfigDict(frozen=True)

    channel: str
    display_name: str
    provider: Provider
    activation_status: ActivationStatus
    capabilities: ChannelCapabilities
    required_scopes: tuple[str, ...]
    activation_requirements: list[ActivationRequirementPublic]

    @classmethod
    def from_definition(cls, definition: ChannelDefinition) -> ChannelPublic:
        return cls(
            channel=definition.channel.value,
            display_name=definition.display_name,
            provider=definition.provider,
            activation_status=definition.activation_status,
            capabilities=definition.capabilities,
            required_scopes=definition.required_scopes,
            activation_requirements=[
                _requirement_public(r) for r in definition.activation_requirements
            ],
        )


class ChannelListPublic(BaseModel):
    model_config = ConfigDict(frozen=True)

    channels: list[ChannelPublic]


def _requirement_public(requirement: ActivationRequirement) -> ActivationRequirementPublic:
    return ActivationRequirementPublic(
        key=requirement.key,
        title=requirement.title,
        owner=requirement.owner.value,
        status=requirement.status.value,
        note=requirement.note,
    )


@router.get("", response_model=ChannelListPublic)
def list_channels(_actor: CurrentUser) -> ChannelListPublic:
    return ChannelListPublic(
        channels=[ChannelPublic.from_definition(d) for d in get_registry().all()]
    )


@router.get("/{channel}", response_model=ChannelPublic)
def get_channel(channel: str, _actor: CurrentUser) -> ChannelPublic:
    try:
        definition = get_registry().get(channel)
    except UnknownChannelError as exc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={"error": "unknown_channel", "message": f"unknown channel {channel!r}"},
        ) from exc
    return ChannelPublic.from_definition(definition)
