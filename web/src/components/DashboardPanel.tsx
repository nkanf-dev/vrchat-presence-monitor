import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { RefreshCw } from 'lucide-react';
import { Component, useMemo, type ReactNode } from 'react';
import type { EChartsCoreOption } from 'echarts/core';

import { offsetDateKey, todayKey } from '../analytics';
import {
  ApiError,
  type DashboardPanel as DashboardPanelModel,
  getAnalyticsStats,
  getDiscovery,
  getOverview,
  getPresenceAnalytics,
} from '../api';
import { formatNumber, statusLabel } from '../format';
import { EChart } from './EChart';
import { ChartDataTable, type ChartDataColumn } from './ChartDataTable';

const colors = ['#b6f36b', '#67d8f4', '#f2a461', '#aa8df5', '#ff7588', '#5fd4b8', '#e7c75e'] as const;
const textColor = '#cbd3dd';
const faintColor = '#7f8b9d';
const lineColor = '#273241';
const escapeHtml = (value: string) => value
  .replaceAll('&', '&amp;')
  .replaceAll('<', '&lt;')
  .replaceAll('>', '&gt;')
  .replaceAll('"', '&quot;')
  .replaceAll("'", '&#39;');

const chartBase = (extra: EChartsCoreOption): EChartsCoreOption => ({
  animationDuration: 350,
  backgroundColor: 'transparent',
  textStyle: { color: textColor, fontFamily: 'Inter, system-ui, sans-serif' },
  tooltip: {
    trigger: 'item',
    backgroundColor: '#0b1118',
    borderColor: '#39495c',
    textStyle: { color: '#f5f7fa' },
  },
  aria: { enabled: true },
  ...extra,
});

function PanelState({
  pending,
  error,
  hasData,
  onRetry,
  children,
}: {
  pending: boolean;
  error: unknown;
  hasData: boolean;
  onRetry: () => void;
  children: React.ReactNode;
}) {
  if (pending && !hasData) return <div className="dashboard-panel-state" role="status">正在载入图表…</div>;
  if (error && !hasData) {
    return (
      <div className="dashboard-panel-state dashboard-panel-error" role="alert">
        <span>{error instanceof ApiError ? error.message : '图表暂时无法载入'}</span>
        <button type="button" className="button button-secondary button-compact" onClick={onRetry}>
          <RefreshCw size={14} aria-hidden="true" />重试
        </button>
      </div>
    );
  }
  return (
    <>
      {error && <span className="dashboard-stale-note">刷新失败，显示上次结果</span>}
      {children}
    </>
  );
}

function MetricPanel({ panel }: { panel: DashboardPanelModel }) {
  const result = useQuery({
    queryKey: ['dashboard-data', 'overview'],
    queryFn: getOverview,
    placeholderData: keepPreviousData,
    staleTime: 30_000,
  });
  const value = panel.kind === 'online-now' ? result.data?.online_count : result.data?.tracked_count;
  const detail = panel.kind === 'online-now'
    ? `${result.data?.tracked_count ?? 0} 位追踪对象`
    : `${result.data?.online_count ?? 0} 位当前在线`;
  return (
    <PanelState pending={result.isPending} error={result.error} hasData={value !== undefined} onRetry={() => void result.refetch()}>
      <div className="dashboard-metric">
        <strong>{formatNumber(value ?? 0)}</strong>
        <span>{detail}</span>
      </div>
    </PanelState>
  );
}

function StatusPanel() {
  const result = useQuery({
    queryKey: ['dashboard-data', 'overview'],
    queryFn: getOverview,
    placeholderData: keepPreviousData,
    staleTime: 30_000,
  });
  const entries = Object.entries(result.data?.status_counts ?? {}).sort((a, b) => b[1] - a[1]);
  const option = useMemo(() => chartBase({
    color: [...colors],
    legend: { type: 'scroll', orient: 'vertical', right: 2, top: 'middle', textStyle: { color: textColor } },
    tooltip: { trigger: 'item', valueFormatter: (value: unknown) => `${Number(value).toLocaleString('zh-CN')} 位` },
    series: [{
      type: 'pie',
      radius: ['48%', '72%'],
      center: ['34%', '52%'],
      label: { show: false },
      data: entries.map(([name, value]) => ({ name: statusLabel(name), value })),
    }],
  }), [entries]);
  return (
    <PanelState pending={result.isPending} error={result.error} hasData={Boolean(result.data)} onRetry={() => void result.refetch()}>
      {entries.length ? <>
        <EChart option={option} label="当前玩家状态分布" />
        <ChartDataTable
          label="当前玩家状态分布数据"
          columns={[
            { key: 'status', header: '状态', rowHeader: true, render: (row: [string, number]) => statusLabel(row[0]) },
            { key: 'count', header: '人数', render: (row: [string, number]) => `${row[1]} 位` },
          ]}
          rows={entries}
          getRowKey={(row) => row[0]}
        />
      </> : <div className="dashboard-panel-state">还没有状态数据</div>}
    </PanelState>
  );
}

function StatsPanel({ panel, rangeDays }: { panel: DashboardPanelModel; rangeDays: number }) {
  const result = useQuery({
    queryKey: ['dashboard-data', 'stats', rangeDays],
    queryFn: () => getAnalyticsStats(rangeDays),
    placeholderData: keepPreviousData,
    staleTime: 30_000,
  });
  const ranking = (result.data?.online_hours_all ?? []).slice(0, panel.limit);
  const changes = result.data?.daily_changes ?? [];
  const rankingOption = useMemo(() => chartBase({
    color: [colors[0]],
    grid: { left: 12, right: 26, top: 8, bottom: 8, containLabel: true },
    xAxis: { type: 'value', axisLabel: { color: faintColor, formatter: '{value}h' }, splitLine: { lineStyle: { color: lineColor } } },
    yAxis: { type: 'category', inverse: true, data: ranking.map((item) => item.name), axisLabel: { color: textColor, width: 120, overflow: 'truncate' }, axisTick: { show: false }, axisLine: { show: false } },
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, valueFormatter: (value: unknown) => `${Number(value).toFixed(1)} 小时` },
    series: [{ type: 'bar', data: ranking.map((item) => item.hours), barMaxWidth: 18, itemStyle: { borderRadius: [0, 6, 6, 0] } }],
  }), [ranking]);
  const changesOption = useMemo(() => chartBase({
    color: [colors[1]],
    grid: { left: 8, right: 18, top: 12, bottom: 8, containLabel: true },
    xAxis: { type: 'category', boundaryGap: false, data: changes.map((item) => item.day.slice(5)), axisLabel: { color: faintColor }, axisLine: { lineStyle: { color: lineColor } } },
    yAxis: { type: 'value', minInterval: 1, axisLabel: { color: faintColor }, splitLine: { lineStyle: { color: lineColor } } },
    tooltip: { trigger: 'axis', valueFormatter: (value: unknown) => `${Number(value)} 次` },
    series: [{ type: 'line', data: changes.map((item) => item.changes), smooth: true, symbolSize: 6, areaStyle: { opacity: 0.12 }, lineStyle: { width: 3 } }],
  }), [changes]);
  const hasData = panel.kind === 'online-ranking' ? ranking.length > 0 : changes.length > 0;
  const tableColumns = useMemo<ChartDataColumn<(typeof ranking)[number] | (typeof changes)[number]>[]>(() => panel.kind === 'online-ranking' ? [
    { key: 'name', header: '玩家', rowHeader: true, render: (row) => 'name' in row ? row.name : '' },
    { key: 'hours', header: '在线时长', render: (row) => 'hours' in row ? `${row.hours.toFixed(1)} 小时` : '' },
  ] : [
    { key: 'day', header: '日期', rowHeader: true, render: (row) => 'day' in row ? row.day : '' },
    { key: 'changes', header: '状态变化', render: (row) => 'changes' in row ? `${row.changes} 次` : '' },
  ], [panel.kind]);
  const tableRows = panel.kind === 'online-ranking' ? ranking : changes;
  return (
    <PanelState pending={result.isPending} error={result.error} hasData={Boolean(result.data)} onRetry={() => void result.refetch()}>
      {hasData ? (
        <>
        <EChart
          option={panel.kind === 'online-ranking' ? rankingOption : changesOption}
          label={panel.kind === 'online-ranking' ? `近 ${rangeDays} 天在线时长排行` : `近 ${rangeDays} 天每日状态变化`}
        />
        <ChartDataTable
          label={panel.kind === 'online-ranking' ? '在线时长排行数据' : '每日状态变化数据'}
          columns={tableColumns}
          rows={tableRows}
          getRowKey={(row) => 'id' in row ? row.id : row.day}
        />
        </>
      ) : <div className="dashboard-panel-state">这个范围还没有记录</div>}
    </PanelState>
  );
}

function HeatmapPanel({ panel, rangeDays }: { panel: DashboardPanelModel; rangeDays: number }) {
  const today = todayKey();
  const from = offsetDateKey(today, -(rangeDays - 1));
  const result = useQuery({
    queryKey: ['dashboard-data', 'presence', from, today],
    queryFn: () => getPresenceAnalytics({ day: today, heatmapFrom: from, heatmapTo: today }),
    placeholderData: keepPreviousData,
    staleTime: 30_000,
  });
  const rows = (result.data?.heatmap ?? [])
    .filter((row) => panel.include_self || !row.is_self)
    .sort((a, b) => b.cells.reduce((sum, cell) => sum + cell.online_minutes, 0) - a.cells.reduce((sum, cell) => sum + cell.online_minutes, 0))
    .slice(0, panel.limit);
  const values = rows.flatMap((row, rowIndex) => row.cells.flatMap((cell, hour) =>
    cell.ratio === null ? [] : [[hour, rowIndex, Math.round(cell.ratio * 100)]],
  ));
  const option = useMemo(() => chartBase({
    grid: { left: 12, right: 24, top: 12, bottom: 32, containLabel: true },
    xAxis: { type: 'category', data: Array.from({ length: 24 }, (_, hour) => String(hour).padStart(2, '0')), axisLabel: { color: faintColor }, splitArea: { show: true }, axisTick: { show: false } },
    yAxis: { type: 'category', data: rows.map((row) => row.name), axisLabel: { color: textColor, width: 130, overflow: 'truncate' }, axisTick: { show: false }, axisLine: { show: false }, splitArea: { show: true } },
    visualMap: { min: 0, max: 100, calculable: false, orient: 'horizontal', left: 'center', bottom: 0, show: false, inRange: { color: ['#121c24', '#315d55', '#76ac60', '#b6f36b'] } },
    tooltip: { position: 'top', formatter: (params: unknown) => {
      const value = (params as { value?: [number, number, number] }).value;
      if (!value) return '';
      return `${escapeHtml(rows[value[1]]?.name ?? '')}<br/>${String(value[0]).padStart(2, '0')}:00 · 在线比例 ${value[2]}%`;
    } },
    series: [{ type: 'heatmap', data: values, itemStyle: { borderColor: '#0b1118', borderWidth: 2, borderRadius: 3 }, emphasis: { itemStyle: { borderColor: '#f5f7fa', borderWidth: 1 } } }],
  }), [rows, values]);
  const heatmapColumns = useMemo<ChartDataColumn<(typeof rows)[number]>[]>(() => [
    { key: 'name', header: '玩家', rowHeader: true, render: (row) => row.name },
    ...Array.from({ length: 24 }, (_, hour) => ({
      key: `hour-${hour}`,
      header: String(hour).padStart(2, '0'),
      render: (row: (typeof rows)[number]) => row.cells[hour]?.ratio === null || row.cells[hour]?.ratio === undefined ? '—' : `${Math.round((row.cells[hour]?.ratio ?? 0) * 100)}%`,
    })),
  ], []);
  return (
    <PanelState pending={result.isPending} error={result.error} hasData={Boolean(result.data)} onRetry={() => void result.refetch()}>
      {rows.length ? <>
        <EChart option={option} label={`近 ${rangeDays} 天每位好友每小时在线比例热力图`} />
        <ChartDataTable label="每好友每小时在线比例数据" columns={heatmapColumns} rows={rows} getRowKey={(row) => row.id} />
      </> : <div className="dashboard-panel-state">这个范围还没有热力数据</div>}
    </PanelState>
  );
}

function WorldPanel({ panel, rangeDays }: { panel: DashboardPanelModel; rangeDays: number }) {
  const discoveryDays = rangeDays === 1 || rangeDays === 7 || rangeDays === 30 ? rangeDays : 30;
  const result = useQuery({
    queryKey: ['dashboard-data', 'world-ranking', discoveryDays, panel.include_self, panel.limit],
    queryFn: () => getDiscovery({ days: discoveryDays, includeSelf: panel.include_self, limit: panel.limit, offset: 0 }),
    placeholderData: keepPreviousData,
    staleTime: 60_000,
  });
  const worlds = (result.data?.hot ?? []).slice(0, panel.limit);
  const option = useMemo(() => chartBase({
    color: [colors[5]],
    grid: { left: 12, right: 26, top: 8, bottom: 8, containLabel: true },
    xAxis: { type: 'value', axisLabel: { color: faintColor, formatter: '{value}m' }, splitLine: { lineStyle: { color: lineColor } } },
    yAxis: { type: 'category', inverse: true, data: worlds.map((world) => world.name || world.world_id), axisLabel: { color: textColor, width: 150, overflow: 'truncate' }, axisTick: { show: false }, axisLine: { show: false } },
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, valueFormatter: (value: unknown) => `${Math.round(Number(value))} 分钟` },
    series: [{ type: 'bar', data: worlds.map((world) => Math.round(world.minutes)), barMaxWidth: 18, itemStyle: { borderRadius: [0, 6, 6, 0] } }],
  }), [worlds]);
  return (
    <PanelState pending={result.isPending} error={result.error} hasData={Boolean(result.data)} onRetry={() => void result.refetch()}>
      {worlds.length ? <>
        <EChart option={option} label={`近 ${discoveryDays} 天好友热门世界`} />
        <ChartDataTable
          label="好友热门世界数据"
          columns={[
            { key: 'name', header: '世界', rowHeader: true, render: (row: (typeof worlds)[number]) => row.name || row.world_id },
            { key: 'minutes', header: '游玩时间', render: (row: (typeof worlds)[number]) => `${Math.round(row.minutes)} 分钟` },
            { key: 'people', header: '玩家', render: (row: (typeof worlds)[number]) => `${row.unique_people} 位` },
          ]}
          rows={worlds}
          getRowKey={(row) => row.world_id}
        />
      </> : <div className="dashboard-panel-state">这个范围还没有世界记录</div>}
    </PanelState>
  );
}

function DashboardPanelContent({
  panel,
  globalRangeDays,
}: {
  panel: DashboardPanelModel;
  globalRangeDays: number;
}) {
  const rangeDays = panel.range_days || globalRangeDays;
  if (panel.kind === 'online-now' || panel.kind === 'tracked-count') return <MetricPanel panel={panel} />;
  if (panel.kind === 'status-breakdown') return <StatusPanel />;
  if (panel.kind === 'online-ranking' || panel.kind === 'daily-changes') return <StatsPanel panel={panel} rangeDays={rangeDays} />;
  if (panel.kind === 'friend-heatmap') return <HeatmapPanel panel={panel} rangeDays={rangeDays} />;
  return <WorldPanel panel={panel} rangeDays={rangeDays} />;
}

class PanelErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    if (this.state.failed) {
      return (
        <div className="dashboard-panel-state dashboard-panel-error" role="alert">
          <span>这张图表暂时无法显示</span>
          <button type="button" className="button button-secondary button-compact" onClick={() => this.setState({ failed: false })}>重新渲染</button>
        </div>
      );
    }
    return this.props.children;
  }
}

export function DashboardPanel(props: {
  panel: DashboardPanelModel;
  globalRangeDays: number;
}) {
  return (
    <PanelErrorBoundary key={`${props.panel.id}:${props.panel.kind}:${props.globalRangeDays}`}>
      <DashboardPanelContent {...props} />
    </PanelErrorBoundary>
  );
}
