from __future__ import annotations

from typing import Annotated, Literal, Optional

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginRequest(StrictModel):
    access_code: str = Field(min_length=1, max_length=40)


class BootstrapRequest(StrictModel):
    tenant_name: str = Field(min_length=1, max_length=120)
    collector_name: str = Field(default="local bridge", min_length=1, max_length=120)


class TelemetryModel(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


BoundedLink = Annotated[str, Field(max_length=2048)]


class FriendTelemetry(TelemetryModel):
    id: str = Field(min_length=1, max_length=128)
    username: str = Field(default="", max_length=128)
    display_name: str = Field(
        default="",
        max_length=256,
        validation_alias=AliasChoices("display_name", "displayName"),
    )
    is_self: bool = Field(default=False, validation_alias=AliasChoices("is_self", "isSelf"))
    status: str = Field(default="offline", max_length=40)
    status_description: str = Field(
        default="",
        max_length=512,
        validation_alias=AliasChoices("status_description", "statusDescription"),
    )
    location: str = Field(default="", max_length=1024)
    platform: str = Field(default="", max_length=80)
    avatar_url: str = Field(default="", max_length=2048, validation_alias=AliasChoices("avatar_url", "avatarUrl"))
    avatar_image_url: str = Field(
        default="",
        max_length=2048,
        validation_alias=AliasChoices("avatar_image_url", "avatarImageUrl"),
    )
    bio: str = Field(default="", max_length=8192)
    bio_links: list[BoundedLink] = Field(
        default_factory=list,
        max_length=32,
        validation_alias=AliasChoices("bio_links", "bioLinks"),
    )
    last_seen: Optional[str] = Field(default=None, max_length=64, validation_alias=AliasChoices("last_seen", "lastSeen"))
    last_changed: Optional[str] = Field(
        default=None,
        max_length=64,
        validation_alias=AliasChoices("last_changed", "lastChanged"),
    )
    updated_at: Optional[str] = Field(
        default=None,
        max_length=64,
        validation_alias=AliasChoices("updated_at", "updatedAt"),
    )


class EventTelemetry(TelemetryModel):
    client_event_id: str = Field(min_length=1, max_length=256, validation_alias=AliasChoices("client_event_id", "id"))
    friend_id: str = Field(min_length=1, max_length=128, validation_alias=AliasChoices("friend_id", "friendId"))
    occurred_at: str = Field(min_length=1, max_length=64)
    old_status: str = Field(default="unknown", max_length=40)
    new_status: str = Field(default="offline", max_length=40)
    location: str = Field(default="", max_length=1024)
    platform: str = Field(default="", max_length=80)
    source: str = Field(default="local-bridge", max_length=80)


class TelemetryRequest(StrictModel):
    schema_version: Literal[1]
    friends: list[FriendTelemetry] = Field(default_factory=list, max_length=5000)
    events: list[EventTelemetry] = Field(default_factory=list, max_length=10000)
