import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { Overview } from '../api';
import { StatusBanner } from './StatusBanner';

const overview: Overview = {
  tracked_count: 4,
  online_count: 1,
  event_total: 20,
  change_count_7d: 5,
  status_counts: { active: 1, offline: 3 },
  last_sync: '2026-08-27T12:00:00Z',
  collector_error: '',
      collector_state: 'stale',
      live: false,
  sync_age_seconds: 900,
  stale_after_seconds: 300,
};

describe('StatusBanner', () => {
  it('does not describe stale collector data as connected', () => {
    render(<StatusBanner overview={overview} refreshFailed={false} onRetry={vi.fn()} />);

    expect(screen.getByText('采集数据已经过期')).toBeVisible();
    expect(screen.queryByText('数据已更新')).not.toBeInTheDocument();
  });

  it('reports an overview refresh failure without requiring cached data', () => {
    render(<StatusBanner overview={undefined} refreshFailed onRetry={vi.fn()} />);

    expect(screen.getByText('状态摘要暂时无法加载')).toBeVisible();
    expect(screen.getByRole('button', { name: '重试' })).toBeEnabled();
  });
});
