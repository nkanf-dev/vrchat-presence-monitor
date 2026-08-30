from __future__ import annotations

import unittest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from server.analytics import AnalyticsService
from server.observation import (
    TimeWindow,
    activity_cell,
    build_observed_windows,
    build_online_spans,
    build_tracking_windows,
    build_world_spans,
    classify_location,
    coverage_summary,
    effective_state,
)


UTC = timezone.utc


def instant(hour: str, *, day: str = "2026-08-30") -> datetime:
    return datetime.fromisoformat(f"{day}T{hour}:00+00:00")


def window(start: str, end: str) -> TimeWindow:
    return TimeWindow(instant(start), instant(end))


def sample(hour: str, cadence_seconds: int, **overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "observed_at": instant(hour),
        "expected_interval_seconds": cadence_seconds,
        "outcome": "success",
        "authoritative": True,
    }
    value.update(overrides)
    return value


def event(
    hour: str,
    *,
    status: str = "active",
    location: str = "wrld_00000000-0000-0000-0000-000000000001:1",
    anomaly: bool = False,
    day: str = "2026-08-30",
) -> dict[str, object]:
    return {
        "occurred_at": instant(hour, day=day),
        "new_status": status,
        "location": location,
        "platform": "standalonewindows",
        "anomaly": anomaly,
    }


class ObservationCoverageTests(unittest.TestCase):
    def test_gap_stops_online_and_world_span(self):
        windows = build_observed_windows(
            [sample("08:00", 180), sample("08:03", 180), sample("09:00", 180)],
            range_start=instant("08:00"),
            range_end=instant("10:00"),
        )

        self.assertEqual(windows, [window("08:00", "08:13")])

    def test_single_success_does_not_claim_an_observed_interval(self):
        self.assertEqual(
            build_observed_windows(
                [sample("08:00", 180)],
                range_start=instant("08:00"),
                range_end=instant("09:00"),
            ),
            [],
        )

    def test_non_authoritative_and_failed_samples_do_not_claim_coverage(self):
        windows = build_observed_windows(
            [
                sample("08:00", 180),
                sample("08:03", 180, authoritative=False),
                sample("08:06", 180, outcome="network"),
                sample("08:09", 180),
            ],
            range_start=instant("08:00"),
            range_end=instant("09:00"),
        )

        self.assertEqual(windows, [window("08:00", "08:19")])

    def test_coverage_summary_reports_real_gaps(self):
        summary = coverage_summary(
            [window("08:10", "08:30"), window("09:00", "09:15")],
            range_start=instant("08:00"),
            range_end=instant("10:00"),
        )

        self.assertEqual(summary.expected_minutes, 120)
        self.assertEqual(summary.observed_minutes, 35)
        self.assertAlmostEqual(summary.ratio, 35 / 120)
        self.assertEqual(
            summary.gaps,
            [window("08:00", "08:10"), window("08:30", "09:00"), window("09:15", "10:00")],
        )


class PresenceSemanticsTests(unittest.TestCase):
    def test_location_offline_overrides_active_status(self):
        self.assertEqual(effective_state("active", "offline", is_self=False), "offline")

    def test_self_without_location_is_offline_but_friend_is_hidden_online(self):
        self.assertEqual(effective_state("active", "", is_self=True), "offline")
        self.assertEqual(effective_state("active", "", is_self=False), "active")
        self.assertEqual(classify_location("active", "", is_self=False), "hidden")

    def test_special_locations_remain_distinct(self):
        self.assertEqual(classify_location("active", "private", is_self=False), "private")
        self.assertEqual(classify_location("active", "traveling", is_self=False), "traveling")
        self.assertEqual(classify_location("active", "offline", is_self=False), "offline")

    def test_future_anomaly_is_excluded_even_after_wall_clock_passes(self):
        start = instant("00:00", day="2029-12-31")
        end = instant("00:00", day="2030-01-02")
        covered = [TimeWindow(start, end)]
        events = [event("00:00", day="2030-01-01", anomaly=True)]

        self.assertEqual(build_online_spans(events, covered, start, end), [])

    def test_collection_gap_splits_an_otherwise_continuous_online_state(self):
        spans = build_online_spans(
            [event("07:50")],
            [window("08:00", "08:20"), window("09:00", "09:10")],
            instant("08:00"),
            instant("10:00"),
        )

        self.assertEqual(
            [TimeWindow(item.start, item.end) for item in spans],
            [window("08:00", "08:20"), window("09:00", "09:10")],
        )

    def test_world_spans_keep_world_and_private_categories(self):
        events = [
            event("08:00"),
            event("08:20", location="private"),
            event("08:30", status="offline", location="offline"),
        ]

        spans = build_world_spans(
            events,
            [window("08:00", "09:00")],
            instant("08:00"),
            instant("09:00"),
        )

        self.assertEqual([item.location_kind for item in spans], ["world", "private"])
        self.assertEqual(
            spans[0].world_id,
            "wrld_00000000-0000-0000-0000-000000000001",
        )
        self.assertEqual(TimeWindow(spans[1].start, spans[1].end), window("08:20", "08:30"))

    def test_empty_platform_transition_keeps_the_last_known_platform(self):
        first = event("08:00")
        transition = event("08:20", status="ask me")
        transition["platform"] = ""

        spans = build_world_spans(
            [first, transition],
            [window("08:00", "09:00")],
            instant("08:00"),
            instant("09:00"),
        )

        self.assertEqual([item.platform for item in spans], ["standalonewindows", "standalonewindows"])


class TrackingAndActivityTests(unittest.TestCase):
    def test_tracking_window_uses_the_latest_transition_before_range(self):
        tracked = build_tracking_windows(
            [
                {"occurred_at": instant("07:45"), "tracked": True},
                {"occurred_at": instant("08:40"), "tracked": False},
                {"occurred_at": instant("08:50"), "tracked": True},
            ],
            range_start=instant("08:00"),
            range_end=instant("09:00"),
        )

        self.assertEqual(tracked, [window("08:00", "08:40"), window("08:50", "09:00")])

    def test_person_hour_denominator_intersects_tracking_and_coverage(self):
        result = activity_cell(
            online=[window("08:10", "08:30")],
            covered=[window("08:00", "08:40")],
            tracked=[window("08:05", "09:00")],
            hour_start=instant("08:00"),
            hour_end=instant("09:00"),
        )

        self.assertEqual(result.online_minutes, 20)
        self.assertEqual(result.observed_minutes, 35)
        self.assertEqual(result.eligible_minutes, 55)
        self.assertAlmostEqual(result.ratio or 0, 20 / 35)

    def test_insufficient_evidence_is_unavailable_not_zero(self):
        result = activity_cell(
            online=[],
            covered=[window("08:00", "08:20")],
            tracked=[window("08:00", "09:00")],
            hour_start=instant("08:00"),
            hour_end=instant("09:00"),
        )

        self.assertEqual(result.online_minutes, 0)
        self.assertEqual(result.observed_minutes, 20)
        self.assertEqual(result.eligible_minutes, 60)
        self.assertIsNone(result.ratio)

    def test_dst_day_duration_uses_elapsed_time(self):
        zone = ZoneInfo("America/New_York")
        spring = TimeWindow(
            datetime(2026, 3, 8, 0, 0, tzinfo=zone),
            datetime(2026, 3, 9, 0, 0, tzinfo=zone),
        )
        autumn = TimeWindow(
            datetime(2026, 11, 1, 0, 0, tzinfo=zone),
            datetime(2026, 11, 2, 0, 0, tzinfo=zone),
        )

        self.assertEqual(spring.minutes, 23 * 60)
        self.assertEqual(autumn.minutes, 25 * 60)


class AnalyticsCompatibilityTests(unittest.TestCase):
    def test_existing_timeline_shape_is_preserved(self):
        spans = AnalyticsService._online_spans(
            [
                event("08:00"),
                event("08:30", status="offline", location="offline"),
            ],
            instant("08:00"),
            instant("09:00"),
            ZoneInfo("UTC"),
        )

        self.assertEqual(
            spans,
            [{"start_minute": 0, "end_minute": 30, "status": "active"}],
        )

    def test_existing_analytics_adapter_excludes_quarantined_event(self):
        spans = AnalyticsService._online_spans(
            [event("08:00", anomaly=True)],
            instant("08:00"),
            instant("09:00"),
            ZoneInfo("UTC"),
        )

        self.assertEqual(spans, [])


if __name__ == "__main__":
    unittest.main()
