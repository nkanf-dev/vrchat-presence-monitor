import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { FriendDialog } from './FriendDialog';

const jsonResponse = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status,
  headers: { 'Content-Type': 'application/json' },
});

const insight = {
  friend: {
    id: 'usr_a', username: 'alice', display_name: 'Alice', status: 'active',
    location: 'wrld_coffee:1', platform: 'standalonewindows', bio: 'Hello',
    updated_at: '2026-08-30T00:00:00Z',
  },
  from: '2026-08-01', to: '2026-08-30', timezone: 'Asia/Shanghai',
  first_recorded_at: '2026-08-01T00:00:00Z',
  latest_observed_online: '2026-08-30T00:00:00Z',
  online_minutes: 120,
  online_overlap_minutes: 45,
  co_presence_minutes: 20,
  most_visited_worlds: [{
    world_id: 'wrld_coffee', name: 'Coffee House', minutes: 70, visits: 3,
    last_observed: '2026-08-29T10:00:00Z',
  }],
  hourly_activity: Array.from({ length: 24 }, (_, hour) => ({
    hour, ratio: 0.25, online_minutes: 15, observed_minutes: 60,
    eligible_minutes: 60, covered_days: 1, range_days: 30,
  })),
  identity_events: [],
  coverage: {
    expected_minutes: 1000, observed_minutes: 900, ratio: 0.9,
    first_observed: '2026-08-01T00:00:00Z', last_observed: '2026-08-30T00:00:00Z', gaps: [],
  },
  gaps: [],
};

function renderDialog(fetchMock: ReturnType<typeof vi.fn>, onOpenWorld = vi.fn()) {
  vi.stubGlobal('fetch', fetchMock);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return {
    onOpenWorld,
    ...render(
      <QueryClientProvider client={client}>
        <FriendDialog friendId="usr_a" onClose={vi.fn()} onOpenWorld={onOpenWorld} />
      </QueryClientProvider>,
    ),
  };
}

describe('player details', () => {
  afterEach(() => {
    window.location.hash = '';
    vi.unstubAllGlobals();
  });

  it('keeps the active tab in the URL and opens a visited world', async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const path = String(input);
      if (path.startsWith('/v1/friends/usr_a/insights?')) return Promise.resolve(jsonResponse(insight));
      if (path === '/v1/worlds/wrld_coffee') return Promise.resolve(jsonResponse({
        id: 'wrld_coffee', name: 'Coffee House', author_name: 'Alice', resolution_status: 'ready',
      }));
      return Promise.resolve(jsonResponse({ error: 'not found' }, 404));
    });
    const user = userEvent.setup();
    const { onOpenWorld } = renderDialog(fetchMock);

    expect(await screen.findByRole('dialog', { name: 'Alice' })).toHaveAttribute('open');
    await user.click(screen.getByRole('button', { name: '世界' }));
    await waitFor(() => expect(new URLSearchParams(window.location.hash.slice(1)).get('personTab')).toBe('worlds'));
    await user.click(await screen.findByRole('button', { name: /Coffee House/ }));

    expect(onOpenWorld).toHaveBeenCalledWith('wrld_coffee');
  });

  it('automatically saves a note after the user pauses typing', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
      const path = String(input);
      if (path.startsWith('/v1/friends/usr_a/insights?')) return jsonResponse(insight);
      if (path === '/v1/friends/usr_a/annotation' && options?.method === 'PUT') {
        const body = JSON.parse(String(options.body)) as { note: string; pinned: boolean };
        return jsonResponse({
          friend_id: 'usr_a', note: body.note, pinned: body.pinned,
          revision: 'rev_2', updated_at: '2026-08-30T01:00:00Z', tags: [],
        });
      }
      if (path === '/v1/friends/usr_a/annotation') return jsonResponse({
        friend_id: 'usr_a', note: '', pinned: false,
        revision: null, updated_at: null, tags: [],
      });
      if (path === '/v1/tags') return jsonResponse([]);
      return jsonResponse({ error: 'not found' }, 404);
    });
    const user = userEvent.setup();
    renderDialog(fetchMock);

    await screen.findByRole('dialog', { name: 'Alice' });
    await user.click(screen.getByRole('button', { name: '备注与标签' }));
    const note = await screen.findByLabelText('玩家备注');
    await user.type(note, '下次一起去新世界');

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/v1/friends/usr_a/annotation',
        expect.objectContaining({ method: 'PUT', body: expect.stringContaining('下次一起去新世界') }),
      );
    }, { timeout: 2500 });
    expect(await screen.findByText('已保存')).toBeVisible();
  });
});
