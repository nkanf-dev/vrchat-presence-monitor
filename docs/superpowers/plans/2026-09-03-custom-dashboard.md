# Custom Dashboard Implementation Plan

> **For Codex:** Implement directly on `main` as explicitly requested by the user. Preserve all existing local and production data.

**Goal:** Ship a tenant-persisted Grafana-like custom dashboard using the product's existing analytics read models.

**Architecture:** Add a revisioned JSON dashboard document beside tenant preferences, expose GET/PUT endpoints through the organization service, then render the document with a responsive React Grid Layout and tree-shaken ECharts panels. Existing analytics APIs remain the source of chart data.

**Tech Stack:** FastAPI, SQLite, Pydantic, React 19, TypeScript, TanStack Query, React Grid Layout v2, Apache ECharts v6, Vitest, pytest.

---

### Task 1: Persist dashboard documents

**Files:** `server/storage.py`, `server/schemas.py`, `server/organization.py`, `server/app.py`, `tests/test_hosted_organization.py`

- Add the tenant-owned dashboard table without touching existing historical tables.
- Validate the bounded version-1 document and return a stable default when absent.
- Implement revision-aware GET/PUT and a 409 response carrying the server version.
- Cover save, conflict, invalid input, and tenant isolation.

### Task 2: Add the dashboard client model and route

**Files:** `web/src/api.ts`, `web/src/navigation.ts`, `web/src/components/AnalysisNav.tsx`, `web/src/App.tsx`

- Add strict Zod models and client methods.
- Register the analysis route and preserve URL state.
- Connect global refresh invalidation to dashboard data.

### Task 3: Build the chart workspace

**Files:** `web/src/views/DashboardView.tsx`, `web/src/components/DashboardPanel.tsx`, `web/src/components/EChart.tsx`, `web/src/styles.css`, `web/package.json`

- Install React Grid Layout and ECharts.
- Implement the responsive edit/view grid, chart library, panel editor, duplicate/delete, save/conflict recovery, global range, and refresh cadence.
- Implement seven panel renderers using existing read models and isolated query errors.
- Keep mobile single-column scrolling and provide useful chart labels/tooltips.

### Task 4: Verify and deliver

**Files:** frontend/backend tests, release metadata as needed

- Run focused backend and frontend checks, then production builds.
- Apply independent subagent findings that reveal P0/P1 issues.
- Commit with Conventional Commits, push `main`, back up production data, deploy, and verify the public page and tenant persistence.
