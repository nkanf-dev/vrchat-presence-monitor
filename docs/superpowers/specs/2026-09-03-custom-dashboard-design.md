# Custom Dashboard Design

## Outcome

Add a first-class “自定义图表” analysis page where a tenant can assemble, configure,
reorder, resize, and save a dashboard from existing Presence Monitor data. It should
feel like a focused Grafana workspace while retaining the product's current visual
language and mobile behavior.

## Experience

The page opens with a useful default dashboard: current online count, tracked count,
status distribution, online-time ranking, daily changes, friend/hour heatmap, and
popular worlds. The toolbar controls the global range and refresh cadence. “编辑布局”
reveals drag handles and panel actions; “添加图表” opens a compact library of supported
visualizations. Each panel may override its title, range, result count, and self-account
inclusion where the underlying metric supports it.

Desktop uses a 12-column grid. Mobile presents the same panels as a single column and
never captures vertical touch scrolling for layout editing. Every chart has hover/tap
details, a concise accessible label, loading state, retained previous data, and an
isolated retry action.

## Persistence

The backend stores one bounded, schema-versioned dashboard document per tenant. GET
returns the saved document or a stable default. PUT uses an expected revision. A 409
conflict preserves the local draft and returns the current server version so the user
chooses whether to load it or overwrite it. Saving does not alter historical presence
or world data.

## Panel Types

- metric: current online players
- metric: tracked players
- donut: current status distribution
- horizontal bars: online-time ranking
- line: daily status changes
- heatmap: friend by hour online ratio
- horizontal bars: popular worlds

## Technical Shape

React Grid Layout owns desktop layout projection. Apache ECharts uses tree-shaken
modules and the SVG renderer for a moderate number of dashboard charts. TanStack Query
deduplicates existing read-model requests; the dashboard cadence invalidates only the
dashboard data prefix. The layout/config document is validated by Zod in the browser
and strict Pydantic models at the API boundary.
