from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Mapping, Sequence


UTC = timezone.utc
WORLD_ID_PATTERN = re.compile(r"^(wrld_[0-9A-Za-z-]+)(?::|$)")


def _normalized_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _field(item: object, name: str, default: object = None) -> object:
    if isinstance(item, Mapping):
        return item.get(name, default)
    return getattr(item, name, default)


def _boolean(value: object) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _is_anomaly(event: object) -> bool:
    return any(
        _boolean(_field(event, name, False))
        for name in ("anomaly", "is_anomaly", "quarantined")
    ) or bool(str(_field(event, "anomaly_reason", "") or "").strip())


@dataclass(frozen=True, slots=True)
class TimeWindow:
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        normalized_start = _normalized_datetime(self.start)
        normalized_end = _normalized_datetime(self.end)
        if normalized_start is None or normalized_end is None:
            raise ValueError("time windows require valid datetimes")
        if normalized_end <= normalized_start:
            raise ValueError("time window end must be after start")
        object.__setattr__(self, "start", normalized_start)
        object.__setattr__(self, "end", normalized_end)

    @property
    def seconds(self) -> float:
        return (self.end - self.start).total_seconds()

    @property
    def minutes(self) -> float:
        return self.seconds / 60

    def intersection(self, other: "TimeWindow") -> "TimeWindow | None":
        start = max(self.start, other.start)
        end = min(self.end, other.end)
        return TimeWindow(start, end) if end > start else None


@dataclass(frozen=True, slots=True)
class PresenceSpan:
    start: datetime
    end: datetime
    status: str

    def __post_init__(self) -> None:
        normalized = TimeWindow(self.start, self.end)
        object.__setattr__(self, "start", normalized.start)
        object.__setattr__(self, "end", normalized.end)

    @property
    def window(self) -> TimeWindow:
        return TimeWindow(self.start, self.end)


@dataclass(frozen=True, slots=True)
class WorldSpan:
    start: datetime
    end: datetime
    status: str
    location: str
    location_kind: str
    world_id: str
    platform: str

    def __post_init__(self) -> None:
        normalized = TimeWindow(self.start, self.end)
        object.__setattr__(self, "start", normalized.start)
        object.__setattr__(self, "end", normalized.end)

    @property
    def window(self) -> TimeWindow:
        return TimeWindow(self.start, self.end)


@dataclass(frozen=True, slots=True)
class ActivityCell:
    online_minutes: float
    observed_minutes: float
    eligible_minutes: float
    ratio: float | None


@dataclass(frozen=True, slots=True)
class CoverageSummary:
    expected_minutes: float
    observed_minutes: float
    ratio: float
    first_observed: datetime | None
    last_observed: datetime | None
    gaps: list[TimeWindow]


def _as_window(value: object) -> TimeWindow | None:
    if isinstance(value, TimeWindow):
        return value
    start = _normalized_datetime(_field(value, "start"))
    end = _normalized_datetime(_field(value, "end"))
    if start is None or end is None or end <= start:
        return None
    return TimeWindow(start, end)


def merge_windows(windows: Iterable[object]) -> list[TimeWindow]:
    normalized = sorted(
        (item for value in windows if (item := _as_window(value)) is not None),
        key=lambda item: (item.start, item.end),
    )
    merged: list[TimeWindow] = []
    for current in normalized:
        if not merged or current.start > merged[-1].end:
            merged.append(current)
            continue
        previous = merged[-1]
        merged[-1] = TimeWindow(previous.start, max(previous.end, current.end))
    return merged


def intersect_windows(left: Iterable[object], right: Iterable[object]) -> list[TimeWindow]:
    left_windows = merge_windows(left)
    right_windows = merge_windows(right)
    intersections: list[TimeWindow] = []
    left_index = 0
    right_index = 0
    while left_index < len(left_windows) and right_index < len(right_windows):
        first = left_windows[left_index]
        second = right_windows[right_index]
        overlap = first.intersection(second)
        if overlap is not None:
            intersections.append(overlap)
        if first.end <= second.end:
            left_index += 1
        else:
            right_index += 1
    return merge_windows(intersections)


def observation_deadline(observed_at: datetime, cadence_seconds: int) -> datetime:
    normalized = _normalized_datetime(observed_at)
    if normalized is None:
        raise ValueError("observed_at must be a valid datetime")
    cadence = max(0, int(cadence_seconds))
    return normalized + timedelta(seconds=max(2 * cadence + 60, 600))


def build_observed_windows(
    samples: Iterable[object],
    *,
    range_start: datetime,
    range_end: datetime,
) -> list[TimeWindow]:
    query = TimeWindow(range_start, range_end)
    successful_by_time: dict[datetime, int] = {}
    for sample in samples:
        if not _boolean(_field(sample, "authoritative", False)):
            continue
        if str(_field(sample, "outcome", "")).strip().lower() != "success":
            continue
        observed_at = _normalized_datetime(_field(sample, "observed_at"))
        if observed_at is None:
            continue
        try:
            cadence = int(_field(sample, "expected_interval_seconds", 0))
        except (TypeError, ValueError):
            continue
        if cadence <= 0:
            continue
        successful_by_time[observed_at] = max(
            cadence, successful_by_time.get(observed_at, 0)
        )

    successful = sorted(successful_by_time.items())
    runs: list[TimeWindow] = []
    run_start: datetime | None = None
    run_end: datetime | None = None
    for previous, current in zip(successful, successful[1:]):
        previous_at, previous_cadence = previous
        current_at, current_cadence = current
        if current_at <= observation_deadline(previous_at, previous_cadence):
            if run_start is None:
                run_start = previous_at
            run_end = observation_deadline(current_at, current_cadence)
            continue
        if run_start is not None and run_end is not None:
            runs.append(TimeWindow(run_start, run_end))
        run_start = None
        run_end = None
    if run_start is not None and run_end is not None:
        runs.append(TimeWindow(run_start, run_end))

    return merge_windows(
        overlap
        for item in runs
        if (overlap := item.intersection(query)) is not None
    )


def build_tracking_windows(
    events: Iterable[object],
    *,
    range_start: datetime,
    range_end: datetime,
) -> list[TimeWindow]:
    query = TimeWindow(range_start, range_end)
    ordered: list[tuple[datetime, object]] = []
    for item in events:
        if _is_anomaly(item):
            continue
        occurred_at = _normalized_datetime(_field(item, "occurred_at"))
        if occurred_at is not None:
            ordered.append((occurred_at, item))
    ordered.sort(key=lambda item: item[0])

    tracked = False
    cursor = query.start
    windows: list[TimeWindow] = []
    for occurred_at, item in ordered:
        next_tracked = _boolean(_field(item, "tracked", False))
        if occurred_at < query.start:
            tracked = next_tracked
            continue
        if occurred_at >= query.end:
            break
        if tracked and occurred_at > cursor:
            windows.append(TimeWindow(cursor, occurred_at))
        tracked = next_tracked
        cursor = max(cursor, occurred_at)
    if tracked and query.end > cursor:
        windows.append(TimeWindow(cursor, query.end))
    return merge_windows(windows)


def effective_state(status: str, location: str, is_self: bool) -> str:
    normalized_location = str(location or "").strip().lower()
    if normalized_location == "offline" or (is_self and not normalized_location):
        return "offline"
    normalized_status = str(status or "").strip().lower()
    return normalized_status or "offline"


def world_id_from_location(location: str) -> str:
    match = WORLD_ID_PATTERN.match(str(location or "").strip())
    return match.group(1) if match else ""


def classify_location(status: str, location: str, is_self: bool) -> str:
    if effective_state(status, location, is_self) == "offline":
        return "offline"
    normalized = str(location or "").strip().lower()
    if normalized == "private":
        return "private"
    if normalized == "traveling":
        return "traveling"
    if world_id_from_location(location):
        return "world"
    return "hidden"


@dataclass(frozen=True, slots=True)
class _PresenceState:
    start: datetime
    end: datetime
    status: str
    location: str
    platform: str


def _presence_states(
    events: Iterable[object],
    start: datetime,
    end: datetime,
    *,
    is_self: bool,
) -> list[_PresenceState]:
    query = TimeWindow(start, end)
    ordered: list[tuple[datetime, object]] = []
    for item in events:
        if _is_anomaly(item):
            continue
        occurred_at = _normalized_datetime(_field(item, "occurred_at"))
        if occurred_at is not None:
            ordered.append((occurred_at, item))
    ordered.sort(key=lambda item: item[0])

    status = "offline"
    location = ""
    platform = ""
    cursor = query.start
    states: list[_PresenceState] = []
    for occurred_at, item in ordered:
        next_status = str(
            _field(item, "new_status", _field(item, "status", "offline")) or "offline"
        )
        has_location = (
            "location" in item if isinstance(item, Mapping) else hasattr(item, "location")
        )
        has_platform = (
            "platform" in item if isinstance(item, Mapping) else hasattr(item, "platform")
        )
        next_location = str(_field(item, "location", location) or "")
        next_platform = str(_field(item, "platform", platform) or "")
        if occurred_at < query.start:
            status = effective_state(next_status, next_location, is_self)
            if has_location:
                location = next_location
            if has_platform and next_platform:
                platform = next_platform
            continue
        if occurred_at >= query.end:
            break
        if occurred_at > cursor:
            states.append(_PresenceState(cursor, occurred_at, status, location, platform))
        status = effective_state(next_status, next_location, is_self)
        if has_location:
            location = next_location
        if has_platform and next_platform:
            platform = next_platform
        cursor = max(cursor, occurred_at)
    if query.end > cursor:
        states.append(_PresenceState(cursor, query.end, status, location, platform))
    return states


def build_online_spans(
    events: Iterable[object],
    covered: Iterable[object],
    start: datetime,
    end: datetime,
    *,
    is_self: bool = False,
) -> list[PresenceSpan]:
    coverage = merge_windows(covered)
    spans: list[PresenceSpan] = []
    for state in _presence_states(events, start, end, is_self=is_self):
        if state.status == "offline":
            continue
        state_window = TimeWindow(state.start, state.end)
        for observed in coverage:
            overlap = state_window.intersection(observed)
            if overlap is not None:
                spans.append(PresenceSpan(overlap.start, overlap.end, state.status))

    merged: list[PresenceSpan] = []
    for current in sorted(spans, key=lambda item: (item.start, item.end, item.status)):
        if (
            merged
            and current.status == merged[-1].status
            and current.start <= merged[-1].end
        ):
            previous = merged[-1]
            merged[-1] = PresenceSpan(
                previous.start, max(previous.end, current.end), previous.status
            )
        else:
            merged.append(current)
    return merged


def build_world_spans(
    events: Iterable[object],
    covered: Iterable[object],
    start: datetime,
    end: datetime,
    *,
    is_self: bool = False,
) -> list[WorldSpan]:
    coverage = merge_windows(covered)
    spans: list[WorldSpan] = []
    for state in _presence_states(events, start, end, is_self=is_self):
        if state.status == "offline":
            continue
        state_window = TimeWindow(state.start, state.end)
        for observed in coverage:
            overlap = state_window.intersection(observed)
            if overlap is None:
                continue
            spans.append(
                WorldSpan(
                    overlap.start,
                    overlap.end,
                    state.status,
                    state.location,
                    classify_location(state.status, state.location, is_self),
                    world_id_from_location(state.location),
                    state.platform,
                )
            )

    merged: list[WorldSpan] = []
    for current in sorted(spans, key=lambda item: (item.start, item.end)):
        if merged:
            previous = merged[-1]
            same_value = (
                current.status,
                current.location,
                current.location_kind,
                current.world_id,
                current.platform,
            ) == (
                previous.status,
                previous.location,
                previous.location_kind,
                previous.world_id,
                previous.platform,
            )
            if same_value and current.start <= previous.end:
                merged[-1] = WorldSpan(
                    previous.start,
                    max(previous.end, current.end),
                    previous.status,
                    previous.location,
                    previous.location_kind,
                    previous.world_id,
                    previous.platform,
                )
                continue
        merged.append(current)
    return merged


def _minutes(windows: Sequence[TimeWindow]) -> float:
    return sum(item.minutes for item in windows)


def activity_cell(
    *,
    online: Iterable[object],
    covered: Iterable[object],
    tracked: Iterable[object],
    hour_start: datetime,
    hour_end: datetime,
) -> ActivityCell:
    hour = TimeWindow(hour_start, hour_end)
    eligible = intersect_windows(tracked, [hour])
    observed = intersect_windows(eligible, covered)
    online_observed = intersect_windows(observed, online)
    eligible_minutes = _minutes(eligible)
    observed_minutes = _minutes(observed)
    online_minutes = _minutes(online_observed)
    minimum_evidence = max(30.0, eligible_minutes * 0.1)
    ratio = (
        min(1.0, online_minutes / observed_minutes)
        if observed_minutes >= minimum_evidence and observed_minutes > 0
        else None
    )
    return ActivityCell(
        online_minutes=online_minutes,
        observed_minutes=observed_minutes,
        eligible_minutes=eligible_minutes,
        ratio=ratio,
    )


def coverage_summary(
    covered: Iterable[object],
    *,
    range_start: datetime,
    range_end: datetime,
) -> CoverageSummary:
    query = TimeWindow(range_start, range_end)
    observed = intersect_windows(covered, [query])
    gaps: list[TimeWindow] = []
    cursor = query.start
    for item in observed:
        if item.start > cursor:
            gaps.append(TimeWindow(cursor, item.start))
        cursor = max(cursor, item.end)
    if cursor < query.end:
        gaps.append(TimeWindow(cursor, query.end))
    expected_minutes = query.minutes
    observed_minutes = _minutes(observed)
    return CoverageSummary(
        expected_minutes=expected_minutes,
        observed_minutes=observed_minutes,
        ratio=observed_minutes / expected_minutes,
        first_observed=observed[0].start if observed else None,
        last_observed=observed[-1].end if observed else None,
        gaps=gaps,
    )
