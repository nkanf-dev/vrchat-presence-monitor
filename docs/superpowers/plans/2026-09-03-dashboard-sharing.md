# Dashboard Sharing And Exploration Implementation Plan

> **For Codex:** Implement directly on `main` as explicitly requested by the user. Preserve raw observations and create a production backup before cleanup or deployment.

**Goal:** Deliver a top-level, filterable, draggable dashboard with password-protected standalone sharing and owner-visible access audit.

**Architecture:** Extend the versioned dashboard document with bounded filter fields and new panel kinds. Keep React Grid Layout v2 as the mature desktop layout engine. Add a published dashboard-share snapshot with independent visitor grants and append-only audit events. Reject non-user Pipeline identities at normalization and remove only their derived projections in a backed-up transaction.

**Tech Stack:** FastAPI, SQLite, Pydantic, React 19, TypeScript, TanStack Query, React Grid Layout v2, Apache ECharts v6.

---

### Task 1: Correct identity and grid behavior

- Require `usr_` IDs in Pipeline presence normalization.
- Add a targeted cleanup method for existing notification projections without touching raw fetches.
- Fix the drag-handle cancellation bug, measure before mount, constrain the grid, and wrap the toolbar.

### Task 2: Promote and enrich the dashboard

- Move dashboard routing to its own top-level area.
- Add typed person/status/platform filters and searchable selectors.
- Add platform distribution and data-coverage visualizations while applying filters consistently.

### Task 3: Publish secure standalone shares

- Persist share snapshots, password hashes, grants, and append-only access events.
- Add owner create/update/revoke/audit APIs and restricted public unlock/data APIs.
- Render a standalone share route and share-management dialog.

### Task 4: Verify and release

- Run focused backend/frontend checks and production builds.
- Commit with Conventional Commits, push main, create a release, back up production, deploy, run targeted cleanup, and verify the public route plus stored counts.
