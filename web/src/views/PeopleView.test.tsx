import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { PeopleView } from './PeopleView';

const jsonResponse = (body: unknown) =>
  new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });

describe('PeopleView URL state', () => {
  afterEach(() => {
    window.location.hash = '';
    vi.unstubAllGlobals();
  });

  it('keeps a deep-linked page until that page has loaded', async () => {
    window.location.hash = '#view=people&peoplePage=3';
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        items: [
          {
            id: 'usr_1',
            display_name: 'Alice',
            status: 'active',
            updated_at: '2026-08-27T12:00:00Z',
          },
        ],
        total: 100,
        limit: 24,
        offset: 48,
      }),
    );
    vi.stubGlobal('fetch', fetchMock);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    render(
      <QueryClientProvider client={client}>
        <PeopleView onOpenFriend={vi.fn()} />
      </QueryClientProvider>,
    );

    expect(await screen.findByText('Alice')).toBeVisible();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(fetchMock.mock.calls[0]?.[0]).toContain('offset=48');
    expect(new URLSearchParams(window.location.hash.slice(1)).get('peoplePage')).toBe('3');
    expect(screen.getByRole('status', { name: '' })).toHaveTextContent('第 3 / 5 页');
  });
});
