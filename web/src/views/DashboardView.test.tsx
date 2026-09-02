import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { Dashboard } from '../api';
import { DashboardView } from './DashboardView';

const api = vi.hoisted(() => ({
  getDashboard: vi.fn(),
  updateDashboard: vi.fn(),
}));
const grid = vi.hoisted(() => ({
  onLayoutChange: undefined as undefined | ((layout: Array<Record<string, unknown>>) => void),
}));

vi.mock('../api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api')>()),
  ...api,
}));

vi.mock('react-grid-layout', () => ({
  default: ({ children, onLayoutChange }: { children: React.ReactNode; onLayoutChange?: typeof grid.onLayoutChange }) => {
    grid.onLayoutChange = onLayoutChange;
    return <div data-testid="dashboard-grid">{children}</div>;
  },
  useContainerWidth: () => ({ width: 1200, containerRef: () => undefined, mounted: true }),
  verticalCompactor: {},
}));

vi.mock('../components/DashboardPanel', () => ({
  DashboardPanel: ({ panel }: { panel: { title: string } }) => <div>{panel.title} 图表内容</div>,
}));

const dashboard: Dashboard = {
  revision: 'revision-1',
  updated_at: '2026-09-03T00:00:00Z',
  document: {
    schema_version: 1,
    title: '我的仪表盘',
    range_days: 7,
    refresh_seconds: 60,
    panels: [{
      id: 'online-now',
      kind: 'online-now',
      title: '当前在线',
      x: 0,
      y: 0,
      w: 3,
      h: 4,
      range_days: 0,
      limit: 10,
      include_self: true,
      friend_ids: [],
      statuses: [],
      platforms: [],
      world_ids: [],
      world_tag: '',
      world_sort: 'people',
      view: 'auto',
      sort_direction: 'auto',
      show_legend: true,
      show_table: true,
      metric: 'auto',
    }],
  },
};

function renderDashboard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const onUpdateParameters = vi.fn();
  return {
    onUpdateParameters,
    ...render(
      <QueryClientProvider client={client}>
        <DashboardView parameters={new URLSearchParams()} onUpdateParameters={onUpdateParameters} />
      </QueryClientProvider>,
    ),
  };
}

describe('DashboardView', () => {
  beforeEach(() => {
    grid.onLayoutChange = undefined;
    window.sessionStorage.clear();
    api.getDashboard.mockResolvedValue(structuredClone(dashboard));
    api.updateDashboard.mockImplementation(async (value) => ({
      ...value,
      revision: 'revision-2',
      updated_at: '2026-09-03T00:01:00Z',
    }));
  });

  it('adds, configures and saves a tenant dashboard', async () => {
    const user = userEvent.setup();
    const { onUpdateParameters } = renderDashboard();
    expect(await screen.findByRole('heading', { name: '我的仪表盘' })).toBeVisible();

    await user.click(screen.getByRole('button', { name: '30 天' }));
    expect(onUpdateParameters).toHaveBeenCalledWith({ dashRange: 30 }, true);

    await user.click(screen.getByRole('button', { name: /添加图表/ }));
    const dialog = screen.getByRole('dialog', { name: '添加图表' });
    await user.click(within(dialog).getByRole('button', { name: /每日状态变化/ }));
    expect(screen.getByText('每日状态变化 图表内容')).toBeVisible();

    await user.click(screen.getByRole('button', { name: '保存' }));
    await waitFor(() => expect(api.updateDashboard).toHaveBeenCalledWith(expect.objectContaining({
      revision: 'revision-1',
      document: expect.objectContaining({ range_days: 30, panels: expect.arrayContaining([
        expect.objectContaining({ kind: 'daily-changes' }),
      ]) }),
    })));
    expect(await screen.findByText(/已保存/)).toBeVisible();
  });

  it('persists the normalized grid layout reported by the layout engine', async () => {
    const user = userEvent.setup();
    renderDashboard();
    expect(await screen.findByRole('heading', { name: '我的仪表盘' })).toBeVisible();

    await user.click(screen.getByRole('button', { name: '编辑布局' }));
    await waitFor(() => expect(grid.onLayoutChange).toBeTypeOf('function'));
    act(() => grid.onLayoutChange?.([{ i: 'online-now', x: 2, y: 3, w: 4, h: 5 }]));
    await user.click(screen.getByRole('button', { name: '保存' }));

    await waitFor(() => expect(api.updateDashboard).toHaveBeenCalledWith(expect.objectContaining({
      document: expect.objectContaining({
        panels: [expect.objectContaining({ id: 'online-now', x: 2, y: 3, w: 4, h: 5 })],
      }),
    })));
  });
});
