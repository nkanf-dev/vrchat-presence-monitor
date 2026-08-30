# Observation Trust Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every current-state and historical statistic evidence-bounded, tenant-scoped, recoverable, and compatible with existing data.

**Architecture:** Add append-only observation, tracking, identity, and anomaly ledgers to hosted SQLite, then route interval analytics through a pure coverage engine. The hosted collector writes authoritative full-snapshot samples while pipeline events remain partial transitions. Portable backup v3 streams the complete tenant dataset, including raw fetches, through validated staging before an atomic merge.

**Tech Stack:** Python 3.11+, FastAPI, SQLite WAL, `unittest`, React backup worker, gzip JSON

**Execution context:** The designer explicitly authorized direct work on `main`; do not create a worktree and do not replace production data or secrets.

---

### Task 1: Versioned observation schema and stable raw IDs

**Files:**
- Create: `tests/test_observation_storage.py`
- Modify: `server/storage.py`
- Modify: `scripts/backup_format.py`

- [ ] **Step 1: Write the failing migration and idempotency tests**

```python
class ObservationStorageTests(unittest.TestCase):
    def test_init_creates_tenant_scoped_observation_tables(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(str(Path(directory) / "hosted.sqlite3"))
            with store.connection() as db:
                tables = {
                    row[0]
                    for row in db.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
                self.assertTrue(
                    {
                        "collection_samples",
                        "friend_annotations",
                        "tags",
                        "friend_tags",
                        "friend_tracking_events",
                        "friend_identity_events",
                        "event_anomalies",
                        "tenant_preferences",
                        "world_resolution_state",
                    }.issubset(tables)
                )

    def test_existing_raw_fetch_gets_stable_tenant_scoped_id(self):
        store, tenant_id = seeded_store()
        store.record_raw_fetch(tenant_id, "GET", "/auth/user/friends", 200, "application/json", b"[]", "")
        with store.connection() as db:
            row = db.execute(
                "SELECT client_fetch_id FROM raw_fetches WHERE tenant_id=?",
                (tenant_id,),
            ).fetchone()
        self.assertRegex(str(row[0]), r"^fetch_[0-9a-f]{64}$")
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run: `python3 -m unittest discover -s tests -p 'test_observation_storage.py' -v`

Expected: failure because the new tables and `client_fetch_id` column do not exist.

- [ ] **Step 3: Add idempotent DDL and deterministic backfill**

Add the approved schema inside `Store._init()` and a one-shot `schema_meta` migration. Use composite tenant foreign keys and indexes required by range scans.

```python
db.executescript("""
CREATE TABLE IF NOT EXISTS friend_annotations (
    tenant_id TEXT NOT NULL,
    friend_id TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    pinned INTEGER NOT NULL DEFAULT 0 CHECK(pinned IN (0,1)),
    revision TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, friend_id),
    FOREIGN KEY (tenant_id, friend_id) REFERENCES friends(tenant_id, id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS tags (
    tenant_id TEXT NOT NULL,
    id TEXT NOT NULL,
    name TEXT NOT NULL COLLATE NOCASE,
    color TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, id),
    UNIQUE (tenant_id, name),
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS friend_tags (
    tenant_id TEXT NOT NULL,
    friend_id TEXT NOT NULL,
    tag_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, friend_id, tag_id),
    FOREIGN KEY (tenant_id, friend_id) REFERENCES friends(tenant_id, id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, tag_id) REFERENCES tags(tenant_id, id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS collection_samples (
    tenant_id TEXT NOT NULL,
    sample_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    source TEXT NOT NULL,
    outcome TEXT NOT NULL,
    authoritative INTEGER NOT NULL CHECK(authoritative IN (0,1)),
    expected_interval_seconds INTEGER NOT NULL,
    friend_count INTEGER,
    online_count INTEGER,
    duration_ms INTEGER,
    error_category TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (tenant_id, sample_id),
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_collection_samples_tenant_time
    ON collection_samples(tenant_id, observed_at, sample_id);
CREATE TABLE IF NOT EXISTS friend_tracking_events (
    tenant_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    friend_id TEXT NOT NULL,
    tracked INTEGER NOT NULL CHECK(tracked IN (0,1)),
    occurred_at TEXT NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY (tenant_id, event_id),
    FOREIGN KEY (tenant_id, friend_id) REFERENCES friends(tenant_id, id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_tracking_tenant_friend_time
    ON friend_tracking_events(tenant_id, friend_id, occurred_at, event_id);
CREATE TABLE IF NOT EXISTS friend_identity_events (
    tenant_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    friend_id TEXT NOT NULL,
    field TEXT NOT NULL CHECK(field IN ('username','display_name')),
    old_value TEXT NOT NULL,
    new_value TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY (tenant_id, event_id),
    FOREIGN KEY (tenant_id, friend_id) REFERENCES friends(tenant_id, id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS event_anomalies (
    tenant_id TEXT NOT NULL,
    anomaly_id TEXT NOT NULL,
    event_kind TEXT NOT NULL,
    event_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    detected_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, anomaly_id),
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_event_anomalies_target
    ON event_anomalies(tenant_id, event_kind, event_id);
CREATE TABLE IF NOT EXISTS tenant_preferences (
    tenant_id TEXT PRIMARY KEY,
    timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai',
    updated_at TEXT NOT NULL,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS world_resolution_state (
    world_id TEXT PRIMARY KEY,
    outcome TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    retry_at TEXT,
    error_category TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);
""")
```

For legacy rows, include the original row identity so byte-identical fetches stay
distinct. New writes generate and persist `fetch_` plus 32 random hexadecimal bytes.
Both forms remain stable across export/import:

```python
def legacy_raw_fetch_id(legacy_id, occurred_at, method, path, status_code,
                        content_type, body, error):
    identity = json.dumps(
        [legacy_id, occurred_at, method, path, status_code, content_type,
         hashlib.sha256(body).hexdigest(), error],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return "legacy_fetch_" + hashlib.sha256(identity).hexdigest()
```

- [ ] **Step 4: Run storage and migration tests**

Run: `python3 -m unittest discover -s tests -p 'test_observation_storage.py' -v`

Expected: all observation storage tests pass.

Run: `python3 -m unittest tests/test_hosted_storage.py tests/test_hosted_backup.py -v`

Expected: existing hosted storage and backup tests pass without destructive migration behavior.

- [ ] **Step 5: Commit the schema slice**

```bash
git add server/storage.py scripts/backup_format.py tests/test_observation_storage.py
git commit -m "feat(storage): add observation evidence ledgers"
```

### Task 2: Atomic authoritative snapshot ingestion

**Files:**
- Modify: `server/storage.py`
- Modify: `server/hosted_collector.py`
- Modify: `server/schemas.py`
- Create: `tests/test_observation_collection.py`

- [ ] **Step 1: Write failing tests for snapshot, tracking, identity, and failure transitions**

```python
def test_authoritative_snapshot_records_one_sample_and_tracking_edges(self):
    store, tenant_id, collector_id = seeded_store_with_collector()
    store.ingest_authoritative_snapshot(
        tenant_id,
        collector_id,
        [friend("usr_a", "Alice", "active")],
        [],
        source="hosted-rest",
        observed_at="2026-08-30T08:00:00+00:00",
        expected_interval_seconds=180,
        duration_ms=120,
    )
    store.ingest_authoritative_snapshot(
        tenant_id,
        collector_id,
        [friend("usr_b", "Bob", "offline")],
        [],
        source="hosted-rest",
        observed_at="2026-08-30T08:03:00+00:00",
        expected_interval_seconds=180,
        duration_ms=110,
    )
    self.assertEqual(store.collection_sample_count(tenant_id), 2)
    self.assertEqual(
        [(item["friend_id"], item["tracked"]) for item in store.tracking_events(tenant_id)],
        [("usr_a", True), ("usr_a", False), ("usr_b", True)],
    )

def test_same_identity_change_is_idempotent(self):
    store, tenant_id, collector_id = seeded_store_with_collector()
    ingest_named_snapshot(store, tenant_id, collector_id, "Alice")
    ingest_named_snapshot(store, tenant_id, collector_id, "Alicia")
    ingest_named_snapshot(store, tenant_id, collector_id, "Alicia")
    self.assertEqual(store.identity_event_count(tenant_id), 1)

def test_repeated_failure_records_only_the_failure_edge(self):
    store, tenant_id, _ = seeded_store_with_collector()
    store.record_collection_failure(tenant_id, "hosted-rest", "network", 180)
    store.record_collection_failure(tenant_id, "hosted-rest", "network", 180)
    self.assertEqual(store.collection_failure_count(tenant_id), 1)
```

- [ ] **Step 2: Verify the tests fail before implementation**

Run: `python3 -m unittest discover -s tests -p 'test_observation_collection.py' -v`

Expected: failure because authoritative ingestion and sample query helpers are absent.

- [ ] **Step 3: Implement one-transaction snapshot ingestion**

`Store.ingest_authoritative_snapshot()` must begin one immediate transaction, compare
the complete incoming ID set with the previously tracked set, write deterministic
tracking/identity edges, normalize future events into `event_anomalies`, upsert the
friend snapshot, insert status events, insert exactly one successful collection
sample, and update collector/account freshness before commit.

```python
sample_id = stable_id("sample", tenant_id, source, observed_at, "success")
tracking_id = stable_id("tracking", friend_id, observed_at, int(tracked), source)
identity_id = stable_id("identity", friend_id, field, old_value, new_value, observed_at, source)
anomaly_id = stable_id("anomaly", "status_event", client_event_id, "future_timestamp")
```

Define stable ledger IDs once and reuse it for every append-only edge:

```python
def stable_id(prefix: str, *parts: object) -> str:
    encoded = json.dumps(parts, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return f"{prefix}_" + hashlib.sha256(encoded).hexdigest()
```

Keep `Store.ingest()` as the partial pipeline/external telemetry path; it may write
friend and status transitions but must not claim an authoritative coverage sample.

Add telemetry schema v2 for external collectors while preserving v1 as partial data:

```python
class ObservationTelemetry(StrictModel):
    observed_at: str = Field(min_length=1, max_length=64)
    expected_interval_seconds: int = Field(ge=45, le=3600)
    authoritative: Literal[True]

class TelemetryRequest(StrictModel):
    schema_version: Literal[1, 2]
    friends: list[FriendTelemetry] = Field(default_factory=list, max_length=5000)
    events: list[EventTelemetry] = Field(default_factory=list, max_length=10000)
    observation: ObservationTelemetry | None = None
```

Require `observation` for v2 and route it through authoritative ingestion. Version 1
continues to import friends/events without claiming coverage.

- [ ] **Step 4: Wire the hosted collector to authoritative and failure APIs**

In `HostedCollectorManager._sync()`, capture `started = time.monotonic()`, perform the
complete current-user and friend fetch, then call the new atomic method with
`expected_interval_seconds=self.poll_seconds`. In each exception branch call
`record_collection_failure()` with one of `rate_limited`, `session_expired`,
`upstream`, or `network` before scheduling bounded retry.

- [ ] **Step 5: Run collection, hosted collector, and isolation tests**

Run: `python3 -m unittest discover -s tests -p 'test_observation_collection.py' -v`

Expected: all new tests pass.

Run: `python3 -m unittest tests/test_hosted_storage.py tests/test_hosted_http.py tests/test_hosted_isolation.py -v`

Expected: existing login, collection, and tenant isolation behavior remains green.

- [ ] **Step 6: Commit collection evidence**

```bash
git add server/storage.py server/hosted_collector.py server/schemas.py tests/test_observation_collection.py
git commit -m "feat(collector): record authoritative observation evidence"
```

### Task 3: Pure coverage and interval engine

**Files:**
- Create: `server/observation.py`
- Create: `tests/test_observation_engine.py`
- Modify: `server/analytics.py`

- [ ] **Step 1: Write failing semantic tests**

```python
def test_gap_stops_online_and_world_span():
    windows = build_observed_windows(
        [sample("08:00", 180), sample("08:03", 180), sample("09:00", 180)],
        range_start=instant("08:00"),
        range_end=instant("10:00"),
    )
    self.assertEqual(windows, [window("08:00", "08:13")])

def test_location_offline_overrides_active_status():
    self.assertEqual(effective_state("active", "offline", is_self=False), "offline")

def test_future_event_is_excluded_even_after_wall_clock_passes():
    events = [event("2030-01-01T00:00:00Z", anomaly=True)]
    self.assertEqual(build_online_spans(events, [], start, end), [])

def test_person_hour_denominator_intersects_tracking_and_coverage():
    result = activity_cell(
        online=[window("08:10", "08:30")],
        covered=[window("08:00", "08:40")],
        tracked=[window("08:05", "09:00")],
        hour_start=instant("08:00"),
        hour_end=instant("09:00"),
    )
    self.assertEqual(result.observed_minutes, 35)
    self.assertAlmostEqual(result.ratio, 20 / 35)
```

- [ ] **Step 2: Run the engine tests and verify failure**

Run: `python3 -m unittest discover -s tests -p 'test_observation_engine.py' -v`

Expected: import failure because `server.observation` does not exist.

- [ ] **Step 3: Implement immutable interval primitives**

```python
@dataclass(frozen=True, slots=True)
class TimeWindow:
    start: datetime
    end: datetime

    def intersection(self, other: "TimeWindow") -> "TimeWindow | None":
        start = max(self.start, other.start)
        end = min(self.end, other.end)
        return TimeWindow(start, end) if end > start else None

@dataclass(frozen=True, slots=True)
class ActivityCell:
    online_minutes: float
    observed_minutes: float
    eligible_minutes: float
    ratio: float | None

def observation_deadline(observed_at: datetime, cadence_seconds: int) -> datetime:
    seconds = max(2 * cadence_seconds + 60, 600)
    return observed_at + timedelta(seconds=seconds)

def effective_state(status: str, location: str, is_self: bool) -> str:
    normalized_location = location.strip().lower()
    if normalized_location == "offline" or (is_self and not normalized_location):
        return "offline"
    normalized_status = status.strip().lower()
    return normalized_status or "offline"
```

Add deterministic functions to merge adjacent windows, construct tracking windows,
split state/world spans at coverage gaps, classify location, calculate coverage, and
calculate each person-hour cell with the approved minimum-evidence rule.

- [ ] **Step 4: Run engine tests**

Run: `python3 -m unittest discover -s tests -p 'test_observation_engine.py' -v`

Expected: all gap, normalization, anomaly, tracking, and DST cases pass.

- [ ] **Step 5: Commit the pure engine**

```bash
git add server/observation.py tests/test_observation_engine.py server/analytics.py
git commit -m "feat(analytics): bound intervals by observation coverage"
```

### Task 4: Coverage-aware analytics and per-person heatmaps

**Files:**
- Modify: `server/analytics.py`
- Modify: `server/app.py`
- Modify: `web/src/api.ts`
- Modify: `tests/test_presence.py`
- Create: `tests/test_hosted_analytics.py`

- [ ] **Step 1: Add failing response-contract tests**

```python
def test_presence_heatmap_exposes_evidence_per_person_hour(self):
    response = client.get("/v1/analytics/presence?heatmap_from=2026-08-01&heatmap_to=2026-08-30")
    self.assertEqual(response.status_code, 200)
    cell = response.json()["heatmap"][0]["cells"][8]
    self.assertEqual(
        set(cell),
        {"ratio", "online_minutes", "observed_minutes", "eligible_minutes", "covered_days", "range_days"},
    )

def test_stale_current_state_is_last_known_not_live(self):
    response = client.get("/v1/overview")
    self.assertEqual(response.json()["collector_state"], "stale")
    self.assertFalse(response.json()["live"])

def test_standalone_coverage_reports_gaps_and_timezone(self):
    response = client.get("/v1/coverage?from=2026-08-29&to=2026-08-30")
    self.assertEqual(response.status_code, 200)
    self.assertEqual(response.json()["timezone"], "Asia/Shanghai")
    self.assertGreaterEqual(len(response.json()["gaps"]), 1)
```

- [ ] **Step 2: Verify contract tests fail**

Run: `python3 -m unittest discover -s tests -p 'test_hosted_analytics.py' -v`

Expected: failure because heatmap cells and coverage metadata are not returned.

- [ ] **Step 3: Replace shared heatmap denominators**

Change each heatmap row to return 24 evidence cells and include range-level coverage:

```python
{
    "id": friend_id,
    "name": display_name,
    "is_self": is_self,
    "tracking_started_at": tracking_started_at,
    "cells": [
        {
            "ratio": cell.ratio,
            "online_minutes": round(cell.online_minutes, 1),
            "observed_minutes": round(cell.observed_minutes, 1),
            "eligible_minutes": round(cell.eligible_minutes, 1),
            "covered_days": cell.covered_days,
            "range_days": range_days,
        }
        for cell in cells
    ],
}
```

Timeline and world responses must include `coverage`, `gaps`, and the normalized
location category. Future time stays empty. Cache keys include time zone plus event,
friend, tracking, identity, anomaly, and sample revisions.

Add `GET /v1/coverage?from=&to=` using the same engine and tenant time zone so other
views never reimplement gap calculation.

- [ ] **Step 4: Update Zod response validation before UI use**

```typescript
const activityCellSchema = z.object({
  ratio: z.number().min(0).max(1).nullable(),
  online_minutes: z.number().nonnegative(),
  observed_minutes: z.number().nonnegative(),
  eligible_minutes: z.number().nonnegative(),
  covered_days: z.number().int().nonnegative(),
  range_days: z.number().int().positive(),
});
```

- [ ] **Step 5: Run backend and frontend contract tests**

Run: `python3 -m unittest discover -s tests -p 'test_hosted_analytics.py' -v`

Run: `python3 -m unittest tests/test_presence.py tests/test_hosted_http.py -v`

Run: `npm --prefix web test -- --run src/api.test.ts`

Expected: all commands pass and no existing analytics endpoint disappears.

- [ ] **Step 6: Commit coverage-aware APIs**

```bash
git add server/analytics.py server/app.py web/src/api.ts tests/test_presence.py tests/test_hosted_analytics.py web/src/api.test.ts
git commit -m "fix(analytics): calculate evidence per player and hour"
```

### Task 5: Streaming portable backup v3

**Files:**
- Create: `server/backup_v3.py`
- Modify: `server/backup_json.py`
- Modify: `server/storage.py`
- Modify: `server/app.py`
- Modify: `web/src/backup.ts`
- Modify: `web/src/backup-normalizer.ts`
- Modify: `web/src/workers/backup-preview.worker.ts`
- Create: `tests/test_hosted_backup_v3.py`
- Modify: `web/src/backup-normalizer.test.ts`
- Modify: `web/src/workers/backup-preview.worker.test.ts`

- [ ] **Step 1: Write failing v3 round-trip, credential exclusion, and rollback tests**

```python
def test_v3_full_round_trip_preserves_raw_and_new_ledgers(self):
    exported = b"".join(stream_tenant_backup(store, tenant_id, include_raw=True))
    target, target_tenant = empty_tenant_store()
    result = import_tenant_backup(target, target_tenant, gzip.decompress(exported))
    self.assertEqual(result["raw_fetches"], 2)
    self.assertEqual(result["collection_samples"], 3)
    self.assertEqual(result["friend_annotations"], 1)

def test_v3_never_exports_authentication_material(self):
    decoded = gzip.decompress(b"".join(stream_tenant_backup(store, tenant_id, True)))
    for forbidden in (b"encrypted_cookie", b"viewer_tokens", b"password", b"bootstrap_token"):
        self.assertNotIn(forbidden, decoded)

def test_invalid_late_record_rolls_back_entire_staged_import(self):
    before = target.table_counts(target_tenant)
    with self.assertRaises(ValueError):
        import_tenant_backup(target, target_tenant, backup_with_invalid_final_reference())
    self.assertEqual(target.table_counts(target_tenant), before)
```

- [ ] **Step 2: Verify v3 tests fail**

Run: `python3 -m unittest discover -s tests -p 'test_hosted_backup_v3.py' -v`

Expected: import failure because the v3 streaming module is absent.

- [ ] **Step 3: Implement streaming gzip JSON export**

`stream_tenant_backup()` writes a stable top-level field order and uses SQLite cursors
plus `json.JSONEncoder.iterencode()` so no tenant collection is materialized. The
manifest fields are:

```python
BACKUP_V3_FIELDS = (
    "friends",
    "status_events",
    "friend_annotations",
    "tags",
    "friend_tags",
    "friend_identity_events",
    "friend_tracking_events",
    "collection_samples",
    "event_anomalies",
    "tenant_preferences",
    "raw_fetches",
)
```

Raw bodies are base64 encoded per row while streaming. `include_raw=False` emits an
empty `raw_fetches` array and records `scope: "normalized"`; the default records
`scope: "full"`.

- [ ] **Step 4: Implement bounded staging import**

Parse arrays incrementally into temporary tenant-scoped staging tables, enforce
expanded byte/object/member limits, validate stable IDs and references, reject
credential-shaped root fields, then merge in one `BEGIN IMMEDIATE` transaction. Drop
staging tables in `finally`; a merge error must not commit any live row.

- [ ] **Step 5: Update browser preview and import normalization**

The worker must count v3 arrays without redacting `raw_fetches`; it sends the original
gzip file directly to the server after bounded preview instead of rebuilding a
materialized JSON file. Extend `BackupPreview` with `version`, `scope`, and all record
counts.

- [ ] **Step 6: Run all backup compatibility tests**

Run: `python3 -m unittest tests/test_hosted_backup.py tests/test_backup_io.py tests/test_import_export_history.py tests/test_hosted_backup_v3.py -v`

Run: `npm --prefix web test -- --run src/backup.test.ts src/backup-normalizer.test.ts src/workers/backup-preview.worker.test.ts`

Expected: v1/v2 imports remain supported, v3 round-trips are idempotent, and invalid
imports leave existing data unchanged.

- [ ] **Step 7: Commit portable backup v3**

```bash
git add server/backup_v3.py server/backup_json.py server/storage.py server/app.py tests/test_hosted_backup_v3.py web/src/backup.ts web/src/backup-normalizer.ts web/src/backup-normalizer.test.ts web/src/workers/backup-preview.worker.ts web/src/workers/backup-preview.worker.test.ts
git commit -m "feat(backup): stream complete tenant backups"
```
