# SKMB-2026-08-30-001: Hosted Session Lifecycle

- status: accepted
- decided_by: designer
- approval_source: user approved the complete release design with “通过，后续无需再审批”
- date: 2026-08-30
- commit: 68721f8
- patterns:
  - B_state_persistence
  - C_concurrent_operations
  - E_security_boundary
  - F_fail_semantics
- scope: hosted identity, viewer sessions, encrypted VRChat sessions, collector ownership

## Decision

The VRChat user ID is the canonical tenant mapping. A successful login for an
existing VRChat user reuses that tenant and atomically replaces its encrypted
upstream session only after authentication and any 2FA challenge succeeds. A failed
login leaves the prior usable session and collector running.

Browser viewer sessions and the upstream collector session are independent. Signing
out one device revokes only that viewer session. Only the explicit disconnect action
stops hosted collection and clears the upstream session. The VRChat password exists
only in the login request and is never persisted, logged, exported, backed up, or
included in raw capture.

Every viewer and collector query derives `tenant_id` from authenticated server-side
context. User, friend, world, tag, or event IDs in a path never select another tenant.

## Applies To

- `server/vrchat_auth.py`
- `server/storage.py`
- `server/hosted_collector.py`
- viewer, reconnect, sign-out, and disconnect endpoints
- tenant-isolation and session-lifecycle tests

## Rationale

The approved product journey is one login followed by server-resident collection,
with seamless reuse from other devices and no duplicate user data spaces.

## Alternatives

- Creating a tenant per browser login was rejected because it fragments one user's history.
- Stopping collection on browser sign-out was rejected because browser presence is not collector ownership.
- Persisting passwords for automated relogin was rejected; only encrypted VRChat session material is durable.

## Supersedes

None.

## Superseded By

None.
