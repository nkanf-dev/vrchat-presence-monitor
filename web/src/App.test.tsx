import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { App } from './App';

const jsonResponse = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });

const renderApp = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <App />
    </QueryClientProvider>,
  );
};

describe('product state machine', () => {
  afterEach(() => {
    localStorage.clear();
    window.location.hash = '';
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('shows one clear signed-out state when no browser session exists', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(401, { error: 'unauthorized' })));

    renderApp();

    expect(await screen.findByRole('heading', { name: '打开你的监控面板' })).toBeVisible();
    expect(screen.queryByRole('navigation')).not.toBeInTheDocument();
  });

  it('does not promise that an unverifiable browser session is still valid', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));

    renderApp();

    expect(await screen.findByRole('heading', { name: '暂时连不上服务' })).toBeVisible();
    expect(screen.getByText(/仍有效的会话会继续使用/)).toBeVisible();
    expect(screen.queryByText(/登录仍然保留/)).not.toBeInTheDocument();
  });

  it('logs in without exposing tokens and lands on an exact overview', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(401, { error: 'unauthorized' }))
      .mockResolvedValueOnce(
        jsonResponse(200, {
          ok: true,
          user: { tenant_id: 'ten_1', name: 'Alice 的监控' },
          expires_at: '2026-09-26T00:00:00+00:00',
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse(200, {
          authenticated: true,
          migrated: false,
          user: { tenant_id: 'ten_1', name: 'Alice 的监控' },
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse(200, {
          tracked_count: 29,
          online_count: 7,
          event_total: 10005,
          change_count_7d: 111,
          status_counts: { active: 4, offline: 22 },
          last_sync: '2026-08-27T10:00:00+00:00',
          collector_error: '',
          collector_state: 'fresh',
          sync_age_seconds: 0,
          stale_after_seconds: 300,
        }),
      )
      .mockResolvedValueOnce(jsonResponse(200, { items: [], total: 0, limit: 8, offset: 0 }))
      .mockResolvedValueOnce(jsonResponse(200, { items: [], total: 0, limit: 8, offset: 0 }));
    vi.stubGlobal('fetch', fetchMock);
    const user = userEvent.setup();
    renderApp();

    await user.type(await screen.findByLabelText('访问码'), 'ABCDEFGHJKLMNPQRSTUV');
    await user.click(screen.getByRole('button', { name: '登录' }));

    expect(await screen.findByRole('heading', { name: '状态总览' })).toBeVisible();
    expect(screen.getByText('10,005')).toBeVisible();
    expect(screen.getByText('近 7 天变化')).toBeVisible();
  });

  it('opens friend details as a keyboard-accessible dialog', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse(200, {
          authenticated: true,
          migrated: false,
          user: { tenant_id: 'ten_1', name: 'Alice 的监控' },
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse(200, {
          tracked_count: 1,
          online_count: 1,
          event_total: 0,
          change_count_7d: 0,
          status_counts: { active: 1 },
          last_sync: '2026-08-27T10:00:00+00:00',
          collector_error: '',
          collector_state: 'fresh',
          sync_age_seconds: 0,
          stale_after_seconds: 300,
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse(200, {
          items: [
            {
              id: 'usr_1',
              display_name: 'Alice',
              username: 'alice',
              status: 'active',
              location: 'wrld_example:1',
              platform: 'standalonewindows',
              is_self: 0,
              updated_at: '2026-08-27T10:00:00+00:00',
            },
          ],
          total: 1,
          limit: 8,
          offset: 0,
        }),
      )
      .mockResolvedValueOnce(jsonResponse(200, { items: [], total: 0, limit: 8, offset: 0 }));
    vi.stubGlobal('fetch', fetchMock);
    const user = userEvent.setup();
    renderApp();

    const person = await screen.findByRole('button', { name: /查看 Alice 的资料/ });
    await user.click(person);

    await waitFor(() => expect(screen.getByRole('dialog', { name: 'Alice' })).toHaveAttribute('open'));
    expect(screen.getByRole('button', { name: '关闭资料' })).toBeVisible();
  });

  it('keeps another view usable when the overview request fails', async () => {
    window.location.hash = '#view=history';
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const path = String(input);
      if (path === '/v1/me') {
        return Promise.resolve(
          jsonResponse(200, {
            authenticated: true,
            migrated: false,
            user: { tenant_id: 'ten_1', name: 'Alice 的监控' },
          }),
        );
      }
      if (path === '/v1/overview') {
        return Promise.resolve(jsonResponse(503, { error: 'temporarily unavailable' }));
      }
      if (path.startsWith('/v1/events?')) {
        return Promise.resolve(
          jsonResponse(200, {
            items: [
              {
                client_event_id: 'evt_1',
                friend_id: 'usr_1',
                display_name: 'Alice',
                occurred_at: '2026-08-27T12:00:00Z',
                old_status: 'offline',
                new_status: 'active',
              },
            ],
            total: 1,
            limit: 30,
            offset: 0,
          }),
        );
      }
      return Promise.resolve(jsonResponse(404, { error: 'not found' }));
    });
    vi.stubGlobal('fetch', fetchMock);

    renderApp();

    expect(await screen.findByRole('heading', { name: '状态历史' })).toBeVisible();
    expect(await screen.findByText('Alice')).toBeVisible();
    expect(await screen.findByText('状态摘要暂时无法加载')).toBeVisible();
    expect(screen.queryByRole('heading', { name: '数据暂时没有加载出来' })).not.toBeInTheDocument();
  });

  it('shows only one recovery surface when the first overview request fails', async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      if (String(input) === '/v1/me') {
        return Promise.resolve(
          jsonResponse(200, {
            authenticated: true,
            migrated: false,
            user: { tenant_id: 'ten_1', name: 'Alice 的监控' },
          }),
        );
      }
      return Promise.resolve(jsonResponse(503, { error: 'temporarily unavailable' }));
    });
    vi.stubGlobal('fetch', fetchMock);

    renderApp();

    expect(await screen.findByRole('heading', { name: '数据暂时没有加载出来' })).toBeVisible();
    expect(screen.getAllByRole('alert')).toHaveLength(1);
    expect(screen.queryByText('状态摘要暂时无法加载')).not.toBeInTheDocument();
  });

  it('returns to login immediately when a data-page session expires', async () => {
    window.location.hash = '#view=data';
    let meRequests = 0;
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const path = String(input);
      if (path === '/v1/me') {
        meRequests += 1;
        if (meRequests === 1) {
          return Promise.resolve(
            jsonResponse(200, {
              authenticated: true,
              migrated: false,
              user: { tenant_id: 'ten_1', name: 'Alice 的监控' },
            }),
          );
        }
        return Promise.resolve(jsonResponse(401, { error: 'unauthorized' }));
      }
      if (path === '/v1/overview') {
        return Promise.resolve(
          jsonResponse(200, {
            tracked_count: 0,
            online_count: 0,
            event_total: 0,
            change_count_7d: 0,
            status_counts: {},
            last_sync: null,
            collector_error: '',
            collector_state: 'never',
            sync_age_seconds: null,
            stale_after_seconds: 300,
          }),
        );
      }
      if (path === '/v1/capabilities') {
        return Promise.resolve(
          jsonResponse(200, {
            max_import_bytes: 32 * 1024 * 1024,
            max_import_expanded_bytes: 64 * 1024 * 1024,
            max_source_expanded_bytes: 256 * 1024 * 1024,
          }),
        );
      }
      if (path === '/v1/export.json') {
        return Promise.resolve(jsonResponse(401, { error: 'unauthorized' }));
      }
      return Promise.resolve(jsonResponse(404, { error: 'not found' }));
    });
    vi.stubGlobal('fetch', fetchMock);
    const user = userEvent.setup();
    renderApp();

    await user.click(await screen.findByRole('button', { name: '下载备份' }));

    expect(await screen.findByRole('heading', { name: '打开你的监控面板' })).toBeVisible();
    expect(screen.queryByRole('heading', { name: '数据与备份' })).not.toBeInTheDocument();
    expect(meRequests).toBe(2);
  });
});
