# Read-only intelligence release design

## Objective

Turn Presence Monitor from a reliable presence recorder into a useful read-only
VRChat intelligence product without copying VRCX's account-management surface. The
release adds friend relationship insights, personal organization, global search,
and world discovery while preserving the product's existing login journey, current
presence dashboard, daily timelines, world timelines, history, and tenant-scoped
backup flow.

The release target is `v0.3.0-beta.1`. It remains a small-group, single-node beta;
the new functionality must not add per-friend VRChat requests, increase the normal
REST polling cadence, or weaken current session persistence and tenant isolation.

## Product boundary

Presence Monitor remains read-only with respect to VRChat. It may mutate only
product-owned data such as notes, tags, pins, saved filters, and imports. It does not
send invites, change social status, manage friends, avatars, groups, or favorites,
write Discord Rich Presence, expose a VR overlay, or automate the VRChat client.

This boundary keeps the product differentiated:

- VRCX remains the broad desktop companion for performing VRChat actions.
- Presence Monitor remains the always-on, self-hosted, mobile-friendly system for
  reviewing presence, relationships, and world activity over time.

Notifications, Web Push, customizable dashboards, and cross-tenant/community
aggregation are explicitly deferred. They require separate attention, delivery,
and privacy semantics and are not prerequisites for this release.

The release is delivered in two ordered slices. **P0 trust work** fixes observation
gaps, future-event quarantine, per-person heatmap denominators, state semantics,
mobile chart inspection, and product wording. **P1 product work** then adds search,
friend intelligence, notes/tags/pins, the world library, and discovery. P1 may not
ship on analytics that still fail a P0 acceptance case.

## User-visible scope

### Information architecture

Do not add more top-level mobile destinations to the existing six-item bar. Replace
it with four stable destinations:

- `在线` — the current-online homepage and status summary;
- `玩家` — the player list, organization, and player detail routes;
- `分析` — tabs for daily activity, relationship activity, world timelines, and
  discovery;
- `更多` — complete status history, data ownership, import/export, and settings.

Desktop may expose the child destinations in a secondary navigation region. Global
search is a persistent top-bar action and never consumes a navigation slot. Existing
hash URLs are migrated through aliases so bookmarks continue to open the equivalent
new destination. The URL retains selected person/world, date ranges, filters, active
tab, pagination, and scroll anchor across refresh and Back/Forward navigation.

The signed-in VRChat account remains a first-class tracked person in every global
list and analytic. It uses the account's real display name with a secondary `自己`
badge, never the replacement label `我`, and follows the same offline/location and
coverage rules as every friend.

### Global search

Add a command palette opened by `Ctrl+K` or `Command+K`, plus a visible search
button in the desktop and mobile top bars. It searches only the signed-in tenant and
groups results into:

- people, including current and historical names;
- personal notes and tags;
- resolved worlds observed in this tenant's history;
- shortcuts into filtered status history and existing analytics views.

Pasted `usr_…` and `wrld_…` identifiers, plus supported VRChat user/world URLs, are
recognized without requiring an exact display-name match. Search never retrieves an
unknown user or world from VRChat as a side effect; it searches tenant-observed data
and explains when an identifier has not been observed.

Desktop search supports keyboard navigation and returns focus to the invoking
control. Mobile search is a full-height sheet with an explicit close control and
does not trap vertical page scrolling. Queries are debounced, cancellable, bounded,
and never scan raw response bodies. Empty queries show recent in-product destinations
rather than sending a server request. Recent queries, if retained, stay in the
browser and contain no result payload.

### Friend intelligence

The existing friend dialog becomes a stable deep-linked player route. Desktop can
present it as a wide detail pane while narrow screens use a full-screen page; neither
is an oversized, non-addressable modal. The URL hash stores the friend ID and active
tab so refresh, Back, and shared internal links restore the same context. Opening and
closing it preserves the originating list filters and scroll position.

The surface has five tabs:

1. **Overview** — current state, public profile, first recorded date, latest observed
   activity, and coverage.
2. **Activity** — online activity, observed overlap with the signed-in user's own
   activity, observed co-presence, and a selected date range.
3. **Worlds** — most visited worlds, observed visits, dwell time, and last observed
   visit.
4. **Names** — the append-only current and historical name record.
5. **Notes & tags** — pin, private note, and tenant-local organization controls.

The activity tab also includes this person's `weekday × hour` activity heatmap and
peak observed periods. No view invents a relationship or intimacy score.

Terminology must not claim knowledge the data cannot prove:

- `首次记录` means the first record in Presence Monitor, not the friendship date.
- `最近在线` means the latest observed online state, not the last time two people met.
- `在线重叠` means both accounts were observably online during the same interval.
- `共同游玩` requires overlapping intervals with the same parseable world and
  instance location. Generic values such as `private`, `traveling`, `offline`, or an
  unresolved location never count as co-presence.
- Every duration is qualified by the selected observation range and data coverage.

### Notes, tags, and pins

Users can maintain one private note, a pinned state, and multiple tenant-local tags
for each tracked person. Tags have a name and optional color, but every UI state also
uses text or an icon so color is never the only distinction.

Notes use optimistic autosave with a visible `正在保存 / 已保存 / 保存失败` state.
Failure retains the draft and offers retry; closing the dialog never silently drops
unsaved text. A successful note edit offers a short local undo action. Tag creation
is inline, duplicate names are rejected case-insensitively, and removing a tag from
a friend does not delete the tag from other friends. Deleting a tag requires
confirmation and removes only that tenant's assignments in one transaction.
Autosave includes the last acknowledged revision. A conflicting edit from another
device returns a conflict response and preserves both the local draft and the newer
server text so the user can choose which to keep.

### World discovery

Add a `发现` view based entirely on already recorded world intervals. It contains:

- friend-circle hot worlds for 7- and 30-day ranges;
- unique observed visitors, visits, observed dwell time, and return visits;
- recently rising worlds compared with the immediately preceding equal-length range;
- filters for friend/tag and a direct link to the existing world timeline.

The signed-in account is included by default, consistently with other global
analytics, and can be excluded with an explicit filter.

The ranking exposes its component statistics rather than presenting an unexplained
score. Only parseable world IDs are ranked. Private/offline/traveling categories are
reported separately as unresolvable observation time and never merged into a world.
World cards reuse the existing resolver, thumbnail proxy, world dialog, and stable
world color mapping.

The same analysis area exposes a searchable **world library** of every resolved
world observed by the tenant. It supports title, author, world ID, friend/tag, and
last-visited filters. Resolver refresh failures retain the last successful title,
thumbnail, and metadata with a stale marker; they never replace useful content with
`正在解析世界`.

## Data semantics and confidence

Analytics must distinguish an observed state from absence of observations. Each
complete REST synchronization records one authoritative tenant-scoped collection
sample; failure state transitions are recorded once rather than on every retry.
Samples include timestamp, source, outcome, expected cadence, friend count, online
count, duration, and a stable error category; they do not contain credentials or raw
bodies. Partial or paginated failures are not authoritative samples.

Two consecutive authoritative samples define an observed window only when their
distance is no more than `max(2 × expected cadence + 60 seconds, 10 minutes)`. After
the last authoritative sample, status can extend only to that same threshold. Later
time is unknown until another successful sample arrives. Pipeline transitions can
shorten or divide an observed span, but pipeline silence alone cannot prove coverage.
External collectors must report an equivalent expected cadence and authoritative
snapshot outcome.

An authoritative complete friend list also drives append-only per-person tracking
transitions. A person's denominator begins only when that person is first confirmed
in a complete snapshot and ends when a later complete snapshot confirms absence.
Historical imports without this evidence remain `覆盖率未知`; they are never treated
as fully observed.

The interval engine splits status and world spans at collection gaps. As soon as the
same cadence-derived threshold expires, the homepage changes `当前在线` to
`最后已知状态 · 截至 HH:MM` and removes live/green semantics; with the production
default cadence this occurs before 15 minutes. A missing collector window is
rendered as a hatched `数据缺口`, not as offline and not as a continuation of the
previous world. Each date-range response includes:

- expected and observed duration;
- coverage percentage;
- first and last usable observation;
- gap intervals;
- one of `完整`, `部分`, or `数据不足`.

Coverage labels are descriptive, not hidden weighting. The UI continues to show
useful partial results while making the gaps visible in charts, tooltips, summaries,
and exports.

Every average-activity heatmap cell has its own denominator:

`this person's observed online minutes in this hour ÷ this person's total observed
minutes in this hour across eligible dates`.

Eligible minutes are the intersection of the requested local hour, authoritative
collection windows, and that person's tracking periods. A denominator below the
minimum of `max(30 minutes, 10% of that cell's eligible tracked minutes)` is `—`,
never `0%`. Hover, focus, or tap reports both the value and evidence, for example
`在线 32% · 覆盖 18/30 天 · 已观测 742 分钟`. It also states when tracking began
inside the selected range.

Location and status normalization follows one shared backend rule used by current
presence and every analytic:

- `location=offline` is offline even when the incoming status says active;
- `private` is `私人位置`, rendered gray;
- online without a parseable world ID is `位置隐藏`, rendered with a neutral pattern;
- `traveling` is `切换世界中`, rendered with a blue-gray dashed pattern;
- a collection gap is `数据缺口`, with a different warning pattern;
- future time that has not occurred is blank and cannot contribute a span.

Color is never the only carrier of these states. Text, icon, or texture remains
available in legends and detailed values.

Events whose timestamp exceeds ingestion time by more than five minutes are
preserved in the append-only status history, in raw fetch storage when available,
and in an anomaly ledger, but excluded from current state, timelines, rankings,
heatmaps, and discovery. They do not become valid merely because wall-clock time
later passes them. An explicit, audited data repair can supersede an anomaly without
deleting the original event.

Collection samples complement rather than replace raw capture. Every collector data
request, including categorized failures, keeps its response or error envelope in the
existing tenant-scoped append-only raw store with fetch time and request kind.
Authentication requests, cookies, credential fields, and authorization headers are
never written there. Normal analytics do not read raw bodies on the request path.

Name history is append-only. When a normalized friend snapshot changes `username`
or `display_name`, the same transaction stores an identity event with old value, new
value, timestamp, and source. Initial imports establish a baseline and do not invent
a rename event.

Every tenant has an IANA time zone. First login proposes the browser's time zone;
the user can change it under `更多`. Dates, hourly buckets, tooltips, exports, and
range boundaries display the active zone. DST days use their actual 23/24/25-hour
length rather than assuming 1,440 minutes.

## Storage model

Add versioned, idempotent migrations for these tenant-scoped tables:

- `friend_annotations(tenant_id, friend_id, note, pinned, updated_at)`;
- `tags(tenant_id, id, name, color, created_at, updated_at)`;
- `friend_tags(tenant_id, friend_id, tag_id, created_at)`;
- `friend_identity_events(tenant_id, event_id, friend_id, field, old_value,
  new_value, occurred_at, source)`;
- `friend_tracking_events(tenant_id, event_id, friend_id, tracked, occurred_at,
  source)`;
- `collection_samples(tenant_id, sample_id, observed_at, source, outcome,
  authoritative, expected_interval_seconds, friend_count, online_count, duration_ms,
  error_category)`;
- `event_anomalies(tenant_id, anomaly_id, event_kind, event_id, reason,
  detected_at)`;
- `tenant_preferences(tenant_id, timezone, updated_at)`.

The existing `raw_fetches` table gains a tenant-scoped stable `client_fetch_id`.
Existing rows receive deterministic IDs from request metadata plus body/error hashes,
allowing repeated imports to remain idempotent without trusting local integer IDs.

All foreign keys include the tenant boundary. Every query derives `tenant_id` from
the authenticated viewer or collector; IDs in paths cannot select a different
tenant. Identity, tracking, collection, and anomaly events are append-only.
Annotation, tag, and preference records are intentionally mutable product data.
Tag names have a case-insensitive unique constraint per tenant. Friend annotations
reference the composite `(tenant_id, friend_id)` key, and tag deletion cascades only
through same-tenant assignment rows.

The portable backup format advances to version 3 and includes annotations, tags,
tag assignments, identity/tracking events, collection samples, anomalies, and tenant
preferences. Version 1 and 2 imports remain supported. Imports are merge-only:
append-only IDs are idempotent, newer annotations win by normalized timestamp, and
no import can remove existing data. Imported legacy history remains readable but
reports unknown coverage until native observation evidence begins.

The default `完整备份` also includes raw fetch records and is streamed as gzip JSON so
export size does not require materializing the tenant dataset in application memory.
A clearly labeled `轻量数据导出` may omit raw bodies for spreadsheet-style portability.
Version 3 import streams raw records into tenant-scoped staging tables, validates
schema, stable IDs, expansion limits, and credential exclusions, then performs one
atomic merge. A failed validation leaves live tenant data unchanged.

The migration backfills deterministic collection samples and future-event anomalies
from existing raw fetch metadata wherever a complete successful snapshot can be
proved. It never rewrites or deletes historical status events. Existing histories
without adequate raw evidence remain visible with unknown coverage; after migration,
previously unbounded online/world spans are recomputed against the recovered windows
and stop at real gaps.

## APIs and components

Add focused endpoints rather than one oversized dashboard response:

- `GET /v1/search?q=&limit=`;
- `GET /v1/friends/{friend_id}/insights?from=&to=`;
- `GET /v1/friends/{friend_id}/annotation`;
- `PUT /v1/friends/{friend_id}/annotation`;
- tenant-scoped tag list/create/update/delete and friend-tag assignment endpoints;
- `GET /v1/discovery/worlds?days=&friend_id=&tag_id=`;
- `GET /v1/world-library?q=&author=&friend_id=&tag_id=&cursor=`;
- `GET /v1/coverage?from=&to=`;
- `GET/PUT /v1/preferences` for the tenant time zone;
- bootstrap-authenticated `GET /v1/admin/health/tenants` returning only compact
  sync age, account state, categorized error, and recent request success counts.

Public `/readyz` remains generic. The operator endpoint never returns raw fetch
bodies, cookies, profile biographies, notes, or tokens.

Collection health uses stable user-facing categories: this site is unreachable,
VRChat reports a service failure, the VRChat login session expired, or this tenant's
collector is unhealthy. Low-level SSL, curl, stack, and response-body text stays in
redacted operator logs and is never sent to the normal dashboard.

The server separates five modules with narrow interfaces:

- collection sampling and identity-event recording;
- interval and coverage calculation;
- friend insights;
- world discovery;
- tenant search and personal organization.

React Query owns remote caching and invalidation. Zod validates every new response.
The global search, player detail route, and discovery cards remain independent
components; the main `App` coordinates navigation but does not absorb their internal
state.

## Performance and VRChat API discipline

All new user-facing analytics are derived from hosted SQLite. They must cause zero
additional VRChat requests during ordinary browsing. World metadata resolution keeps
the existing global cache but adds single-flight request coalescing, bounded
concurrency, positive and negative cache TTLs, `Retry-After` handling, and per-world
backoff. The same world ID cannot be resolved concurrently or retried in a tight
loop. A page request returns cached metadata or the world ID immediately; unresolved
IDs enter the collector's background resolver queue, so opening search, a player, or
Hot Worlds never synchronously calls VRChat.

Search operates over compact tenant-owned entities and capped result sets, not the
million-row raw table. The initial implementation may use indexed, parameterized
SQLite queries because the supported deployment is small-group and single-node; its
storage interface must permit an FTS or PostgreSQL adapter later without changing
the HTTP contract.

Analytics caches are keyed by tenant, time zone, date range, relevant filters, event
revision, friend/tracking revision, identity revision, anomaly revision, and
collection-sample revision. Cache invalidation is revision-based so append-only
history stays cacheable while current-day results refresh. Target response budgets
on a sanitized production-scale fixture are 300 ms for search, 1 second for a cold
30-day insight/discovery query, and 150 ms for a warm cached query.

## Error handling and UX states

- Already rendered data remains visible during refresh failure.
- One stable status surface reports connection or collection problems; repeated
  network failures do not create stacked toasts.
- A failed panel has a local retry control and does not blank unrelated panels.
- Login expiration is distinct from a transient VRChat/API timeout.
- Empty results explain whether no activity exists, filters excluded it, or coverage
  is insufficient.
- Loading uses shape-matched skeletons without layout jumps.
- Charts retain vertical page scrolling on touch devices and expose the same details
  through focus, hover, and tap.
- Tapping a heatmap cell or timeline segment pins the vertical cursor, row highlight,
  and detail card; tapping it again or outside clears the selection.
- Row labels remain sticky while a chart scrolls horizontally, time labels remain
  sticky while a long chart scrolls vertically, and mobile charts show one concise
  `左右滑动查看全天` affordance.
- Every complex chart has a short explanation and a collapsible, keyboard-readable
  table containing the same values.
- World/profile dialogs always expose a reachable close button, Escape behavior,
  focus restoration, and safe-area padding.
- Empty panels distinguish a true zero, no observation coverage, filters excluding
  all results, and future time not yet reached.

## Testing and independent UX review

An independent pre-implementation review found four release blockers: stale spans
being extended through collection outages, shared rather than per-person heatmap
denominators, touch charts that cannot expose desktop-level detail, and six-item
mobile navigation that cannot absorb more features. Those findings are incorporated
as P0 requirements above. The same reviewer role is repeated after implementation;
the author of a feature cannot be its only UX approver.

Backend tests cover migrations from every released schema, import/export v1-v3,
tenant isolation, annotation conflicts, identity-event idempotency, collection gaps,
future-event quarantine, per-person tracking denominators, timezone/DST boundaries,
overlap and co-presence semantics, hot-world ranking, world resolver
single-flight/backoff, and operator-health redaction.

Frontend tests cover keyboard and mobile search, deep-linked friend tabs, note save
recovery, tag operations, gap rendering, world filters, loading/empty/error states,
touch-pinned chart inspection, sticky headers/labels, tabular chart alternatives,
focus management, Back/refresh restoration, and response-schema rejection. Existing
login, current presence, daily, world, history, and backup tests remain release gates.

An independent UX reviewer evaluates the candidate from three perspectives:

1. a returning mobile user checking who is online;
2. a user researching one friend and their shared activity;
3. a power user searching and organizing a larger friend list.

The review produces P0/P1/P2 findings. All P0 and P1 findings must be fixed before
release; features cannot be silently removed to clear the list. The reviewer repeats
the key flows against the final deployed candidate; a source-only review is not
sufficient.

The release candidate is also checked at 360, 390, and 430 CSS pixels, 200% browser
zoom, keyboard-only input, reduced motion, and a screen reader. Vertical swipes that
begin inside any chart must continue scrolling the page, while horizontal swipes
remain available inside the chart viewport.

## README and product presentation

Rewrite the README around the shipped product, not its implementation history. The
top section contains the product promise, one representative desktop/mobile visual,
the hosted demo link, and concise badges for CI, CodeQL, latest release, license, and
the published GHCR image. Badges must point to real workflows or artifacts and must
not make unsupported uptime, privacy, or platform claims.

The remaining order is:

1. what users can learn;
2. screenshots and the read-only product boundary;
3. self-hosted quick start;
4. data ownership, import/export, and verified backups;
5. architecture and operational limits;
6. development and release verification.

Text stays concrete and concise. It avoids defensive promises, generic feature
marketing, fake testimonials, roadmap padding, and duplicated security prose.
User-facing copy consistently says `一次登录，服务器持续记录`; legacy local-bridge or
access-code language may remain only in clearly labeled compatibility documentation,
never in the primary journey. README, application navigation, screenshots, and
release notes use the same feature names.

## Release and deployment

The release is published from `main` as `v0.3.0-beta.1` using the existing immutable
release workflow. Conventional Commits are grouped by user-visible slice, for
example `feat(storage)`, `feat(insights)`, `feat(search)`, `feat(discovery)`,
`feat(web)`, `docs(readme)`, and targeted `fix(...)` commits from UX review. Commits
must remain independently understandable and must not mix generated dependencies
with feature logic.

Deployment is blocked until:

1. the local checkout exactly follows the public `main` lineage used in production;
2. CI, CodeQL, Python, React, container, migration, isolation, and backup checks pass;
3. a fresh production SQLite snapshot is integrity-checked, uploaded to R2,
   downloaded, and restore-verified without replacing the live database;
4. production tenant/friend/event counts and latest-sync timestamps are recorded;
5. the independent UX review has no open P0/P1 findings;
6. the candidate image is published and its digest is recorded;
7. the server deploys that immutable version and reports healthy containers,
   successful migrations, current tenant sync, zero new 429 loops, and a healthy
   public tunnel.

Rollback keeps the pre-deploy database snapshot and previous immutable image. A
failed schema migration leaves the old containers and database untouched. A runtime
regression stops the new containers, restores the previous image and, only if the
migration wrote incompatible data, restores into a separate verified database file
before atomic replacement. Production data is never deleted to make a deployment
succeed.

## Success criteria

- Existing users retain their browser and VRChat sessions across the upgrade.
- A successful login for the same VRChat user ID reuses the existing tenant and
  atomically refreshes its encrypted upstream session; it never creates a duplicate
  tenant or interrupts the working collector before the new login completes.
- VRChat passwords remain request-only and are never persisted or included in raw
  capture, logs, backups, or exports.
- Current presence continues to refresh at the existing cadence with no additional
  VRChat calls caused by search or analytics browsing.
- Gaps are never counted as offline, online, or co-presence.
- Collection stopped for 15 minutes cannot add online minutes, and stale current
  state is visibly labeled as last known rather than live.
- Every average heatmap denominator is scoped to person × local hour × observed
  tracking time; insufficient evidence is shown as `—`.
- Future/anomalous events cannot enter current state, timelines, rankings, or Hot
  Worlds.
- Search, annotations, identity history, insights, and discovery remain strictly
  tenant-scoped and survive export/import.
- `nkanf` and `gerasilence` continue minute-level collection after deployment, and
  the legacy imported tenant remains readable without fabricated raw-fetch metrics.
- Public readiness, hourly local backup, off-site upload, and downloaded restore
  drill remain healthy.
- README badges and screenshots resolve from the public repository and describe the
  released behavior accurately.
- On touch devices, every timeline/heatmap value is inspectable without blocking
  vertical page scrolling or losing the active person/time context.

## Product references

- [VRCX feature set](https://github.com/vrcx-team/VRCX)
- [VRCX v2026.05.03 release](https://github.com/vrcx-team/VRCX/releases/tag/v2026.05.03)
- [W3C complex-image accessibility](https://www.w3.org/WAI/tutorials/images/complex/)
- [WCAG 2.2: use of color](https://www.w3.org/WAI/WCAG22/Understanding/use-of-color)
