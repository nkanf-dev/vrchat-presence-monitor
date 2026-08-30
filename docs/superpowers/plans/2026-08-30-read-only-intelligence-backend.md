# Read-only Intelligence Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add tenant-local organization, global search, explainable friend insights, and observed-world discovery without adding user-driven VRChat requests.

**Architecture:** Keep product-owned mutable data behind a focused organization service and derive all intelligence from normalized SQLite history plus the observation engine. Search uses indexed compact entities, insights and discovery expose evidence instead of inferred relationship scores, and a background single-flight resolver enriches world IDs independently from page requests.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, SQLite, `unittest`, existing VRChat collector/cache

**Execution context:** Execute after `2026-08-30-observation-trust-foundation.md` on the authorized `main` branch.

---

### Task 1: Tenant preferences, notes, pins, and tags

**Files:**
- Create: `server/organization.py`
- Modify: `server/storage.py`
- Modify: `server/schemas.py`
- Modify: `server/app.py`
- Create: `tests/test_hosted_organization.py`
- Modify: `tests/test_hosted_isolation.py`

- [ ] **Step 1: Write failing service and HTTP tests**

```python
def test_annotation_revision_conflict_preserves_both_values(self):
    first = client.put(
        "/v1/friends/usr_a/annotation",
        json={"note": "第一次", "pinned": True, "revision": None},
    )
    conflict = client.put(
        "/v1/friends/usr_a/annotation",
        json={"note": "另一台设备", "pinned": False, "revision": "stale"},
    )
    self.assertEqual(first.status_code, 200)
    self.assertEqual(conflict.status_code, 409)
    self.assertEqual(conflict.json()["server"]["note"], "第一次")

def test_tag_names_are_case_insensitively_unique_per_tenant(self):
    self.assertEqual(client.post("/v1/tags", json={"name": "常玩", "color": "#8bd450"}).status_code, 201)
    self.assertEqual(client.post("/v1/tags", json={"name": "常玩", "color": "#ffffff"}).status_code, 409)

def test_timezone_rejects_non_iana_value(self):
    response = client.put("/v1/preferences", json={"timezone": "GMT+8"})
    self.assertEqual(response.status_code, 422)
```

- [ ] **Step 2: Run the focused tests and verify failure**

Run: `python3 -m unittest discover -s tests -p 'test_hosted_organization.py' -v`

Expected: 404 responses because organization endpoints do not exist.

- [ ] **Step 3: Implement organization service with optimistic concurrency**

```python
@dataclass(frozen=True, slots=True)
class Annotation:
    friend_id: str
    note: str
    pinned: bool
    revision: str
    updated_at: str

class AnnotationConflict(Exception):
    def __init__(self, server: Annotation):
        self.server = server

class OrganizationService:
    def put_annotation(self, tenant_id: str, friend_id: str, note: str,
                       pinned: bool, expected_revision: str | None) -> Annotation:
        with self.store.lock, self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self.store._require_tenant_friend(db, tenant_id, friend_id)
            current = self._annotation(db, tenant_id, friend_id)
            if current and expected_revision != current.revision:
                raise AnnotationConflict(current)
            revision = secrets.token_urlsafe(18)
            stamp = now()
            db.execute(
                """INSERT INTO friend_annotations(tenant_id,friend_id,note,pinned,revision,updated_at)
                VALUES(?,?,?,?,?,?) ON CONFLICT(tenant_id,friend_id) DO UPDATE SET
                note=excluded.note,pinned=excluded.pinned,revision=excluded.revision,
                updated_at=excluded.updated_at""",
                (tenant_id, friend_id, note, int(pinned), revision, stamp),
            )
            return Annotation(friend_id, note, pinned, revision, stamp)
```

Tag delete runs one same-tenant transaction and relies on composite foreign keys.
Validate tag names after Unicode trim/casefold and validate colors as `#RRGGBB`.
Resolve and persist the IANA time zone with `ZoneInfo`; default from first authenticated
browser proposal, otherwise `Asia/Shanghai`.

- [ ] **Step 4: Add strict request models and endpoints**

```python
class AnnotationWrite(StrictModel):
    note: str = Field(max_length=20_000)
    pinned: bool = False
    revision: str | None = Field(default=None, max_length=128)

class TagWrite(StrictModel):
    name: str = Field(min_length=1, max_length=80)
    color: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")

class PreferenceWrite(StrictModel):
    timezone: str = Field(min_length=1, max_length=80)
```

Expose annotation get/put, tag list/create/update/delete, friend-tag assignment, and
preferences get/put routes. All route handlers use `auth.row["tenant_id"]`; no tenant
parameter is accepted from the browser.

- [ ] **Step 5: Run organization and isolation tests**

Run: `python3 -m unittest discover -s tests -p 'test_hosted_organization.py' -v`

Run: `python3 -m unittest tests/test_hosted_isolation.py tests/test_hosted_security.py -v`

Expected: organization behavior passes and cross-tenant IDs return 404 without data disclosure.

- [ ] **Step 6: Commit organization data**

```bash
git add server/organization.py server/storage.py server/schemas.py server/app.py tests/test_hosted_organization.py tests/test_hosted_isolation.py
git commit -m "feat(organization): add private notes tags and pins"
```

### Task 2: Tenant-wide indexed search

**Files:**
- Create: `server/search.py`
- Modify: `server/storage.py`
- Modify: `server/app.py`
- Create: `tests/test_hosted_search.py`

- [ ] **Step 1: Write failing search behavior and isolation tests**

```python
def test_search_groups_people_names_notes_tags_worlds_and_history(self):
    result = client.get("/v1/search", params={"q": "alice", "limit": 8}).json()
    self.assertEqual(set(result["groups"]), {"people", "worlds", "history", "destinations"})
    self.assertEqual(result["groups"]["people"][0]["id"], "usr_alice")
    self.assertIn("historical_name", result["groups"]["people"][0]["matches"])

def test_search_recognizes_observed_vrchat_url_without_upstream_fetch(self):
    result = client.get(
        "/v1/search",
        params={"q": "https://vrchat.com/home/world/wrld_123", "limit": 8},
    ).json()
    self.assertEqual(result["groups"]["worlds"][0]["id"], "wrld_123")
    self.assertEqual(fake_vrchat.world_calls, 0)

def test_search_never_returns_other_tenant_note(self):
    self.assertNotIn("secret-other-tenant", json.dumps(client.get("/v1/search?q=secret").json()))
```

- [ ] **Step 2: Run tests and verify the endpoint is absent**

Run: `python3 -m unittest discover -s tests -p 'test_hosted_search.py' -v`

Expected: 404 for `/v1/search`.

- [ ] **Step 3: Implement bounded normalized search**

```python
@dataclass(frozen=True, slots=True)
class ParsedQuery:
    text: str
    user_id: str | None
    world_id: str | None

def parse_query(value: str) -> ParsedQuery:
    text = " ".join(value.strip().split())[:160]
    user = re.search(r"\busr_[A-Za-z0-9-]+\b", text)
    world = re.search(r"\bwrld_[A-Za-z0-9-]+\b", text)
    return ParsedQuery(text=text, user_id=user.group(0) if user else None,
                       world_id=world.group(0) if world else None)
```

Search only `friends`, identity events, annotations, tags, world cache joined to
observed world IDs, and compact status-event labels. Use escaped `LIKE` with indexed
exact-ID fast paths and capped subqueries. Never search raw bodies. Return at most
`limit` entries per group and stable navigation hashes instead of the entire matching
event stream.

- [ ] **Step 4: Add the response route and query validation**

`GET /v1/search?q=&limit=` requires 1–160 query characters and clamps group limit to
1–20. Empty-query recent destinations remain browser-local and do not call this route.

- [ ] **Step 5: Run search, performance, and isolation tests**

Run: `python3 -m unittest discover -s tests -p 'test_hosted_search.py' -v`

Expected: all search groups, ID parsing, bounds, and isolation pass; a sanitized
production-scale fixture stays below the 300 ms cold budget.

- [ ] **Step 6: Commit search**

```bash
git add server/search.py server/storage.py server/app.py tests/test_hosted_search.py
git commit -m "feat(search): find observed people worlds and history"
```

### Task 3: Explainable friend insights and identity history

**Files:**
- Create: `server/insights.py`
- Modify: `server/app.py`
- Create: `tests/test_hosted_insights.py`

- [ ] **Step 1: Write failing overlap and co-presence tests**

```python
def test_overlap_and_same_instance_are_distinct(self):
    result = insights.friend("tenant", "usr_a", start, end)
    self.assertEqual(result["online_overlap_minutes"], 90)
    self.assertEqual(result["co_presence_minutes"], 20)

def test_private_or_world_only_match_is_not_co_presence(self):
    result = insights.friend("tenant", "usr_a", start, end)
    self.assertEqual(result["co_presence_minutes"], 0)

def test_insight_labels_first_record_not_friendship_date(self):
    payload = client.get("/v1/friends/usr_a/insights?from=2026-08-01&to=2026-08-30").json()
    self.assertIn("first_recorded_at", payload)
    self.assertNotIn("friends_since", payload)
```

- [ ] **Step 2: Verify insight tests fail**

Run: `python3 -m unittest discover -s tests -p 'test_hosted_insights.py' -v`

Expected: import/route failure because the insight service is absent.

- [ ] **Step 3: Implement evidence-based insight calculations**

```python
@dataclass(frozen=True, slots=True)
class FriendInsightRange:
    start: datetime
    end: datetime
    timezone: str

def same_instance(location_a: str, location_b: str) -> bool:
    if world_id_from_location(location_a) is None:
        return False
    if world_id_from_location(location_b) is None:
        return False
    return canonical_instance(location_a) == canonical_instance(location_b)
```

Use coverage-bounded intervals for friend and self. Return first recorded, latest
observed online, online overlap, same-instance co-presence, most visited worlds,
weekday/hour activity cells, identity events, coverage, and gap intervals. Do not
return a relationship score or imply a friendship creation date.

- [ ] **Step 4: Add the tenant-scoped endpoint**

`GET /v1/friends/{friend_id}/insights?from=&to=` validates the local date range,
limits it to 366 days, and returns 404 for an unknown or cross-tenant friend.

- [ ] **Step 5: Run semantic, DST, and endpoint tests**

Run: `python3 -m unittest discover -s tests -p 'test_hosted_insights.py' -v`

Run: `python3 -m unittest discover -s tests -p 'test_observation_engine.py' -v`

Expected: overlap, co-presence, private/hidden exclusion, gaps, and timezone behavior pass.

- [ ] **Step 6: Commit insights**

```bash
git add server/insights.py server/app.py tests/test_hosted_insights.py
git commit -m "feat(insights): explain observed friend activity"
```

### Task 4: Background world resolver, world library, and discovery

**Files:**
- Create: `server/worlds.py`
- Modify: `server/storage.py`
- Modify: `server/hosted_collector.py`
- Modify: `server/app.py`
- Create: `tests/test_hosted_worlds.py`
- Modify: `tests/test_hosted_http.py`

- [ ] **Step 1: Write failing no-request, single-flight, and ranking tests**

```python
def test_library_request_returns_cached_or_id_without_vrchat_call(self):
    seed_observed_world("wrld_observed_without_cache")
    response = client.get("/v1/world-library?q=wrld_observed_without_cache")
    self.assertEqual(response.status_code, 200)
    self.assertEqual(fake_vrchat.world_calls, 0)
    self.assertEqual(response.json()["items"][0]["id"], "wrld_observed_without_cache")

def test_duplicate_world_ids_queue_once(self):
    resolver.enqueue("tenant-a", "wrld_same")
    resolver.enqueue("tenant-b", "wrld_same")
    resolver.drain_once()
    self.assertEqual(fake_vrchat.world_calls, 1)

def test_hot_worlds_excludes_private_hidden_and_gaps(self):
    items = discovery.hot_worlds("tenant", start, end)
    self.assertEqual([item["world_id"] for item in items], ["wrld_public"])

def test_discovery_cache_meets_cold_and_warm_budget(self):
    cold = measured(lambda: discovery.hot_worlds("tenant", start, end))
    warm = measured(lambda: discovery.hot_worlds("tenant", start, end))
    self.assertLess(cold.seconds, 1.0)
    self.assertLess(warm.seconds, 0.15)
```

- [ ] **Step 2: Verify world-service tests fail**

Run: `python3 -m unittest discover -s tests -p 'test_hosted_worlds.py' -v`

Expected: module and endpoint failures.

- [ ] **Step 3: Implement resolver queue and cache states**

```python
@dataclass(slots=True)
class ResolveState:
    world_id: str
    tenant_ids: set[str]
    attempts: int = 0
    retry_at: float = 0.0

class WorldResolver:
    def enqueue(self, tenant_id: str, world_id: str) -> bool:
        normalized = require_world_id(world_id)
        with self.lock:
            state = self.pending.setdefault(normalized, ResolveState(normalized, set()))
            state.tenant_ids.add(tenant_id)
            return len(state.tenant_ids) == 1 and state.attempts == 0
```

Run resolution in bounded collector-owned workers. Keep one state per world ID,
honor `Retry-After`, exponential backoff, positive and negative TTLs, and retain stale
metadata on failure. Persist outcome, attempt count, and retry deadline in
`world_resolution_state`; reload it before accepting queue work after restart.
`GET /v1/worlds/{id}` returns cached metadata or an unresolved
representation immediately and enqueues background work; it no longer waits on VRChat.

- [ ] **Step 4: Implement observed world library and explainable discovery**

The world library query joins distinct parseable world IDs from status events to
cached metadata and last observed visit. Discovery supports 7/30-day ranges plus
friend/tag filters and returns visitors, visits, covered dwell minutes, return visits,
last observed, and prior-period comparison. Include self by default and allow explicit
exclusion. Every response includes observation coverage and unresolvable time.

- [ ] **Step 5: Add endpoints and pagination**

Expose:

```text
GET /v1/world-library?q=&author=&friend_id=&tag_id=&cursor=&limit=
GET /v1/discovery/worlds?days=&friend_id=&tag_id=&include_self=
GET /v1/worlds/{world_id}
```

Use opaque cursor pagination and a maximum page size of 100.

- [ ] **Step 6: Run resolver, discovery, and 429 tests**

Run: `python3 -m unittest discover -s tests -p 'test_hosted_worlds.py' -v`

Run: `python3 -m unittest tests/test_hosted_http.py -v`

Expected: ordinary browsing makes zero direct VRChat calls, duplicate IDs resolve once,
cached metadata survives failures, and 429 responses schedule bounded retry.

- [ ] **Step 7: Commit worlds and discovery**

```bash
git add server/worlds.py server/storage.py server/hosted_collector.py server/app.py tests/test_hosted_worlds.py tests/test_hosted_http.py
git commit -m "feat(discovery): rank observed worlds without request bursts"
```

### Task 5: Categorized tenant health endpoint

**Files:**
- Modify: `server/storage.py`
- Modify: `server/app.py`
- Modify: `server/security.py`
- Create: `tests/test_hosted_health.py`

- [ ] **Step 1: Write failing redaction and categorization tests**

```python
def test_operator_health_is_compact_and_redacted(self):
    response = bootstrap_client.get("/v1/admin/health/tenants")
    self.assertEqual(response.status_code, 200)
    encoded = json.dumps(response.json())
    for forbidden in ("cookie", "bio", "note", "password", "SSL", "curl"):
        self.assertNotIn(forbidden, encoded)

def test_normal_viewer_cannot_read_operator_health(self):
    self.assertEqual(viewer_client.get("/v1/admin/health/tenants").status_code, 401)
```

- [ ] **Step 2: Verify health tests fail**

Run: `python3 -m unittest discover -s tests -p 'test_hosted_health.py' -v`

Expected: route is absent.

- [ ] **Step 3: Implement stable health categories**

Return only tenant ID suffix/name, account state, last successful sync age, collector
state, `site_network`, `vrchat_service`, `session_expired`, or `collector_failure`, and
recent categorized success/failure counts. Protect with the existing bootstrap secret
using constant-time verification. Never return raw exception or response text.

- [ ] **Step 4: Run health and security tests**

Run: `python3 -m unittest discover -s tests -p 'test_hosted_health.py' -v`

Run: `python3 -m unittest tests/test_hosted_security.py -v`

Expected: compact health works only for bootstrap authentication and all sensitive text is absent.

- [ ] **Step 5: Commit operator health**

```bash
git add server/storage.py server/app.py server/security.py tests/test_hosted_health.py
git commit -m "feat(health): expose redacted tenant collection status"
```
