from __future__ import annotations

from typing import Annotated, Literal, Optional

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    model_validator,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginRequest(StrictModel):
    access_code: str = Field(min_length=1, max_length=40)


class VRChatLoginRequest(StrictModel):
    username: str = Field(min_length=1, max_length=128)
    password: SecretStr = Field(min_length=1, max_length=1024)


class VRChatTwoFactorRequest(StrictModel):
    code: str = Field(min_length=1, max_length=64)


class AnnotationRequest(StrictModel):
    note: str = Field(default="", max_length=20_000)
    pinned: bool = False
    revision: str | None = Field(default=None, max_length=256)


class TagRequest(StrictModel):
    name: str = Field(min_length=1, max_length=80)
    color: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")


class PreferenceRequest(StrictModel):
    timezone: str = Field(min_length=1, max_length=80)


class DashboardPanelRequest(StrictModel):
    id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    kind: Literal[
        "online-now",
        "tracked-count",
        "status-breakdown",
        "online-ranking",
        "daily-changes",
        "friend-heatmap",
        "world-ranking",
        "platform-breakdown",
        "collection-coverage",
    ]
    title: str = Field(default="", max_length=80)
    x: int = Field(ge=0, le=11)
    y: int = Field(ge=0, le=10_000)
    w: int = Field(ge=1, le=12)
    h: int = Field(ge=3, le=20)
    range_days: int = Field(default=0, ge=0, le=730)
    limit: int = Field(default=10, ge=3, le=30)
    include_self: bool = True
    friend_ids: list[str] = Field(default_factory=list, max_length=50)
    statuses: list[str] = Field(default_factory=list, max_length=10)
    platforms: list[str] = Field(default_factory=list, max_length=10)
    world_ids: list[str] = Field(default_factory=list, max_length=50)
    world_tag: str = Field(default="", max_length=160)
    world_sort: Literal["people", "minutes", "visits", "recent"] = "people"
    view: Literal[
        "auto", "number", "progress", "donut", "bar", "line", "area", "heatmap", "table"
    ] = "auto"
    sort_direction: Literal["auto", "asc", "desc"] = "auto"
    show_legend: bool = True
    show_table: bool = True
    metric: Literal[
        "auto", "count", "percent", "hours", "hours_per_day", "changes", "ratio",
        "online_minutes", "people", "minutes", "visits"
    ] = "auto"

    @model_validator(mode="after")
    def validate_grid_bounds(self) -> "DashboardPanelRequest":
        if self.x + self.w > 12:
            raise ValueError("dashboard panel exceeds the 12-column grid")
        if any(not value.startswith("usr_") for value in self.friend_ids):
            raise ValueError("dashboard friend filters must use VRChat user ids")
        if any(not value.startswith("wrld_") for value in self.world_ids):
            raise ValueError("dashboard world filters must use VRChat world ids")
        return self


class DashboardDocumentRequest(StrictModel):
    schema_version: Literal[1] = 1
    title: str = Field(default="我的仪表盘", min_length=1, max_length=80)
    range_days: int = Field(default=7, ge=1, le=730)
    refresh_seconds: Literal[0, 30, 60, 300] = 60
    panels: list[DashboardPanelRequest] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_unique_panels(self) -> "DashboardDocumentRequest":
        ids = [panel.id for panel in self.panels]
        if len(ids) != len(set(ids)):
            raise ValueError("dashboard panel ids must be unique")
        return self


class DashboardPutRequest(StrictModel):
    revision: str | None = Field(default=None, max_length=256)
    document: DashboardDocumentRequest


class DashboardQueryRequest(StrictModel):
    panel: DashboardPanelRequest
    global_range_days: int = Field(default=7, ge=1, le=730)


class DashboardShareAppearanceRequest(StrictModel):
    preset: Literal["midnight", "aurora", "paper", "sunset"] = "midnight"
    heading: str = Field(default="", max_length=120)
    description: str = Field(default="", max_length=500)
    page_title: str = Field(default="", max_length=120)
    avatar_url: str = Field(default="", max_length=2048)
    custom_css: str = Field(default="", max_length=12_000)


class DashboardSharePutRequest(StrictModel):
    password: str = Field(default="", max_length=256)
    appearance: DashboardShareAppearanceRequest = Field(
        default_factory=DashboardShareAppearanceRequest
    )


class DashboardShareUnlockRequest(StrictModel):
    password: str = Field(default="", max_length=256)


class BootstrapRequest(StrictModel):
    tenant_name: str = Field(min_length=1, max_length=120)
    collector_name: str = Field(default="local bridge", min_length=1, max_length=120)


class TelemetryModel(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


BoundedLink = Annotated[str, Field(max_length=2048)]
BoundedLegacyEventId = Annotated[str, Field(pattern=r"^local-[0-9]+$", max_length=64)]


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
    previous_event_ids: list[BoundedLegacyEventId] = Field(default_factory=list, max_length=1)


class ObservationTelemetry(StrictModel):
    observed_at: str = Field(min_length=1, max_length=64)
    expected_interval_seconds: int = Field(ge=45, le=3600)
    authoritative: Literal[True]


class TelemetryRequest(StrictModel):
    schema_version: Literal[1, 2]
    friends: list[FriendTelemetry] = Field(default_factory=list, max_length=5000)
    events: list[EventTelemetry] = Field(default_factory=list, max_length=10000)
    observation: ObservationTelemetry | None = None

    @model_validator(mode="after")
    def validate_observation_contract(self) -> "TelemetryRequest":
        if self.schema_version == 2 and self.observation is None:
            raise ValueError("schema_version 2 requires observation")
        if self.schema_version == 1 and self.observation is not None:
            raise ValueError("schema_version 1 does not accept observation")
        return self
