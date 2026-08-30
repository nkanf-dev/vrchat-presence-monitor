# SKMB-2026-08-30-003: World Resolution

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
- scope: VRChat world metadata cache, background resolver, discovery presentation

## Decision

Opening analytics, search, a player, the world library, or discovery never performs a
synchronous VRChat API request. Unknown IDs return immediately and enter a bounded
background resolver queue. Resolution is single-flight per world ID, globally
deduplicated, concurrency-limited, and honors `Retry-After`, negative cache TTLs, and
per-world exponential backoff. Retry outcome, attempt count, and deadline persist in
SQLite so a process restart cannot erase a 429 deadline and immediately retry.

The last successful title, thumbnail, author, tags, and metadata remain visible when
a refresh fails, with a stale marker. A useful world never regresses to a permanent
`正在解析世界` state. Private, offline, traveling, and hidden locations are not ranked
as worlds. Stable world colors remain unique within the visible legend and combine
color with text or texture.

## Applies To

- `server/analytics.py` and the extracted resolver/discovery modules
- world cache storage and background scheduling
- world library, timelines, legends, cards, and dialogs
- 429, cache, and stale-metadata tests

## Rationale

The product must remain aggressive about using already observed data without turning
normal browsing into an upstream request burst or a source of 429 responses.

## Alternatives

- Resolving worlds inside page requests was rejected because latency and rate limits become user-driven.
- Clearing cached data after refresh failure was rejected because it destroys known-good information.
- Retrying every unresolved card independently was rejected because duplicate world IDs would stampede VRChat.

## Supersedes

None.

## Superseded By

None.
