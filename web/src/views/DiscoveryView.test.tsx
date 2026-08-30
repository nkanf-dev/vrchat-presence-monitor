import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { DiscoveryView } from './DiscoveryView';

const api = vi.hoisted(() => ({
  getDiscovery: vi.fn(),
  getFriends: vi.fn(),
  getTags: vi.fn(),
  getWorldLibrary: vi.fn(),
}));

vi.mock('../api', () => ({
  ...api,
  worldImageUrl: (source: string) => `/world-image?url=${encodeURIComponent(source)}`,
}));

const stats = {
  minutes: 95,
  unique_people: 4,
  visit_count: 6,
  return_visits: 2,
  last_observed: '2026-08-29T14:30:00Z',
};

const discoveryResult = {
  hot: [{
    id: 'wrld_coffee',
    world_id: 'wrld_coffee',
    name: 'Coffee House',
    author_name: 'Alice',
    thumbnail_url: 'https://images.example/coffee.png',
    rank: 1,
    ...stats,
  }],
  rising: [{
    id: 'wrld_coffee',
    world_id: 'wrld_coffee',
    name: 'Coffee House',
    author_name: 'Alice',
    thumbnail_url: 'https://images.example/coffee.png',
    rank: 1,
    current: stats,
    previous: { ...stats, minutes: 25 },
    delta: { minutes: 70, unique_people: 1, visit_count: 2 },
  }],
  unavailable_minutes: 0,
  previous_unavailable_minutes: 0,
  range: {
    days: 30,
    from: '2026-07-31T00:00:00Z',
    to: '2026-08-30T00:00:00Z',
    previous_from: '2026-07-01T00:00:00Z',
    previous_to: '2026-07-31T00:00:00Z',
  },
  coverage: {
    range_minutes: 43_200,
    covered_minutes: 40_000,
    ratio: 0.925,
    first_recorded: '2026-07-31T00:00:00Z',
    last_recorded: '2026-08-30T00:00:00Z',
    gaps: [],
  },
  selected_people: 1,
  ranking: { hot: [], rising: [] },
};

const libraryResult = {
  items: [{
    id: 'wrld_library',
    name: 'Quiet Library',
    author_name: 'Bob',
    description: 'A calm place to read.',
    thumbnail_url: '',
    image_url: '',
    last_observed: '2026-08-28T10:00:00Z',
    event_count: 8,
    stale: false,
    resolution_status: 'ready',
  }],
  next_cursor: null,
  total: 1,
};

function renderView(onOpenWorld = vi.fn()) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity } },
  });
  return {
    onOpenWorld,
    ...render(
      <QueryClientProvider client={client}>
        <DiscoveryView onOpenWorld={onOpenWorld} />
      </QueryClientProvider>,
    ),
  };
}

describe('DiscoveryView', () => {
  beforeEach(() => {
    api.getFriends.mockResolvedValue({
      items: [{
        id: 'usr_alice',
        username: 'alice',
        display_name: 'Alice',
        is_self: 0,
        status: 'offline',
        updated_at: '2026-08-30T00:00:00Z',
      }],
      total: 1,
      limit: 200,
      offset: 0,
    });
    api.getTags.mockResolvedValue([{ id: 'tag_friends', name: '常一起玩', color: '#9bd861' }]);
    api.getDiscovery.mockResolvedValue(discoveryResult);
    api.getWorldLibrary.mockResolvedValue(libraryResult);
  });

  afterEach(() => {
    window.location.hash = '';
    vi.clearAllMocks();
  });

  it('passes hash filters to discovery and opens a world without losing timeline state', async () => {
    window.location.hash = '#area=analysis&section=discover&day=2026-08-21&discoverDays=30&discoverFriend=usr_alice&discoverTag=tag_friends&discoverSelf=0';
    const onOpenWorld = vi.fn();
    renderView(onOpenWorld);

    expect(await screen.findByText('Coffee House')).toBeVisible();
    expect(api.getDiscovery).toHaveBeenCalledWith({
      days: 30,
      friendId: 'usr_alice',
      tagId: 'tag_friends',
      includeSelf: false,
    });

    await userEvent.click(screen.getByRole('button', { name: '查看 Coffee House 的世界详情' }));
    expect(onOpenWorld).toHaveBeenCalledWith('wrld_coffee');

    const timeline = screen.getByRole('link', { name: '查看时间轴' });
    const target = new URLSearchParams(timeline.getAttribute('href')?.slice(1));
    expect(target.get('area')).toBe('analysis');
    expect(target.get('section')).toBe('worlds');
    expect(target.get('world')).toBe('wrld_coffee');
    expect(target.get('person')).toBe('usr_alice');
    expect(target.get('day')).toBe('2026-08-21');
    expect(target.get('discoverTag')).toBe('tag_friends');
  });

  it('keeps tab and filter state in the hash when opening the world library', async () => {
    window.location.hash = '#area=analysis&section=discover&day=2026-08-20&discoverDays=30&discoverTag=tag_friends';
    renderView();

    await screen.findByText('Coffee House');
    await userEvent.click(screen.getByRole('tab', { name: '世界库' }));

    expect(await screen.findByText('Quiet Library')).toBeVisible();
    await waitFor(() => {
      const parameters = new URLSearchParams(window.location.hash.slice(1));
      expect(parameters.get('discoverTab')).toBe('library');
      expect(parameters.get('discoverDays')).toBe('30');
      expect(parameters.get('discoverTag')).toBe('tag_friends');
      expect(parameters.get('day')).toBe('2026-08-20');
    });
    expect(api.getWorldLibrary).toHaveBeenCalledWith(expect.objectContaining({
      tagId: 'tag_friends',
      limit: 36,
    }));
  });

  it('shows a useful empty state for a filtered result', async () => {
    window.location.hash = '#area=analysis&section=discover&discoverFriend=usr_alice';
    api.getDiscovery.mockResolvedValue({ ...discoveryResult, hot: [] });
    renderView();

    expect(await screen.findByText('没有符合当前筛选的世界')).toBeVisible();
    expect(screen.getByRole('button', { name: '清除筛选' })).toBeVisible();
  });

  it('offers a retry when discovery cannot be loaded', async () => {
    api.getDiscovery.mockRejectedValue(new Error('网络暂时不可用'));
    renderView();

    expect(await screen.findByRole('alert')).toHaveTextContent('世界列表暂时没有加载出来');
    expect(screen.getByRole('button', { name: '重新加载' })).toBeVisible();
  });
});
