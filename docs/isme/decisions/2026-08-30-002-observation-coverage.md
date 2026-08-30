# SKMB-2026-08-30-002: Observation Coverage

- status: accepted
- decided_by: designer
- approval_source: user approved the complete release design with “通过，后续无需再审批”
- date: 2026-08-30
- commit: 68721f8
- patterns:
  - B_state_persistence
  - C_concurrent_operations
  - D_external_dependency
  - F_fail_semantics
- scope: collector sampling, status normalization, interval analytics, heatmap denominators

## Decision

Only a complete REST synchronization is an authoritative observation sample. Two
samples form a covered window when separated by no more than
`max(2 × expected cadence + 60 seconds, 10 minutes)`. After the final sample, a state
may extend only to the same deadline. Pipeline transitions can divide an observed
window, but pipeline silence cannot prove coverage.

Complete snapshots also create append-only per-person tracking transitions. Every
person/hour heatmap denominator intersects the requested local hour, authoritative
coverage, and that person's tracked interval. Values with fewer than
`max(30 minutes, 10% of eligible tracked minutes)` are unavailable rather than zero.

`location=offline` overrides an incoming active status. Private, hidden, traveling,
offline, and collection-gap states remain distinct. Events more than five minutes
ahead of ingestion are retained but quarantined from current state and all analytics.
The signed-in account is represented by its real display name plus a `自己` badge and
uses the same rules as friends.

Every collector data request keeps an append-only tenant-scoped raw record after
credential material is excluded. Existing raw metadata is used to backfill provable
coverage and anomalies without rewriting historical status events.

## Applies To

- collection and raw-fetch writes
- status/world interval construction
- current-state freshness
- daily and average heatmaps
- overlap, co-presence, rankings, and discovery
- migration and data-confidence tests

## Rationale

An outage is lack of knowledge, not evidence that a prior online state continued or
that a person became offline. Per-person evidence is required to prevent false
averages when tracking begins mid-range.

## Alternatives

- Extending the last state to query time was rejected because it fabricates duration.
- Treating missing coverage as offline was rejected because it biases every statistic.
- Using one shared hourly denominator was rejected because friend tracking periods differ.

## Supersedes

None.

## Superseded By

None.
