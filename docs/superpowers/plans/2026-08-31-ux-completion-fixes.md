# UX Completion Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the six remaining P1 navigation and interaction defects found by the independent UX review without changing collection or production data semantics.

**Architecture:** Keep browser-visible state in the existing hash router, mark detail-only history entries so close can unwind exactly one entry, and replace the world-library in-memory cursor stack with an offset page stored in the URL. Preserve the existing server cursor contract while adding an offset query for refresh-safe clients.

**Tech Stack:** React 19, TypeScript, TanStack Query, FastAPI, Python, Vitest, unittest.

---

### Task 1: Detail history and 2FA cancellation

**Files:**
- Modify: `web/src/navigation.ts`
- Modify: `web/src/App.tsx`
- Modify: `web/src/components/AuthScreens.tsx`
- Test: `web/src/navigation.test.ts`
- Test: `web/src/App.test.tsx`

- [ ] **Step 1: Add failing regressions**

```ts
expect(window.history.state?.presenceMonitorDetail).toBe('person');
expect(back).toHaveBeenCalledOnce();
expect(screen.getByRole('button', { name: '返回登录' })).toBeVisible();
```

- [ ] **Step 2: Run the focused tests and confirm they fail**

```bash
npm --prefix web test -- navigation.test.ts App.test.tsx
```

Expected: the detail-entry marker and 2FA return action are missing.

- [ ] **Step 3: Implement marked detail pushes and a browser-only 2FA return**

```ts
openDetail({ personDetail: friend.id, personTab: null }, 'person');
closeDetail({ personDetail: null, personTab: null }, 'person');
onBack={() => {
  setRequiresTwoFactor(false);
  loginMutation.reset();
  twoFactorMutation.reset();
}}
```

The close helper calls `history.back()` only when the current entry owns the matching marker; direct deep links are cleaned with `replaceState`. Returning from 2FA clears only the browser challenge UI and never calls disconnect/logout.

- [ ] **Step 4: Re-run the focused tests**

```bash
npm --prefix web test -- navigation.test.ts App.test.tsx
```

Expected: PASS.

### Task 2: Search destinations and stale Enter

**Files:**
- Modify: `server/search.py`
- Modify: `web/src/components/GlobalSearch.tsx`
- Test: `tests/test_hosted_search.py`
- Test: `web/src/components/GlobalSearch.test.tsx`

- [ ] **Step 1: Add failing search regressions**

```python
self.assertIn("historyQ=alice", groups["history"][0]["href"])
```

```ts
fireEvent.change(input, { target: { value: 'alice' } });
fireEvent.keyDown(input, { key: 'Enter' });
expect(window.location.hash).toBe('');
```

- [ ] **Step 2: Run the focused tests and confirm they fail**

```bash
python3 -m unittest tests.test_hosted_search
npm --prefix web test -- GlobalSearch.test.tsx
```

- [ ] **Step 3: Use the canonical history parameter and hide stale results**

```python
"href": f"#area=more&section=history&historyQ={quote(parsed.text)}",
```

```ts
const normalizedQuery = query.trim();
const querySettled = normalizedQuery === debounced;
const displayed = normalizedQuery ? (querySettled ? items : []) : recentItems;
```

Pressing Enter while the debounce is unsettled flushes the current text into the query and cannot select a recent destination.

- [ ] **Step 4: Re-run the focused tests**

```bash
python3 -m unittest tests.test_hosted_search
npm --prefix web test -- GlobalSearch.test.tsx
```

Expected: PASS.

### Task 3: Refresh-safe world-library pagination and scroll anchors

**Files:**
- Modify: `server/worlds.py`
- Modify: `server/app.py`
- Modify: `web/src/api.ts`
- Modify: `web/src/views/DiscoveryView.tsx`
- Test: `tests/test_hosted_worlds.py`
- Test: `web/src/api.test.ts`
- Test: `web/src/views/DiscoveryView.test.tsx`

- [ ] **Step 1: Add failing offset and refresh regressions**

```python
page = service.library(self.tenant_id, offset=1, limit=1)
self.assertEqual(page["items"][0]["id"], expected_second_world)
```

```ts
window.location.hash = '#area=analysis&section=discover&discoverTab=library&libraryPage=2';
expect(api.getWorldLibrary).toHaveBeenCalledWith(expect.objectContaining({ offset: 36 }));
```

- [ ] **Step 2: Run focused backend/frontend tests and confirm failure**

```bash
python3 -m unittest tests.test_hosted_worlds
npm --prefix web test -- api.test.ts DiscoveryView.test.tsx
```

- [ ] **Step 3: Add optional server offset and URL-backed page state**

```python
def library(..., cursor: str = "", offset: int | None = None, limit: int = 50):
    page_offset = max(0, int(offset)) if offset is not None else decode_cursor(cursor)
```

```ts
const libraryPage = selectedPage(parameters.get('libraryPage'));
getWorldLibrary({ ..., offset: libraryPage * LIBRARY_PAGE_SIZE, limit: LIBRARY_PAGE_SIZE });
```

Use the shared `Pagination` component, reset `libraryPage` when filters change, remove the in-memory cursor stack, and clamp an out-of-range page from the returned total.

- [ ] **Step 4: Clear stale scroll anchors on route-changing interactions**

```ts
update({ discoverPage: nextPage > 0 ? nextPage + 1 : null, y: null });
next.set('y', '0');
```

After a page fetch, focus and scroll the results heading; timeline links force the destination to its top instead of copying the discovery page's `y` value.

- [ ] **Step 5: Re-run focused tests**

```bash
python3 -m unittest tests.test_hosted_worlds
npm --prefix web test -- api.test.ts DiscoveryView.test.tsx
```

Expected: PASS.

### Task 4: Release and production verification

**Files:**
- Modify: `web/package.json`
- Modify: `server/__init__.py`

- [ ] **Step 1: Run the bounded release checks**

```bash
python3 -m unittest tests.test_hosted_search tests.test_hosted_worlds
npm --prefix web test -- navigation.test.ts App.test.tsx GlobalSearch.test.tsx api.test.ts DiscoveryView.test.tsx
```

- [ ] **Step 2: Commit with Conventional Commits and push `main`**

```bash
git commit -m "fix(ux): preserve navigation and discovery state"
git push origin main
```

- [ ] **Step 3: Bump and publish the beta release**

```bash
git commit -m "chore(release): prepare 0.3.0 beta 4"
git tag v0.3.0-beta.4
git push origin main v0.3.0-beta.4
```

- [ ] **Step 4: Deploy the immutable image and verify production**

Verify the app, fixed proxy egress, tunnel, local/off-site backup containers, tenant collector freshness, public assets, and a restore drill. Do not mutate the production database except through the existing append-only collector and backup processes.
