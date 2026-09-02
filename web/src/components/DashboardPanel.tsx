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

function MetricPanel({ data, panel }: { data: DashboardPanelData; panel: DashboardPanelModel }) {
  const percent = panel.kind === 'online-now' && panel.metric === 'percent';
  return <div className="dashboard-metric"><strong>{percent ? `${Math.round((data.ratio ?? 0) * 100)}%` : formatNumber(data.value ?? 0)}</strong><span>{data.detail ?? ''}</span></div>;
}

function CoveragePanel({ data, view }: { data: DashboardPanelData; view: DashboardPanelModel['view'] }) {
  const ratio = Math.max(0, Math.min(1, data.ratio ?? 0));
  if (view === 'auto' || view === 'number') {
    if (view === 'number') return <div className="dashboard-metric"><strong>{Math.round(ratio * 100)}%</strong><span>{Math.round(data.observed_minutes ?? 0).toLocaleString('zh-CN')} 分钟有记录</span></div>;
  }
  return (
    <div className="dashboard-coverage">
      <div className="dashboard-coverage-value">{Math.round(ratio * 100)}%</div>
      <div className="dashboard-coverage-track"><span style={{ width: `${ratio * 100}%` }} /></div>
      <span>{Math.round(data.observed_minutes ?? 0).toLocaleString('zh-CN')} / {Math.round(data.expected_minutes ?? 0).toLocaleString('zh-CN')} 分钟有记录</span>
    </div>
  );
}

function DistributionPanel({ data, panel }: { data: DashboardPanelData; panel: DashboardPanelModel }) {
  const kind = panel.kind as 'status-breakdown' | 'platform-breakdown';
  const rawItems = (data.items ?? []).map((item) => ({
    name: kind === 'status-breakdown' ? statusLabel(text(item.name)) : (text(item.name) || '未知平台'),
    value: number(item.value),
  }));
  const total = rawItems.reduce((sum, item) => sum + item.value, 0);
  const percent = panel.metric === 'percent';
  const items = rawItems.map((item) => ({ ...item, value: percent && total ? Math.round((item.value / total) * 1000) / 10 : item.value }));
  const unit = percent ? '%' : ' 位';
  const view = panel.view === 'auto' ? 'donut' : panel.view;
  const option = useMemo(() => chartBase(view === 'bar' ? {
    color: [colors[0]],
    grid: { left: 12, right: 24, top: 10, bottom: 10, containLabel: true },
    xAxis: { type: 'value', minInterval: percent ? undefined : 1, axisLabel: { color: faintColor, formatter: `{value}${unit}` }, splitLine: { lineStyle: { color: lineColor } } },
    yAxis: { type: 'category', inverse: true, data: items.map((item) => item.name), axisLabel: { color: textColor, width: 140, overflow: 'truncate' }, axisTick: { show: false }, axisLine: { show: false } },
    tooltip: { trigger: 'axis', appendTo: 'body', axisPointer: { type: 'shadow' }, valueFormatter: (value: unknown) => `${Number(value).toLocaleString('zh-CN')}${unit}` },
    series: [{ type: 'bar', data: items.map((item) => item.value), barMaxWidth: 20, itemStyle: { borderRadius: [0, 6, 6, 0] } }],
  } : {
    color: [...colors],
    legend: { show: panel.show_legend, type: 'scroll', orient: 'vertical', right: 2, top: 'middle', textStyle: { color: textColor } },
    tooltip: { trigger: 'item', appendTo: 'body', valueFormatter: (value: unknown) => `${Number(value).toLocaleString('zh-CN')}${unit}` },
    series: [{ type: 'pie', radius: ['48%', '72%'], center: [panel.show_legend ? '34%' : '50%', '52%'], label: { show: false }, data: items }],
  }), [items, panel.show_legend, percent, unit, view]);
  const table = <ChartDataTable
      label="分布图数据"
      columns={[
        { key: 'name', header: kind === 'status-breakdown' ? '状态' : '平台', rowHeader: true, render: (row) => row.name },
        { key: 'value', header: percent ? '占比' : '人数', render: (row) => `${row.value}${unit}` },
      ]}
      rows={items}
      getRowKey={(row) => row.name}
      alwaysOpen={view === 'table'}
    />;
  return items.length ? <>{view !== 'table' && <EChart option={option} label={kind === 'status-breakdown' ? '当前玩家状态分布' : '当前玩家平台分布'} />}{(view === 'table' || panel.show_table) && table}</> : <div className="dashboard-panel-state">当前筛选没有数据</div>;
}

function RankingPanel({ data, panel, rangeDays }: { data: DashboardPanelData; panel: DashboardPanelModel; rangeDays: number }) {
  const kind = panel.kind as 'online-ranking' | 'world-ranking';
  const rows = data.items ?? [];
  const isWorld = kind === 'world-ranking';
  const names = rows.map((row) => isWorld ? text(row.name) || text(row.world_id) : text(row.name));
  const metric = panel.metric === 'auto'
    ? (isWorld && panel.world_sort !== 'recent' ? panel.world_sort : isWorld ? 'minutes' : 'hours')
    : panel.metric;
  const values = rows.map((row) => {
    if (isWorld && metric === 'people') return number(row.unique_people);
    if (isWorld && metric === 'visits') return number(row.visit_count);
    if (!isWorld && metric === 'hours_per_day') return Math.round((number(row.hours) / Math.max(1, rangeDays)) * 10) / 10;
    return isWorld ? number(row.minutes) : number(row.hours);
  });
  const unit = metric === 'people' ? ' 位' : metric === 'visits' ? ' 次' : metric === 'minutes' ? ' 分钟' : metric === 'hours_per_day' ? ' 小时/天' : ' 小时';
  const option = useMemo(() => chartBase({
    color: [isWorld ? colors[5] : colors[0]],
    grid: { left: 12, right: 26, top: 8, bottom: 8, containLabel: true },
    xAxis: { type: 'value', axisLabel: { color: faintColor, formatter: `{value}${unit}` }, splitLine: { lineStyle: { color: lineColor } } },
    yAxis: { type: 'category', inverse: true, data: names, axisLabel: { color: textColor, width: 150, overflow: 'truncate' }, axisTick: { show: false }, axisLine: { show: false } },
    tooltip: { trigger: 'axis', appendTo: 'body', axisPointer: { type: 'shadow' }, valueFormatter: (value: unknown) => `${Number(value).toLocaleString('zh-CN')}${unit}` },
    series: [{ type: 'bar', data: values, barMaxWidth: 18, itemStyle: { borderRadius: [0, 6, 6, 0] } }],
  }), [isWorld, names, unit, values]);
  const columns: ChartDataColumn<Record<string, unknown>>[] = isWorld ? [
    { key: 'name', header: '世界', rowHeader: true, render: (row) => text(row.name) || text(row.world_id) },
    { key: 'minutes', header: '游玩时间', render: (row) => `${Math.round(number(row.minutes))} 分钟` },
    { key: 'people', header: '玩家', render: (row) => `${number(row.unique_people)} 位` },
    { key: 'visits', header: '到访', render: (row) => `${number(row.visit_count)} 次` },
    { key: 'recent', header: '最近到访', render: (row) => text(row.last_observed) || '—' },
  ] : [
    { key: 'name', header: '玩家', rowHeader: true, render: (row) => text(row.name) },
    { key: 'hours', header: '在线时长', render: (row) => `${number(row.hours).toFixed(1)} 小时` },
  ];
  const table = <ChartDataTable label="排行数据" columns={columns} rows={rows} getRowKey={(row) => text(row.id) || text(row.world_id)} alwaysOpen={panel.view === 'table'} />;
  return rows.length ? <>{panel.view !== 'table' && <EChart option={option} label={`近 ${rangeDays} 天${isWorld ? '热门世界' : '在线时长排行'}`} />}{(panel.view === 'table' || panel.show_table) && table}</> : <div className="dashboard-panel-state">这个筛选范围还没有记录</div>;
}

function ChangesPanel({ data, panel, rangeDays }: { data: DashboardPanelData; panel: DashboardPanelModel; rangeDays: number }) {
  const rows = data.items ?? [];
  const view = panel.view === 'auto' ? 'area' : panel.view;
  const option = useMemo(() => chartBase({
    color: [colors[1]],
    grid: { left: 8, right: 18, top: 12, bottom: 8, containLabel: true },
    xAxis: { type: 'category', boundaryGap: false, data: rows.map((row) => text(row.day).slice(5)), axisLabel: { color: faintColor }, axisLine: { lineStyle: { color: lineColor } } },
    yAxis: { type: 'value', minInterval: 1, axisLabel: { color: faintColor }, splitLine: { lineStyle: { color: lineColor } } },
    tooltip: { trigger: 'axis', appendTo: 'body', valueFormatter: (value: unknown) => `${Number(value)} 次` },
    series: view === 'bar'
      ? [{ type: 'bar', data: rows.map((row) => number(row.changes)), barMaxWidth: 24, itemStyle: { borderRadius: [5, 5, 0, 0] } }]
      : [{ type: 'line', data: rows.map((row) => number(row.changes)), smooth: true, symbolSize: 6, areaStyle: view === 'area' ? { opacity: 0.12 } : undefined, lineStyle: { width: 3 } }],
  }), [rows, view]);
  const table = <ChartDataTable
      label="每日状态变化数据"
      columns={[
        { key: 'day', header: '日期', rowHeader: true, render: (row: Record<string, unknown>) => text(row.day) },
        { key: 'changes', header: '状态变化', render: (row: Record<string, unknown>) => `${number(row.changes)} 次` },
      ]}
      rows={rows}
      getRowKey={(row) => text(row.day)}
      alwaysOpen={view === 'table'}
    />;
  return rows.length ? <>{view !== 'table' && <EChart option={option} label={`近 ${rangeDays} 天每日状态变化`} />}{(view === 'table' || panel.show_table) && table}</> : <div className="dashboard-panel-state">这个范围还没有记录</div>;
}

function HeatmapPanel({ data, panel, rangeDays }: { data: DashboardPanelData; panel: DashboardPanelModel; rangeDays: number }) {
  const rows = (data.rows ?? []).map((row) => ({
    id: text(row.id),
    name: text(row.name),
    cells: Array.isArray(row.cells) ? row.cells as Array<Record<string, unknown>> : [],
  }));
  const metric = panel.metric === 'online_minutes' ? 'online_minutes' : 'ratio';
  const values = rows.flatMap((row, rowIndex) => row.cells.flatMap((cell, hour) =>
    cell.ratio === null || cell.ratio === undefined ? [] : [[hour, rowIndex, metric === 'online_minutes' ? Math.round(number(cell.online_minutes)) : Math.round(number(cell.ratio) * 100)]],
  ));
  const maximum = metric === 'online_minutes' ? Math.max(60, ...values.map((value) => number(value[2]))) : 100;
  const tableRows = rows.flatMap((row) => row.cells.map((cell, hour) => ({
    id: `${row.id}:${hour}`, name: row.name, hour, ratio: cell.ratio, online_minutes: cell.online_minutes,
  }))).filter((row) => row.ratio !== null && row.ratio !== undefined);
  const option = useMemo(() => chartBase({
    grid: { left: 12, right: 24, top: panel.show_legend ? 42 : 12, bottom: 32, containLabel: true },
    xAxis: { type: 'category', data: Array.from({ length: 24 }, (_, hour) => String(hour).padStart(2, '0')), axisLabel: { color: faintColor }, splitArea: { show: true }, axisTick: { show: false } },
    yAxis: { type: 'category', data: rows.map((row) => row.name), axisLabel: { color: textColor, width: 130, overflow: 'truncate' }, axisTick: { show: false }, axisLine: { show: false }, splitArea: { show: true } },
    visualMap: { min: 0, max: maximum, show: panel.show_legend, orient: 'horizontal', left: 'center', top: 0, inRange: { color: ['#121c24', '#315d55', '#76ac60', '#b6f36b'] }, textStyle: { color: faintColor } },
    tooltip: { appendTo: 'body', position: 'top', formatter: (params: unknown) => {
      const value = (params as { value?: [number, number, number] }).value;
      return value ? `${escapeHtml(rows[value[1]]?.name ?? '')}<br/>${String(value[0]).padStart(2, '0')}:00 · ${metric === 'online_minutes' ? `在线 ${value[2]} 分钟` : `在线比例 ${value[2]}%`}` : '';
    } },
    series: [{ type: 'heatmap', data: values, itemStyle: { borderColor: '#0b1118', borderWidth: 2, borderRadius: 3 }, emphasis: { itemStyle: { borderColor: '#f5f7fa', borderWidth: 1 } } }],
  }), [maximum, metric, panel.show_legend, rows, values]);
  const table = <ChartDataTable
    label="好友时段热力数据"
    columns={[
      { key: 'name', header: '玩家', rowHeader: true, render: (row) => row.name },
      { key: 'hour', header: '时段', render: (row) => `${String(row.hour).padStart(2, '0')}:00` },
      { key: 'ratio', header: metric === 'online_minutes' ? '在线分钟' : '在线比例', render: (row) => metric === 'online_minutes' ? `${Math.round(number(row.online_minutes))} 分钟` : `${Math.round(number(row.ratio) * 100)}%` },
    ]}
    rows={tableRows}
    getRowKey={(row) => row.id}
    alwaysOpen={panel.view === 'table'}
  />;
  return rows.length ? <>{panel.view !== 'table' && <EChart option={option} label={`近 ${rangeDays} 天每位玩家每小时在线比例热力图`} />}{(panel.view === 'table' || panel.show_table) && table}</> : <div className="dashboard-panel-state">这个筛选范围还没有热力数据</div>;
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
        : panel.kind === 'online-now' || panel.kind === 'tracked-count' ? <MetricPanel data={data} panel={panel} />
        : panel.kind === 'collection-coverage' ? panel.metric === 'minutes' ? <MetricPanel data={{ ...data, value: data.observed_minutes, detail: '已覆盖分钟' }} panel={panel} /> : <CoveragePanel data={data} view={panel.view} />
        : panel.kind === 'status-breakdown' || panel.kind === 'platform-breakdown' ? <DistributionPanel data={data} panel={panel} />
        : panel.kind === 'daily-changes' ? <ChangesPanel data={data} panel={panel} rangeDays={rangeDays} />
        : panel.kind === 'friend-heatmap' ? <HeatmapPanel data={data} panel={panel} rangeDays={rangeDays} />
        : <RankingPanel data={data} panel={panel} rangeDays={rangeDays} />}
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
