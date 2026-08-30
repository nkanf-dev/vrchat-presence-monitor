import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
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

const searchResponse = (query: string, id: string, name: string) => ({
  query,
  groups: {
    people: [{
      id, username: query, name, status: 'active',
      location: 'private', avatar_url: '', is_self: false, pinned: false,
      tags: [], matches: ['current_name'], href: `#view=people&personDetail=${id}`,
    }],
    worlds: [], history: [], destinations: [],
  },
});

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

  it('flushes a rapid query on Enter without opening a recent destination', async () => {
    let resolveSearch!: (response: Response) => void;
    const pendingSearch = new Promise<Response>((resolve) => {
      resolveSearch = resolve;
    });
    vi.stubGlobal('fetch', vi.fn().mockReturnValue(pendingSearch));
    localStorage.setItem('presence-monitor:recent-destinations', JSON.stringify([
      '#area=analysis&section=worlds',
    ]));
    renderSearch();

    fireEvent.click(screen.getByRole('button', { name: '搜索玩家、世界和历史' }));
    const input = screen.getByLabelText('搜索');
    fireEvent.change(input, { target: { value: 'alice' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    expect(window.location.hash).toBe('');
    expect(screen.queryByRole('option', { name: /世界时间轴/ })).toBeNull();
    expect(screen.getByRole('status')).toHaveTextContent('正在搜索');
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));

    resolveSearch(jsonResponse(searchResponse('alice', 'usr_a', 'Alice')));
    await screen.findByRole('option', { name: /Alice/ });
    expect(window.location.hash).toBe('');

    fireEvent.keyDown(input, { key: 'Enter' });
    expect(window.location.hash).toBe('#view=people&personDetail=usr_a');
  });

  it('never opens a previous query result while the next query is debouncing', async () => {
    vi.stubGlobal('fetch', vi.fn((request: RequestInfo | URL) => {
      const url = new URL(String(request), window.location.origin);
      const query = url.searchParams.get('q');
      if (query === 'bob') {
        return Promise.resolve(jsonResponse(searchResponse('bob', 'usr_b', 'Bob')));
      }
      return Promise.resolve(jsonResponse(searchResponse('alice', 'usr_a', 'Alice')));
    }));
    const user = userEvent.setup();
    renderSearch();

    await user.click(screen.getByRole('button', { name: '搜索玩家、世界和历史' }));
    const input = screen.getByLabelText('搜索');
    await user.type(input, 'alice');
    await screen.findByRole('option', { name: /Alice/ });

    fireEvent.change(input, { target: { value: 'bob' } });
    expect(screen.queryByRole('option', { name: /Alice/ })).toBeNull();
    expect(screen.getByRole('status')).toHaveTextContent('正在搜索');
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(window.location.hash).toBe('');

    await screen.findByRole('option', { name: /Bob/ });
    expect(window.location.hash).toBe('');
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(window.location.hash).toBe('#view=people&personDetail=usr_b');
  });
});
