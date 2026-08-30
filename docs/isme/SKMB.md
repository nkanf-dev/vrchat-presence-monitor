# State Machine Knowledge Base

This index records the state and failure decisions that govern Presence Monitor.
A decision is authoritative only when its file is `accepted`, names the designer,
and cites explicit approval evidence.

## Decision Index

| id | status | scope | patterns | file | commit |
| --- | --- | --- | --- | --- | --- |
| SKMB-2026-08-30-001 | accepted | hosted identity and session lifecycle | B,C,E,F | decisions/2026-08-30-001-hosted-session-lifecycle.md | 68721f8 |
| SKMB-2026-08-30-002 | accepted | collection coverage and analytics truth | B,C,D,F | decisions/2026-08-30-002-observation-coverage.md | 68721f8 |
| SKMB-2026-08-30-003 | accepted | world metadata resolution | B,C,D,F | decisions/2026-08-30-003-world-resolution.md | 68721f8 |
| SKMB-2026-08-30-004 | accepted | portable backup and restore | B,C,E,F,G | decisions/2026-08-30-004-backup-restore.md | 68721f8 |
| SKMB-2026-08-30-005 | accepted | browser navigation and recoverable UX | B,C,F | decisions/2026-08-30-005-browser-state.md | 68721f8 |
| SKMB-2026-08-30-006 | accepted | release and production deployment | B,D,F,G | decisions/2026-08-30-006-release-deployment.md | 68721f8 |

## Named States

| state | meaning | owner | notes | source |
| --- | --- | --- | --- | --- |
| browser_authenticated | Browser holds a valid tenant-scoped viewer session | FastAPI | Independent from the upstream VRChat session | SKMB-2026-08-30-001 |
| browser_signed_out | This browser has no valid viewer session | FastAPI | Does not stop collection | SKMB-2026-08-30-001 |
| collector_fresh | An authoritative snapshot is inside its cadence-derived validity window | collector | Current-state UI may use live semantics | SKMB-2026-08-30-002 |
| collector_gap | No authoritative observation covers the interval | analytics | Neither online nor offline | SKMB-2026-08-30-002 |
| vrchat_session_expired | Upstream session cannot authenticate | collector | Historical and browser data remain intact | SKMB-2026-08-30-001 |
| vrchat_disconnected | User explicitly stopped upstream collection | collector | Reconnect requires VRChat login | SKMB-2026-08-30-001 |
| world_cached | Last successful world metadata is available | resolver | May be fresh or visibly stale | SKMB-2026-08-30-003 |
| world_backoff | World resolution is delayed until its retry deadline | resolver | Cached metadata remains visible | SKMB-2026-08-30-003 |
| note_conflict | Local note draft and a newer server revision both exist | browser | Neither value is silently discarded | SKMB-2026-08-30-005 |

## Transition Decisions

| id | from_state | event | to_state | actions | source |
| --- | --- | --- | --- | --- | --- |
| T-001 | browser_signed_out | successful VRChat login and optional 2FA | browser_authenticated | map VRChat user ID to its tenant, atomically refresh encrypted upstream session, issue viewer session | SKMB-2026-08-30-001 |
| T-002 | browser_authenticated | sign out this device | browser_signed_out | revoke only this viewer session | SKMB-2026-08-30-001 |
| T-003 | collector_fresh | cadence-derived observation deadline passes | collector_gap | stop extending all status/world spans and render last-known state | SKMB-2026-08-30-002 |
| T-004 | collector_gap | complete authoritative snapshot succeeds | collector_fresh | record sample and resume coverage from the new observation | SKMB-2026-08-30-002 |
| T-005 | world_cached | refresh fails | world_backoff | retain cached metadata and schedule bounded retry | SKMB-2026-08-30-003 |
| T-006 | any browser view | Back, Forward, or refresh | equivalent restored view | restore route, entity, filters, range, pagination, and scroll anchor | SKMB-2026-08-30-005 |

## Invariants

| id | invariant | source |
| --- | --- | --- |
| I-001 | Tenant identity always comes from authenticated server context; request IDs cannot select another tenant | SKMB-2026-08-30-001 |
| I-002 | VRChat passwords are request-only and never enter storage, raw capture, logs, backups, or exports | SKMB-2026-08-30-001 |
| I-003 | Missing observations contribute neither online nor offline time | SKMB-2026-08-30-002 |
| I-004 | `location=offline` always normalizes to offline | SKMB-2026-08-30-002 |
| I-005 | Ordinary analytics browsing makes zero synchronous VRChat API calls | SKMB-2026-08-30-003 |
| I-006 | Portable import is tenant-scoped, idempotent, merge-only, and atomic | SKMB-2026-08-30-004 |
| I-007 | A release cannot delete production history, replace secrets, or erase a working session to recover | SKMB-2026-08-30-006 |

## Fail Semantics

| id | context | behavior | source |
| --- | --- | --- | --- |
| F-001 | New login fails before upstream authentication completes | Keep the previous encrypted session and collector unchanged | SKMB-2026-08-30-001 |
| F-002 | Collection exceeds its observation deadline | Split intervals and expose an unknown data gap | SKMB-2026-08-30-002 |
| F-003 | World metadata refresh fails | Keep last successful metadata, expose stale state, and back off | SKMB-2026-08-30-003 |
| F-004 | Import validation or merge fails | Roll back the entire import and leave tenant data unchanged | SKMB-2026-08-30-004 |
| F-005 | A dashboard panel refresh fails | Keep rendered data and isolate retry/error state to that panel | SKMB-2026-08-30-005 |
| F-006 | Candidate deploy or migration fails | Retain/restore the prior immutable image and verified database without in-place destructive repair | SKMB-2026-08-30-006 |

## Statistical Defaults Allowed Temporarily

None.

## Open Decisions

None.
