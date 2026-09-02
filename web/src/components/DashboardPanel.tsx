import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { RefreshCw } from 'lucide-react';
import { Component, useMemo, type ReactNode } from 'react';
import type { EChartsCoreOption } from 'echarts/core';

import {
  ApiError,
  type DashboardPanel as DashboardPanelModel,
  type DashboardPanelData,
  getDashboardPanelData,
} from '../api';
import { formatNumber, statusLabel } from '../format';
import { ChartDataTable, type ChartDataColumn } from './ChartDataTable';
import { EChart } from './EChart';

const colors = ['#b6f36b', '#67d8f4', '#f2a461', '#aa8df5', '#ff7588', '#5fd4b8', '#e7c75e'] as const;
const textColor = '#cbd3dd';
const faintColor = '#7f8b9d';
const lineColor = '#273241';
const escapeHtml = (value: string) => value
  .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
  .replaceAll('"', '&quot;').replaceAll("'", '&#39;');
const text = (value: unknown) => typeof value === 'string' ? value : '';
const number = (value: unknown) => Number.isFinite(Number(value)) ? Number(value) : 0;

const chartBase = (extra: EChartsCoreOption): EChartsCoreOption => ({
  animationDuration: 350,
  backgroundColor: 'transparent',
  textStyle: { color: textColor, fontFamily: 'Inter, system-ui, sans-serif' },
  tooltip: { trigger: 'item', appendTo: 'body', backgroundColor: '#0b1118', borderColor: '#39495c', textStyle: { color: '#f5f7fa' } },
  aria: { enabled: true },
  ...extra,
});

function PanelState({ pending, error, hasData, onRetry, children }: {
  pending: boolean;
  error: unknown;
  hasData: boolean;
  onRetry: () => void;
  children: React.ReactNode;
}) {
  if (pending && !hasData) return <div className="dashboard-panel-state" role="status">正在载入图表…</div>;
  if (error && !hasData) return (
    <div className="dashboard-panel-state dashboard-panel-error" role="alert">
      <span>{error instanceof ApiError ? error.message : '图表暂时无法载入'}</span>
      <button type="button" className="button button-secondary button-compact" onClick={onRetry}>
        <RefreshCw size={14} aria-hidden="true" />重试
      </button>
    </div>
  );
  return <>{error && <span className="dashboard-stale-note">刷新失败，显示上次结果</span>}{children}</>;
}

function MetricPanel({ data }: { data: DashboardPanelData }) {
  return <div className="dashboard-metric"><strong>{formatNumber(data.value ?? 0)}</strong><span>{data.detail ?? ''}</span></div>;
}

function CoveragePanel({ data }: { data: DashboardPanelData }) {
  const ratio = Math.max(0, Math.min(1, data.ratio ?? 0));
  return (
    <div className="dashboard-coverage">
      <div className="dashboard-coverage-value">{Math.round(ratio * 100)}%</div>
      <div className="dashboard-coverage-track"><span style={{ width: `${ratio * 100}%` }} /></div>
      <span>{Math.round(data.observed_minutes ?? 0).toLocaleString('zh-CN')} / {Math.round(data.expected_minutes ?? 0).toLocaleString('zh-CN')} 分钟有记录</span>
    </div>
  );
}

function DistributionPanel({ data, kind }: { data: DashboardPanelData; kind: 'status-breakdown' | 'platform-breakdown' }) {
  const items = (data.items ?? []).map((item) => ({
    name: kind === 'status-breakdown' ? statusLabel(text(item.name)) : (text(item.name) || '未知平台'),
    value: number(item.value),
  }));
  const option = useMemo(() => chartBase({
    color: [...colors],
    legend: { type: 'scroll', orient: 'vertical', right: 2, top: 'middle', textStyle: { color: textColor } },
    tooltip: { trigger: 'item', appendTo: 'body', valueFormatter: (value: unknown) => `${Number(value).toLocaleString('zh-CN')} 位` },
    series: [{ type: 'pie', radius: ['48%', '72%'], center: ['34%', '52%'], label: { show: false }, data: items }],
  }), [items]);
  return items.length ? <>
    <EChart option={option} label={kind === 'status-breakdown' ? '当前玩家状态分布' : '当前玩家平台分布'} />
    <ChartDataTable
      label="分布图数据"
      columns={[
        { key: 'name', header: kind === 'status-breakdown' ? '状态' : '平台', rowHeader: true, render: (row) => row.name },
        { key: 'value', header: '人数', render: (row) => `${row.value} 位` },
      ]}
      rows={items}
      getRowKey={(row) => row.name}
    />
  </> : <div className="dashboard-panel-state">当前筛选没有数据</div>;
}

function RankingPanel({ data, kind, rangeDays }: { data: DashboardPanelData; kind: 'online-ranking' | 'world-ranking'; rangeDays: number }) {
  const rows = data.items ?? [];
  const isWorld = kind === 'world-ranking';
  const names = rows.map((row) => isWorld ? text(row.name) || text(row.world_id) : text(row.name));
  const values = rows.map((row) => isWorld ? number(row.minutes) : number(row.hours));
  const option = useMemo(() => chartBase({
    color: [isWorld ? colors[5] : colors[0]],
    grid: { left: 12, right: 26, top: 8, bottom: 8, containLabel: true },
    xAxis: { type: 'value', axisLabel: { color: faintColor, formatter: isWorld ? '{value}m' : '{value}h' }, splitLine: { lineStyle: { color: lineColor } } },
    yAxis: { type: 'category', inverse: true, data: names, axisLabel: { color: textColor, width: 150, overflow: 'truncate' }, axisTick: { show: false }, axisLine: { show: false } },
    tooltip: { trigger: 'axis', appendTo: 'body', axisPointer: { type: 'shadow' }, valueFormatter: (value: unknown) => isWorld ? `${Math.round(Number(value))} 分钟` : `${Number(value).toFixed(1)} 小时` },
    series: [{ type: 'bar', data: values, barMaxWidth: 18, itemStyle: { borderRadius: [0, 6, 6, 0] } }],
  }), [isWorld, names, values]);
  const columns: ChartDataColumn<Record<string, unknown>>[] = isWorld ? [
    { key: 'name', header: '世界', rowHeader: true, render: (row) => text(row.name) || text(row.world_id) },
    { key: 'minutes', header: '游玩时间', render: (row) => `${Math.round(number(row.minutes))} 分钟` },
    { key: 'people', header: '玩家', render: (row) => `${number(row.unique_people)} 位` },
  ] : [
    { key: 'name', header: '玩家', rowHeader: true, render: (row) => text(row.name) },
    { key: 'hours', header: '在线时长', render: (row) => `${number(row.hours).toFixed(1)} 小时` },
  ];
  return rows.length ? <>
    <EChart option={option} label={`近 ${rangeDays} 天${isWorld ? '热门世界' : '在线时长排行'}`} />
    <ChartDataTable label="排行数据" columns={columns} rows={rows} getRowKey={(row) => text(row.id) || text(row.world_id)} />
  </> : <div className="dashboard-panel-state">这个筛选范围还没有记录</div>;
}

function ChangesPanel({ data, rangeDays }: { data: DashboardPanelData; rangeDays: number }) {
  const rows = data.items ?? [];
  const option = useMemo(() => chartBase({
    color: [colors[1]],
    grid: { left: 8, right: 18, top: 12, bottom: 8, containLabel: true },
    xAxis: { type: 'category', boundaryGap: false, data: rows.map((row) => text(row.day).slice(5)), axisLabel: { color: faintColor }, axisLine: { lineStyle: { color: lineColor } } },
    yAxis: { type: 'value', minInterval: 1, axisLabel: { color: faintColor }, splitLine: { lineStyle: { color: lineColor } } },
    tooltip: { trigger: 'axis', appendTo: 'body', valueFormatter: (value: unknown) => `${Number(value)} 次` },
    series: [{ type: 'line', data: rows.map((row) => number(row.changes)), smooth: true, symbolSize: 6, areaStyle: { opacity: 0.12 }, lineStyle: { width: 3 } }],
  }), [rows]);
  return rows.length ? <>
    <EChart option={option} label={`近 ${rangeDays} 天每日状态变化`} />
    <ChartDataTable
      label="每日状态变化数据"
      columns={[
        { key: 'day', header: '日期', rowHeader: true, render: (row: Record<string, unknown>) => text(row.day) },
        { key: 'changes', header: '状态变化', render: (row: Record<string, unknown>) => `${number(row.changes)} 次` },
      ]}
      rows={rows}
      getRowKey={(row) => text(row.day)}
    />
  </> : <div className="dashboard-panel-state">这个范围还没有记录</div>;
}

function HeatmapPanel({ data, rangeDays }: { data: DashboardPanelData; rangeDays: number }) {
  const rows = (data.rows ?? []).map((row) => ({
    id: text(row.id),
    name: text(row.name),
    cells: Array.isArray(row.cells) ? row.cells as Array<Record<string, unknown>> : [],
  }));
  const values = rows.flatMap((row, rowIndex) => row.cells.flatMap((cell, hour) =>
    cell.ratio === null || cell.ratio === undefined ? [] : [[hour, rowIndex, Math.round(number(cell.ratio) * 100)]],
  ));
  const option = useMemo(() => chartBase({
    grid: { left: 12, right: 24, top: 12, bottom: 32, containLabel: true },
    xAxis: { type: 'category', data: Array.from({ length: 24 }, (_, hour) => String(hour).padStart(2, '0')), axisLabel: { color: faintColor }, splitArea: { show: true }, axisTick: { show: false } },
    yAxis: { type: 'category', data: rows.map((row) => row.name), axisLabel: { color: textColor, width: 130, overflow: 'truncate' }, axisTick: { show: false }, axisLine: { show: false }, splitArea: { show: true } },
    visualMap: { min: 0, max: 100, show: false, inRange: { color: ['#121c24', '#315d55', '#76ac60', '#b6f36b'] } },
    tooltip: { appendTo: 'body', position: 'top', formatter: (params: unknown) => {
      const value = (params as { value?: [number, number, number] }).value;
      return value ? `${escapeHtml(rows[value[1]]?.name ?? '')}<br/>${String(value[0]).padStart(2, '0')}:00 · 在线比例 ${value[2]}%` : '';
    } },
    series: [{ type: 'heatmap', data: values, itemStyle: { borderColor: '#0b1118', borderWidth: 2, borderRadius: 3 }, emphasis: { itemStyle: { borderColor: '#f5f7fa', borderWidth: 1 } } }],
  }), [rows, values]);
  return rows.length ? <EChart option={option} label={`近 ${rangeDays} 天每位玩家每小时在线比例热力图`} /> : <div className="dashboard-panel-state">这个筛选范围还没有热力数据</div>;
}

function DashboardPanelContent({ panel, globalRangeDays, data: provided }: {
  panel: DashboardPanelModel;
  globalRangeDays: number;
  data?: DashboardPanelData;
}) {
  const result = useQuery({
    queryKey: ['dashboard-data', panel, globalRangeDays],
    queryFn: () => getDashboardPanelData(panel, globalRangeDays as 1 | 7 | 30 | 90),
    enabled: provided === undefined,
    placeholderData: keepPreviousData,
    staleTime: 30_000,
  });
  const data = provided ?? result.data;
  const error = data?.error ? new ApiError(data.error) : result.error;
  const rangeDays = panel.range_days || globalRangeDays;
  return (
    <PanelState pending={result.isPending && !provided} error={error} hasData={Boolean(data)} onRetry={() => void result.refetch()}>
      {!data ? null
        : panel.kind === 'online-now' || panel.kind === 'tracked-count' ? <MetricPanel data={data} />
        : panel.kind === 'collection-coverage' ? <CoveragePanel data={data} />
        : panel.kind === 'status-breakdown' || panel.kind === 'platform-breakdown' ? <DistributionPanel data={data} kind={panel.kind} />
        : panel.kind === 'daily-changes' ? <ChangesPanel data={data} rangeDays={rangeDays} />
        : panel.kind === 'friend-heatmap' ? <HeatmapPanel data={data} rangeDays={rangeDays} />
        : <RankingPanel data={data} kind={panel.kind} rangeDays={rangeDays} />}
    </PanelState>
  );
}

class PanelErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() {
    if (this.state.failed) return (
      <div className="dashboard-panel-state dashboard-panel-error" role="alert">
        <span>这张图表暂时无法显示</span>
        <button type="button" className="button button-secondary button-compact" onClick={() => this.setState({ failed: false })}>重新渲染</button>
      </div>
    );
    return this.props.children;
  }
}

export function DashboardPanel(props: {
  panel: DashboardPanelModel;
  globalRangeDays: number;
  data?: DashboardPanelData;
}) {
  return (
    <PanelErrorBoundary key={`${props.panel.id}:${props.panel.kind}:${props.globalRangeDays}`}>
      <DashboardPanelContent {...props} />
    </PanelErrorBoundary>
  );
}
