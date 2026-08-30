import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { GlobalSearch } from './GlobalSearch';

const jsonResponse = (body: unknown) => new Response(JSON.stringify(body), {
  status: 200,
  headers: { 'Content-Type': 'application/json' },
});

const renderSearch = () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <GlobalSearch />
    </QueryClientProvider>,
  );
};

describe('global search', () => {
  afterEach(() => {
    localStorage.clear();
    window.location.hash = '';
    vi.unstubAllGlobals();
  });

  it('opens from the keyboard and navigates to a player result', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({
      query: 'alice',
      groups: {
        people: [{
          id: 'usr_a', username: 'alice', name: 'Alice', status: 'active',
          location: 'private', avatar_url: '', is_self: false, pinned: false,
          tags: [], matches: ['current_name'], href: '#view=people&personDetail=usr_a',
        }],
        worlds: [], history: [], destinations: [],
      },
    })));
    const user = userEvent.setup();
    renderSearch();

    await user.keyboard('{Control>}k{/Control}');
    await user.type(screen.getByLabelText('搜索'), 'alice');
    const option = await screen.findByRole('option', { name: /Alice/ });
    await user.click(option);

    expect(window.location.hash).toBe('#view=people&personDetail=usr_a');
  });

  it('offers recent destinations before a query and closes with Escape', async () => {
    localStorage.setItem('presence-monitor:recent-destinations', JSON.stringify([
      '#area=analysis&section=worlds',
    ]));
    const user = userEvent.setup();
    renderSearch();

    await user.click(screen.getByRole('button', { name: '搜索玩家、世界和历史' }));
    expect(screen.getByRole('option', { name: /世界时间轴/ })).toBeVisible();
    await user.keyboard('{Escape}');

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });
});
