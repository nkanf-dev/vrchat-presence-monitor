import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  AUTH_REQUIRED_EVENT,
  downloadBackup,
  getDiscovery,
  getFriendInsight,
  getMe,
  getOverview,
  getSearch,
  getWorldLibrary,
  login,
  loginVrchat,
  updateFriendAnnotation,
  importBackupFile,
  verifyVrchat2fa,
} from './api';

const jsonResponse = (status: number, body: unknown, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', ...headers },
  });

describe('hosted API client', () => {
  afterEach(() => {
    localStorage.clear();
    vi.unstubAllGlobals();
  });

  it('starts a VRChat login with the exact account contract', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        ok: true,
        requires_2fa: true,
        methods: ['totp', 'emailOtp'],
      }),
    );
    vi.stubGlobal('fetch', fetchMock);

    const result = await loginVrchat({ username: 'alice@example.com', password: 'correct horse' });

    expect(result).toEqual({ ok: true, requires_2fa: true, methods: ['totp', 'emailOtp'] });
    expect(fetchMock).toHaveBeenCalledWith(
      '/v1/vrchat/login',
      expect.objectContaining({
        method: 'POST',
        credentials: 'same-origin',
        body: JSON.stringify({ username: 'alice@example.com', password: 'correct horse' }),
      }),
    );
  });

  it('finishes VRChat two-factor login with the exact code contract', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        ok: true,
        requires_2fa: false,
        user: { tenant_id: 'ten_1', name: 'Alice' },
        expires_at: '2026-09-26T00:00:00+00:00',
      }),
    );
    vi.stubGlobal('fetch', fetchMock);

    const result = await verifyVrchat2fa('123456');

    expect(result.user.name).toBe('Alice');
    expect(fetchMock).toHaveBeenCalledWith(
      '/v1/vrchat/2fa',
      expect.objectContaining({
        method: 'POST',
        credentials: 'same-origin',
        body: JSON.stringify({ code: '123456' }),
      }),
    );
  });

  it('keeps the access-code login API compatible', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        ok: true,
        user: { tenant_id: 'ten_1', name: 'Alice' },
        expires_at: '2026-09-26T00:00:00+00:00',
      }),
    );
    vi.stubGlobal('fetch', fetchMock);

    const result = await login('ABCD-EFGH-JKLM-NPQR-STUV');

    expect(result.user.name).toBe('Alice');
    expect(JSON.stringify(result)).not.toContain('session_token');
    expect(fetchMock).toHaveBeenCalledWith(
      '/v1/login',
      expect.objectContaining({
        method: 'POST',
        credentials: 'same-origin',
        body: JSON.stringify({ access_code: 'ABCD-EFGH-JKLM-NPQR-STUV' }),
      }),
    );
  });

  it('adopts a legacy LocalStorage token once and removes it after migration', async () => {
    localStorage.setItem('presence-monitor.session', 'legacy-secret');
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(401, { error: 'unauthorized' }))
      .mockResolvedValueOnce(
        jsonResponse(200, {
          authenticated: true,
          migrated: true,
          user: { tenant_id: 'ten_1', name: 'Alice' },
        }),
      );
    vi.stubGlobal('fetch', fetchMock);

    const result = await getMe();

    expect(result.user.name).toBe('Alice');
    expect(localStorage.getItem('presence-monitor.session')).toBeNull();
    expect(fetchMock.mock.calls[1]?.[1]).toEqual(
      expect.objectContaining({ headers: expect.objectContaining({ Authorization: 'Bearer legacy-secret' }) }),
    );
  });

  it('turns network failures into one user-safe error', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));

    await expect(getMe()).rejects.toEqual(
      expect.objectContaining({ code: 'network', message: '暂时无法连接服务' }),
    );
  });

  it('downloads the server-selected backup format only after a successful response', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response('{"format":"vrchat-monitor-hosted-backup"}', {
        status: 200,
        headers: {
          'Content-Type': 'application/gzip',
          'Content-Disposition': 'attachment; filename="tenant-backup.json.gz"',
        },
      }),
    );
    vi.stubGlobal('fetch', fetchMock);

    const result = await downloadBackup();

    expect(result.filename).toBe('tenant-backup.json.gz');
    expect(result.blob.size).toBeGreaterThan(0);
    expect(fetchMock).toHaveBeenCalledWith(
      '/v1/export.json',
      expect.objectContaining({ credentials: 'same-origin' }),
    );
  });

  it('reports an export error instead of treating an error page as a download', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(jsonResponse(503, { error: '备份容量暂时不可用' })),
    );

    await expect(downloadBackup()).rejects.toEqual(
      expect.objectContaining({ status: 503, message: '备份容量暂时不可用' }),
    );
  });

  it('announces an expired session for every authenticated endpoint', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(401, { error: 'unauthorized' })));
    const listener = vi.fn();
    window.addEventListener(AUTH_REQUIRED_EVENT, listener);

    await expect(getOverview()).rejects.toEqual(expect.objectContaining({ status: 401 }));

    expect(listener).toHaveBeenCalledTimes(1);
    window.removeEventListener(AUTH_REQUIRED_EVENT, listener);
  });

  it('validates grouped search results and forwards cancellation', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, {
      query: 'alice',
      groups: {
        people: [{
          id: 'usr_a', username: 'alice', name: 'Alice', status: 'active',
          location: 'private', avatar_url: '', is_self: false, pinned: true,
          tags: [{ id: 'tag_a', name: '常玩', color: '#8bd450' }],
          matches: ['current_name'], href: '#view=people&person=usr_a',
        }],
        worlds: [],
        history: [],
        destinations: [],
      },
    }));
    vi.stubGlobal('fetch', fetchMock);
    const controller = new AbortController();

    const result = await getSearch('alice', controller.signal);

    expect(result.groups.people[0]?.name).toBe('Alice');
    expect(fetchMock).toHaveBeenCalledWith(
      '/v1/search?q=alice&limit=8',
      expect.objectContaining({ signal: controller.signal }),
    );
  });

  it('rejects search entries without an internal destination', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, {
      query: 'alice',
      groups: {
        people: [{
          id: 'usr_a', username: 'alice', name: 'Alice', status: 'active',
          location: '', avatar_url: '', is_self: false, pinned: false,
          tags: [], matches: ['current_name'],
        }],
        worlds: [], history: [], destinations: [],
      },
    })));

    await expect(getSearch('alice')).rejects.toEqual(
      expect.objectContaining({ code: 'invalid-data' }),
    );
  });

  it('accepts player activity with unavailable hourly ratios', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, {
      friend: {
        id: 'usr_a', username: 'alice', display_name: 'Alice', updated_at: '2026-08-30T00:00:00Z',
      },
      from: '2026-08-01', to: '2026-08-30', timezone: 'Asia/Shanghai',
      first_recorded_at: '2026-08-01T00:00:00Z', latest_observed_online: null,
      online_minutes: 120, online_overlap_minutes: 30, co_presence_minutes: 10,
      most_visited_worlds: [],
      hourly_activity: Array.from({ length: 24 }, (_, hour) => ({
        hour, ratio: hour === 3 ? null : 0.25, online_minutes: 15,
        observed_minutes: 60, eligible_minutes: 60, covered_days: 1, range_days: 30,
      })),
      identity_events: [],
      coverage: {
        expected_minutes: 1000, observed_minutes: 800, ratio: 0.8,
        first_observed: '2026-08-01T00:00:00Z', last_observed: '2026-08-30T00:00:00Z', gaps: [],
      },
      gaps: [],
    })));

    const result = await getFriendInsight('usr_a', '2026-08-01', '2026-08-30');

    expect(result.hourly_activity[3]?.ratio).toBeNull();
  });

  it('keeps the server annotation when another device saved first', async () => {
    const server = {
      friend_id: 'usr_a', note: 'server note', pinned: false,
      revision: 'revision-new', updated_at: '2026-08-30T00:00:00Z', tags: [],
    };
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(409, { server })));

    await expect(updateFriendAnnotation('usr_a', {
      note: 'local note', pinned: true, revision: 'revision-old',
    })).rejects.toEqual(expect.objectContaining({ status: 409, details: { server } }));
  });

  it('reads the world library with stable paging and filters', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, {
      items: [{
        id: 'wrld_a', name: 'Coffee', author_name: 'Alice',
        last_observed: '2026-08-30T00:00:00Z', event_count: 4,
        resolution_status: 'ready', stale: false,
      }],
      next_cursor: 'MzY',
      total: 80,
    }));
    vi.stubGlobal('fetch', fetchMock);

    const result = await getWorldLibrary({
      query: 'coffee',
      friendId: 'usr_a',
      offset: 72,
      limit: 36,
    });

    expect(result.items[0]?.name).toBe('Coffee');
    expect(fetchMock).toHaveBeenCalledWith(
      '/v1/world-library?limit=36&q=coffee&friend_id=usr_a&offset=72',
      expect.objectContaining({ credentials: 'same-origin' }),
    );
  });

  it('reads hot and rising worlds for the selected range', async () => {
    const stats = {
      minutes: 90, unique_people: 4, visit_count: 6, return_visits: 2,
      last_observed: '2026-08-30T00:00:00Z',
    };
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, {
      hot: [{ id: 'wrld_a', world_id: 'wrld_a', name: 'Coffee', rank: 1, ...stats }],
      rising: [{
        id: 'wrld_a', world_id: 'wrld_a', name: 'Coffee', rank: 1,
        current: stats,
        previous: { ...stats, minutes: 20 },
        delta: { minutes: 70, unique_people: 0, visit_count: 0 },
      }],
      unavailable_minutes: 30,
      previous_unavailable_minutes: 20,
      range: {
        days: 7, from: '2026-08-23T00:00:00Z', to: '2026-08-30T00:00:00Z',
        previous_from: '2026-08-16T00:00:00Z', previous_to: '2026-08-23T00:00:00Z',
      },
      coverage: {
        range_minutes: 10080, covered_minutes: 9000, ratio: 0.8929,
        first_recorded: '2026-08-23T00:00:00Z', last_recorded: '2026-08-30T00:00:00Z', gaps: [],
      },
      hot_total: 72,
      rising_total: 18,
      limit: 30,
      offset: 30,
      selected_people: 12,
      ranking: { hot: ['unique_people:desc'], rising: ['delta.minutes:desc'] },
    }));
    vi.stubGlobal('fetch', fetchMock);

    const result = await getDiscovery({
      days: 0,
      includeSelf: false,
      worldTag: 'author_tag_social',
      limit: 30,
      offset: 30,
    });

    expect(result.hot[0]?.minutes).toBe(90);
    expect(result.hot_total).toBe(72);
    expect(fetchMock).toHaveBeenCalledWith(
      '/v1/discovery/worlds?days=0&include_self=false&limit=30&offset=30&world_tag=author_tag_social',
      expect.objectContaining({ credentials: 'same-origin' }),
    );
  });

  it('normalizes complete backup import counts for the UI', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, {
      ok: true,
      imported: {
        friends: 2,
        status_events: 17,
        friend_annotations: 1,
        tags: 3,
        friend_tags: 4,
        friend_identity_events: 2,
        friend_tracking_events: 5,
        collection_samples: 20,
        event_anomalies: 0,
        tenant_preferences: 1,
        raw_fetches: 8,
      },
    })));

    const result = await importBackupFile(new File(['{}'], 'backup.json', { type: 'application/json' }));

    expect(result.imported.events).toBe(17);
    expect(result.imported.raw_fetches).toBe(8);
  });
});
