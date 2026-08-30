# Product UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Present the trusted data and new intelligence through a fast, deep-linkable, mobile-first interface that preserves every existing visualization.

**Architecture:** Keep React Query and hash-based navigation, but group the six legacy destinations under four durable product areas with aliases. Add isolated command search, player detail, and discovery components; remote responses remain Zod-validated. Shared accessible chart primitives provide sticky context, pinned touch inspection, and tabular equivalents without coupling page scroll to horizontal chart movement.

**Tech Stack:** React 19, TypeScript 7, Vite 8, TanStack Query 5, Zod 4, Lucide, CSS, Vitest, Testing Library

**Execution context:** Execute after both backend plans on the authorized `main` branch. Preserve the existing icon, visual language, session journey, and all current analytics.

---

### Task 1: Versioned frontend API contracts

**Files:**
- Modify: `web/src/api.ts`
- Modify: `web/src/api.test.ts`
- Create: `web/src/intelligence.ts`

- [ ] **Step 1: Write failing Zod contract tests for every new surface**

```typescript
it('rejects search results that omit tenant-safe navigation data', async () => {
  mockJson({ groups: { people: [{ id: 'usr_a', label: 'Alice' }] } });
  await expect(getSearch('alice')).rejects.toThrow();
});

it('accepts nullable activity ratios with evidence', () => {
  expect(activityCellSchema.parse({
    ratio: null,
    online_minutes: 0,
    observed_minutes: 12,
    eligible_minutes: 60,
    covered_days: 1,
    range_days: 30,
  }).ratio).toBeNull();
});
```

- [ ] **Step 2: Run API tests and verify failure**

Run: `npm --prefix web test -- --run src/api.test.ts`

Expected: TypeScript/import failures because intelligence schemas and functions are absent.

- [ ] **Step 3: Define strict schemas and exported domain types**

```typescript
export const coverageSchema = z.object({
  expected_minutes: z.number().nonnegative(),
  observed_minutes: z.number().nonnegative(),
  percentage: z.number().min(0).max(1).nullable(),
  first_observation: z.string().nullable(),
  last_observation: z.string().nullable(),
  level: z.enum(['complete', 'partial', 'insufficient', 'unknown']),
  gaps: z.array(z.object({ start: z.string(), end: z.string() })),
});

export const annotationSchema = z.object({
  friend_id: z.string(),
  note: z.string(),
  pinned: z.boolean(),
  revision: z.string(),
  updated_at: z.string(),
});
```

Add typed request helpers for search, insight, annotation, tags, preferences, discovery,
world library, and coverage. Keep all query parameters encoded through `URLSearchParams`.

- [ ] **Step 4: Add pure presentation helpers**

`web/src/intelligence.ts` exports coverage labels, location categories, evidence text,
and stable world color generation. It contains no React state or requests.

```typescript
export const ratioLabel = (ratio: number | null) =>
  ratio === null ? '—' : `${Math.round(ratio * 100)}%`;

export const locationLabel = (kind: LocationKind) => ({
  world: '可见世界',
  private: '私人位置',
  hidden: '位置隐藏',
  traveling: '切换世界中',
  offline: '离线',
  gap: '数据缺口',
}[kind]);
```

- [ ] **Step 5: Run contract and type checks**

Run: `npm --prefix web test -- --run src/api.test.ts`

Run: `npm --prefix web run check`

Expected: all new schemas reject malformed data and the project type-checks.

- [ ] **Step 6: Commit frontend contracts**

```bash
git add web/src/api.ts web/src/api.test.ts web/src/intelligence.ts
git commit -m "feat(web): add typed intelligence contracts"
```

### Task 2: Four-area navigation and complete URL restoration

**Files:**
- Modify: `web/src/navigation.ts`
- Modify: `web/src/components/AppShell.tsx`
- Modify: `web/src/App.tsx`
- Create: `web/src/components/AnalysisNav.tsx`
- Create: `web/src/components/MoreNav.tsx`
- Modify: `web/src/App.test.tsx`
- Create: `web/src/navigation.test.ts`
- Modify: `web/src/styles.css`

- [ ] **Step 1: Write failing alias, refresh, and mobile navigation tests**

```typescript
it.each([
  ['daily', 'analysis', 'daily'],
  ['worlds', 'analysis', 'worlds'],
  ['history', 'more', 'history'],
  ['data', 'more', 'data'],
])('migrates legacy %s hash', (legacy, area, section) => {
  expect(parseRoute(new URLSearchParams(`view=${legacy}`))).toMatchObject({ area, section });
});

it('renders exactly four mobile destinations', () => {
  renderAuthenticatedApp();
  expect(screen.getByLabelText('主要导航').querySelectorAll('a')).toHaveLength(4);
});
```

- [ ] **Step 2: Verify navigation tests fail**

Run: `npm --prefix web test -- --run src/navigation.test.ts src/App.test.tsx`

Expected: six destinations render and legacy hashes do not map to grouped sections.

- [ ] **Step 3: Implement typed route parsing with aliases**

```typescript
export type Area = 'online' | 'people' | 'analysis' | 'more';
export type AnalysisSection = 'daily' | 'relationships' | 'worlds' | 'discover';
export type MoreSection = 'history' | 'data' | 'settings';
export type Route = {
  area: Area;
  section?: AnalysisSection | MoreSection;
  person?: string;
  world?: string;
  tab?: string;
};

const legacy: Record<string, Route> = {
  overview: { area: 'online' },
  people: { area: 'people' },
  daily: { area: 'analysis', section: 'daily' },
  worlds: { area: 'analysis', section: 'worlds' },
  history: { area: 'more', section: 'history' },
  data: { area: 'more', section: 'data' },
};
```

Preserve unknown non-route parameters when updating one field. Keep existing `y`
scroll restoration and add route-specific scroll keys so closing a player returns to
the originating list position.

- [ ] **Step 4: Render the four areas and secondary navigation**

Use `Activity`, `Users`, `ChartNoAxesCombined`, and `Ellipsis` icons. `在线` remains
the default homepage and displays current online players first. Desktop secondary
navigation exposes analysis/more child sections; mobile uses compact in-page tabs.

- [ ] **Step 5: Run navigation and accessibility tests**

Run: `npm --prefix web test -- --run src/navigation.test.ts src/App.test.tsx`

Expected: four navigation items, legacy aliases, Back/Forward, focus, and scroll restoration pass.

- [ ] **Step 6: Commit information architecture**

```bash
git add web/src/navigation.ts web/src/navigation.test.ts web/src/components/AppShell.tsx web/src/components/AnalysisNav.tsx web/src/components/MoreNav.tsx web/src/App.tsx web/src/App.test.tsx web/src/styles.css
git commit -m "feat(web): organize the product into four areas"
```

### Task 3: Global search command surface

**Files:**
- Create: `web/src/components/GlobalSearch.tsx`
- Create: `web/src/components/GlobalSearch.test.tsx`
- Modify: `web/src/components/AppShell.tsx`
- Modify: `web/src/App.tsx`
- Modify: `web/src/styles.css`

- [ ] **Step 1: Write failing keyboard, mobile, cancellation, and navigation tests**

```typescript
it('opens with command-k and returns focus after escape', async () => {
  const user = userEvent.setup();
  renderSearch();
  const trigger = screen.getByRole('button', { name: '搜索' });
  trigger.focus();
  await user.keyboard('{Meta>}k{/Meta}');
  expect(screen.getByRole('dialog', { name: '全局搜索' })).toBeVisible();
  await user.keyboard('{Escape}');
  expect(trigger).toHaveFocus();
});

it('does not request for an empty query and cancels superseded requests', async () => {
  const user = userEvent.setup();
  renderSearch();
  await user.type(screen.getByRole('searchbox'), 'alice');
  await user.clear(screen.getByRole('searchbox'));
  expect(searchMock).toHaveBeenCalledTimes(1);
  expect(abortMock).toHaveBeenCalled();
});
```

- [ ] **Step 2: Verify search component tests fail**

Run: `npm --prefix web test -- --run src/components/GlobalSearch.test.tsx`

Expected: component import failure.

- [ ] **Step 3: Implement desktop palette and mobile full-screen sheet**

Use a visible top-bar button plus `Ctrl/⌘K`. Debounce non-empty queries by 180 ms,
pass `AbortSignal` to fetch, group results by people/worlds/history/destinations, and
support ArrowUp/ArrowDown/Enter/Escape. Empty query renders recent in-product
destinations from local storage; store only route strings, never result payloads.

```typescript
const query = useQuery({
  queryKey: ['search', deferred],
  queryFn: ({ signal }) => getSearch(deferred, signal),
  enabled: open && deferred.length > 0,
  staleTime: 30_000,
});
```

- [ ] **Step 4: Connect result navigation without losing browser history**

Person results navigate to `area=people&person=usr_…`; world results navigate to
`area=analysis&section=discover&world=wrld_…`; history shortcuts keep their query.
Close the sheet after route navigation and restore trigger focus only when staying on
the same route.

- [ ] **Step 5: Run search tests and check mobile layout**

Run: `npm --prefix web test -- --run src/components/GlobalSearch.test.tsx src/App.test.tsx`

Run: `npm --prefix web run check`

Expected: keyboard, cancellation, grouping, mobile close, and route navigation pass.

- [ ] **Step 6: Commit search UX**

```bash
git add web/src/components/GlobalSearch.tsx web/src/components/GlobalSearch.test.tsx web/src/components/AppShell.tsx web/src/App.tsx web/src/styles.css
git commit -m "feat(web): add tenant-wide command search"
```

### Task 4: Deep-linked player intelligence and organization

**Files:**
- Create: `web/src/views/PlayerDetailView.tsx`
- Create: `web/src/views/PlayerDetailView.test.tsx`
- Create: `web/src/components/AnnotationEditor.tsx`
- Create: `web/src/components/TagPicker.tsx`
- Modify: `web/src/views/PeopleView.tsx`
- Modify: `web/src/views/OverviewView.tsx`
- Modify: `web/src/App.tsx`
- Modify: `web/src/styles.css`
- Remove after route migration: `web/src/components/FriendDialog.tsx`

- [ ] **Step 1: Write failing deep-link, semantic, and conflict tests**

```typescript
it('restores the player and activity tab from the hash', async () => {
  window.location.hash = '#area=people&person=usr_a&tab=activity';
  renderAuthenticatedApp();
  expect(await screen.findByRole('heading', { name: 'Alice' })).toBeVisible();
  expect(screen.getByRole('tab', { name: '活动' })).toHaveAttribute('aria-selected', 'true');
});

it('labels first record without claiming friendship date', async () => {
  renderPlayer();
  expect(await screen.findByText('首次记录')).toBeVisible();
  expect(screen.queryByText('成为好友')).not.toBeInTheDocument();
});

it('preserves a note draft when the server reports a conflict', async () => {
  renderAnnotationConflict();
  expect(await screen.findByText('另一台设备保存了更新内容')).toBeVisible();
  expect(screen.getByDisplayValue('本地草稿')).toBeVisible();
  expect(screen.getByText('服务器内容')).toBeVisible();
});
```

- [ ] **Step 2: Verify player tests fail**

Run: `npm --prefix web test -- --run src/views/PlayerDetailView.test.tsx`

Expected: component import failure.

- [ ] **Step 3: Implement the five-tab player route**

Render Overview, Activity, Worlds, Names, and Notes & tags. Overview preserves current
profile/avatar/bio/location details and uses the real username plus a `自己` badge.
Activity shows range controls, online overlap, observed co-presence, coverage, gaps,
and weekday/hour heatmap. Worlds shows observed visits and dwell. Names lists
append-only changes. Notes & tags contains the revision-aware editor and tag picker.

- [ ] **Step 4: Implement resilient annotation autosave**

Debounce edits by 500 ms. Display `正在保存`, `已保存`, or `保存失败`. Keep the draft on
network error. On 409 show both values and actions `保留本地内容` and `采用服务器内容`.
After success expose an undo action that submits the previous text using the new
revision.

- [ ] **Step 5: Replace dialog callers with route navigation**

Overview and player lists set the `person` hash while preserving current filters and
scroll. Closing uses `history.back()` when opened from this session and otherwise
clears `person` with `replaceState`. Delete `FriendDialog.tsx` only after every current
profile feature is present in the route.

- [ ] **Step 6: Run player, people, overview, and navigation tests**

Run: `npm --prefix web test -- --run src/views/PlayerDetailView.test.tsx src/views/PeopleView.test.tsx src/App.test.tsx`

Expected: deep links, tabs, existing profile data, conflicts, tags, Back, and refresh pass.

- [ ] **Step 7: Commit player intelligence UX**

```bash
git add web/src/views/PlayerDetailView.tsx web/src/views/PlayerDetailView.test.tsx web/src/components/AnnotationEditor.tsx web/src/components/TagPicker.tsx web/src/views/PeopleView.tsx web/src/views/OverviewView.tsx web/src/App.tsx web/src/styles.css web/src/components/FriendDialog.tsx
git commit -m "feat(web): add deep-linked player intelligence"
```

### Task 5: Discovery and searchable world library

**Files:**
- Create: `web/src/views/DiscoverView.tsx`
- Create: `web/src/views/DiscoverView.test.tsx`
- Create: `web/src/components/WorldLibrary.tsx`
- Modify: `web/src/components/WorldDialog.tsx`
- Modify: `web/src/App.tsx`
- Modify: `web/src/styles.css`

- [ ] **Step 1: Write failing ranking, filtering, stale-cache, and modal tests**

```typescript
it('explains hot-world ranking with component statistics', async () => {
  renderDiscover();
  const card = await screen.findByRole('button', { name: /Chinese Bar/ });
  expect(within(card).getByText('4 位玩家')).toBeVisible();
  expect(within(card).getByText('7 次访问')).toBeVisible();
  expect(within(card).getByText('5.2 小时')).toBeVisible();
});

it('retains cached world content while refresh is stale', async () => {
  renderWorldWithStaleMetadata();
  expect(await screen.findByText('English Hub')).toBeVisible();
  expect(screen.getByText('资料更新暂时延迟')).toBeVisible();
  expect(screen.queryByText('正在解析世界')).not.toBeInTheDocument();
});
```

- [ ] **Step 2: Verify discovery tests fail**

Run: `npm --prefix web test -- --run src/views/DiscoverView.test.tsx`

Expected: components are absent.

- [ ] **Step 3: Implement range, friend/tag/world filters**

Discovery defaults to 7 days, offers 30 days, includes self by default, and exposes
friend/tag filters plus self inclusion toggle. Cards show unique players, visits,
covered dwell, returns, last observed, and prior-period delta without a hidden score.
Private/hidden/traveling time appears in a separate coverage summary.

- [ ] **Step 4: Implement the world library and deep links**

Library supports title/author/world ID search, friend/tag filters, cursor pagination,
last visit, thumbnail, and resolution status. Selecting a card sets `world=wrld_…`.
`WorldDialog` becomes a responsive route-aware sheet with a sticky close button,
safe-area padding, Escape, focus trap, focus restoration, and preserved cached data.

- [ ] **Step 5: Run discovery and world regression tests**

Run: `npm --prefix web test -- --run src/views/DiscoverView.test.tsx src/App.test.tsx`

Run: `npm --prefix web run check`

Expected: filters, pagination, modal reachability, stale metadata, Back, and deep links pass.

- [ ] **Step 6: Commit discovery UX**

```bash
git add web/src/views/DiscoverView.tsx web/src/views/DiscoverView.test.tsx web/src/components/WorldLibrary.tsx web/src/components/WorldDialog.tsx web/src/App.tsx web/src/styles.css
git commit -m "feat(web): add observed world discovery"
```

### Task 6: Touch-readable accessible charts

**Files:**
- Create: `web/src/components/ChartDataTable.tsx`
- Create: `web/src/components/ChartInteraction.ts`
- Create: `web/src/components/ChartInteraction.test.ts`
- Modify: `web/src/components/ChartViewport.tsx`
- Modify: `web/src/components/PresenceCharts.tsx`
- Modify: `web/src/components/WorldCharts.tsx`
- Modify: `web/src/views/DailyView.tsx`
- Modify: `web/src/views/WorldsView.tsx`
- Modify: `web/src/styles.css`

- [ ] **Step 1: Write failing pointer, evidence, and table tests**

```typescript
it('pins a touch selection and clears it on a second tap', () => {
  const first = reduceChartSelection(null, { type: 'tap', row: 2, column: 8 });
  expect(first).toEqual({ row: 2, column: 8, pinned: true });
  expect(reduceChartSelection(first, { type: 'tap', row: 2, column: 8 })).toBeNull();
});

it('shows unavailable instead of zero when evidence is insufficient', () => {
  renderHeatmap({ ratio: null, observed_minutes: 12, eligible_minutes: 60 });
  expect(screen.getByText('—')).toBeVisible();
  expect(screen.getByText('已观测 12 分钟，证据不足')).toBeVisible();
});

it('provides a table with the same chart values', () => {
  renderTimeline();
  expect(screen.getByRole('button', { name: '查看数据表' })).toBeVisible();
  expect(screen.getByRole('table', { hidden: true })).toBeInTheDocument();
});
```

- [ ] **Step 2: Verify chart tests fail**

Run: `npm --prefix web test -- --run src/components/ChartInteraction.test.ts src/App.test.tsx`

Expected: interaction reducer/table are absent and touch is ignored by existing SVG handlers.

- [ ] **Step 3: Implement shared pointer state and evidence tooltips**

`ChartInteraction.ts` owns pure hover/focus/tap state. Mouse/pen hover remains
transient; touch/keyboard selection is pinned. Every selected timeline renders the
vertical line, active row background, person/time/state/world details, and coverage.
Every heatmap tooltip renders ratio or evidence-insufficient text plus observed,
eligible, covered-day, and range-day values.

- [ ] **Step 4: Preserve vertical scroll and sticky context**

Use `touch-action: pan-x pan-y pinch-zoom` on the chart viewport and horizontal
`overflow-x:auto` on `ChartViewport`. Render row labels in a sticky overlay column and
hour labels in a sticky overlay header synchronized to `scrollLeft`. Show the mobile
hint once per chart route. Do not call `preventDefault()` for vertical pointer moves.

- [ ] **Step 5: Add equivalent collapsible tables**

Timeline tables contain player, start, end, duration, status, location/world, and
coverage. Heatmap tables contain player plus 24 evidence cells. Use native `<table>`,
`<th scope="row">`, and a summary. Keep tables collapsed visually but in DOM only
after the user expands them to avoid an enormous accessibility tree on initial load.

- [ ] **Step 6: Run chart, touch, and existing analytics tests**

Run: `npm --prefix web test -- --run src/components/ChartInteraction.test.ts src/App.test.tsx`

Run: `npm --prefix web run check`

Expected: touch selection, row highlight, evidence details, table equivalence, and type checks pass.

- [ ] **Step 7: Commit chart UX**

```bash
git add web/src/components/ChartDataTable.tsx web/src/components/ChartInteraction.ts web/src/components/ChartInteraction.test.ts web/src/components/ChartViewport.tsx web/src/components/PresenceCharts.tsx web/src/components/WorldCharts.tsx web/src/views/DailyView.tsx web/src/views/WorldsView.tsx web/src/styles.css
git commit -m "fix(web): make analytics readable on touch devices"
```

### Task 7: Stable health, empty, loading, and error states

**Files:**
- Modify: `web/src/components/StatusBanner.tsx`
- Modify: `web/src/components/StatusBanner.test.tsx`
- Create: `web/src/components/PanelState.tsx`
- Modify: `web/src/App.tsx`
- Modify: `web/src/views/OverviewView.tsx`
- Modify: `web/src/styles.css`

- [ ] **Step 1: Write failing stale-state and error-deduplication tests**

```typescript
it('does not call stale data current online', () => {
  render(<StatusBanner overview={staleOverview} refreshFailed={false} onRetry={noop} onReconnect={noop} />);
  expect(screen.getByText(/最后已知状态/)).toBeVisible();
  expect(screen.queryByText('实时')).not.toBeInTheDocument();
});

it('renders one categorized status when several requests fail', () => {
  renderAppWithRepeatedNetworkFailures();
  expect(screen.getAllByRole('status', { name: '连接状态' })).toHaveLength(1);
  expect(screen.queryByText(/SSL|curl|stack/i)).not.toBeInTheDocument();
});
```

- [ ] **Step 2: Verify state tests fail**

Run: `npm --prefix web test -- --run src/components/StatusBanner.test.tsx src/App.test.tsx`

Expected: stale/current wording and categorized single status do not match.

- [ ] **Step 3: Implement one stable global status and isolated panel states**

Map browser-unreachable, VRChat service, session-expired, collector failure, stale,
and fresh states to concise copy. Keep existing panel data during background refresh.
`PanelState` distinguishes true zero, no observation coverage, filters excluding all,
future time, initial loading, and local retry. Only session expiration opens reconnect.

- [ ] **Step 4: Run state and full frontend tests**

Run: `npm --prefix web test`

Run: `npm --prefix web run build`

Expected: all frontend tests pass and the production bundle builds without layout regressions.

- [ ] **Step 5: Commit recoverable states**

```bash
git add web/src/components/StatusBanner.tsx web/src/components/StatusBanner.test.tsx web/src/components/PanelState.tsx web/src/App.tsx web/src/views/OverviewView.tsx web/src/styles.css
git commit -m "fix(web): keep failures contextual and recoverable"
```
