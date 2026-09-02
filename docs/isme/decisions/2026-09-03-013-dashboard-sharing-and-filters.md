# SKMB-2026-09-03-013: Dashboard Sharing And Filter State

- status: proposed
- decided_by: statistical_default
- approval_source: user requested top-level dashboards, flexible filters, password-protected sharing, access audit, and explicitly waived further approval
- date: 2026-09-03
- commit: pending
- patterns:
  - B_state_persistence
  - C_concurrent_operations
  - E_security_boundary
  - F_fail_semantics
  - G_rollback_recovery
- scope: dashboard filters, public share lifecycle, visitor sessions, access audit, and invalid pipeline identities

## Decision

The tenant dashboard is a first-class product area. Panel documents contain only
bounded, typed filters over known dimensions. The server derives tenant identity from
the viewer session and public visitors can execute only the queries frozen into a
published share snapshot.

A share is an explicit published snapshot of layout and filters whose data remains
live. It has a high-entropy identifier, optional scrypt password hash, an authorization
version, and revocation state. Share visitors receive a separate HttpOnly session;
password changes and revocation invalidate prior grants. Access events are append-only
and expose only masked fingerprints and coarse device information to the owner.

VRChat entities presented as players must have a `usr_` identifier. Pipeline messages
without a nested user identity are ignored as presence updates. Existing synthetic
`not_` player projections may be removed in a backed-up transaction while the raw
fetch evidence remains append-only.

## Applies To

- `vrchat_monitor/vrchat.py`
- `server/hosted_collector.py`
- `server/storage.py`
- `server/organization.py`
- `server/schemas.py`
- `server/app.py`
- dashboard navigation, sharing, filters, charts, styles, and tests

## Rationale

Published snapshots prevent an ordinary dashboard edit from unexpectedly changing a
public page while still showing current observations. Separate visitor authorization
keeps share access outside the tenant login boundary. Strict user-ID validation fixes
the notification/player type confusion at its source without deleting evidence.

## Alternatives

- Live-linking every draft edit was rejected because shared output would change silently.
- Reusing the tenant viewer cookie was rejected because it grants a broader capability.
- Free-form query expressions were rejected because they weaken isolation and predictability.
- Hiding `not_` rows only in the frontend was rejected because invalid identities would continue to pollute analytics.

## Supersedes

Extends SKMB-2026-09-03-012.

## Superseded By

None.
