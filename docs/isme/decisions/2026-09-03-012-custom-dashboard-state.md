# SKMB-2026-09-03-012: Tenant Custom Dashboard State

- status: proposed
- decided_by: statistical_default
- approval_source: user requested a Grafana-like custom chart page and explicitly said “不用等我批准计划。用最先进最现代的方案”
- date: 2026-09-03
- commit: pending
- patterns:
  - B_state_persistence
  - C_concurrent_operations
  - E_security_boundary
  - F_fail_semantics
- scope: dashboard layout, panel configuration, refresh state, tenant persistence, mobile interaction, and save conflicts

## Decision

Each authenticated tenant owns one versioned dashboard document. The server derives
the tenant from the viewer session and never accepts a tenant selector in the
dashboard API. The document stores a bounded set of panel definitions, desktop grid
coordinates, global time range, and refresh cadence. Unknown document versions and
invalid panel definitions are rejected before storage.

Writes carry the last observed revision. A concurrent update returns the current
server document without deleting the browser draft; the user can load the server
version or explicitly overwrite it using that current revision. A failed panel
refresh retains its last rendered data and exposes recovery only inside that panel.

Desktop drag and resize are available only in explicit edit mode. Narrow screens use
a stable single-column projection and disable drag/resize so charts cannot trap page
scrolling. The active dashboard route and temporary range/refresh overrides are
represented in the URL and survive reload and browser navigation.

## Applies To

- `server/storage.py`
- `server/organization.py`
- `server/schemas.py`
- `server/app.py`
- `web/src/api.ts`
- `web/src/navigation.ts`
- dashboard components, queries, responsive CSS, and tests

## Rationale

A single dashboard gives the requested Grafana-like composition experience without
introducing folder, sharing, permission, or dashboard-list concepts that the product
does not yet need. Server persistence makes the layout available across devices,
while optimistic concurrency prevents silent multi-device data loss.

## Alternatives

- LocalStorage-only persistence was rejected because it loses the layout across devices.
- Last-write-wins saves were rejected because another device can silently overwrite a layout.
- Always-on dragging was rejected because it conflicts with selection and mobile scrolling.
- One backend endpoint per chart type was rejected because existing analytics read models already supply the required data.

## Supersedes

None.

## Superseded By

None.
