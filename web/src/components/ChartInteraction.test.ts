import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createElement } from 'react';
import { describe, expect, it, vi } from 'vitest';

import type { PresenceAnalytics } from '../api';
import { ChartDataTable } from './ChartDataTable';
import { isTapGesture, reduceChartSelection } from './ChartInteraction';
import { DailyTimelineChart, PresenceHeatmap } from './PresenceCharts';

if (!window.PointerEvent) {
  class TestPointerEvent extends MouseEvent {
    readonly pointerType: string;

    constructor(type: string, parameters: PointerEventInit = {}) {
      super(type, parameters);
      this.pointerType = parameters.pointerType ?? '';
    }
  }
  Object.defineProperty(window, 'PointerEvent', { configurable: true, value: TestPointerEvent });
}

const timelineRows: PresenceAnalytics['timeline'] = [
  {
    id: 'usr_alice',
    name: 'Alice',
    username: 'alice',
    is_self: false,
    online_minutes: 90,
    spans: [{ start_minute: 480, end_minute: 570, status: 'active' }],
  },
];

const unavailableCells: PresenceAnalytics['heatmap'][number]['cells'] = Array.from(
  { length: 24 },
  (_, hour) => ({
    ratio: hour === 0 ? null : 0.25,
    online_minutes: hour === 0 ? 0 : 15,
    observed_minutes: hour === 0 ? 12 : 60,
    eligible_minutes: 60,
    covered_days: hour === 0 ? 1 : 30,
    range_days: 30,
  }),
);

const heatmapRows: PresenceAnalytics['heatmap'] = [
  {
    id: 'usr_alice',
    name: 'Alice',
    is_self: false,
    tracking_started_at: '2026-08-01T00:00:00Z',
    cells: unavailableCells,
    values: Array.from({ length: 24 }, () => 0.25),
  },
];

const mockSvgBounds = (svg: SVGSVGElement) => {
  vi.spyOn(svg, 'getBoundingClientRect').mockReturnValue({
    x: 0,
    y: 0,
    top: 0,
    left: 0,
    right: 1120,
    bottom: 240,
    width: 1120,
    height: 240,
    toJSON: () => ({}),
  });
};

describe('chart selection reducer', () => {
  it('pins a touch selection and clears it on a second tap', () => {
    const first = reduceChartSelection(null, { type: 'tap', row: 2, column: 8 });
    expect(first).toEqual({ row: 2, column: 8, pinned: true });
    expect(reduceChartSelection(first, { type: 'tap', row: 2, column: 8 })).toBeNull();
  });

  it('keeps pinned selections when a pointer leaves and clears transient hover', () => {
    const hovered = reduceChartSelection(null, { type: 'hover', row: 1, column: 4 });
    expect(reduceChartSelection(hovered, { type: 'leave' })).toBeNull();

    const pinned = reduceChartSelection(null, { type: 'tap', row: 1, column: 4 });
    expect(reduceChartSelection(pinned, { type: 'leave' })).toEqual(pinned);
  });

  it('moves a keyboard selection within chart bounds', () => {
    const focused = reduceChartSelection(null, {
      type: 'move',
      rowDelta: -1,
      columnDelta: 1,
      rowCount: 3,
      columnCount: 24,
    });
    expect(focused).toEqual({ row: 0, column: 1, pinned: true });

    expect(reduceChartSelection(focused, {
      type: 'move',
      rowDelta: 8,
      columnDelta: 30,
      rowCount: 3,
      columnCount: 24,
    })).toEqual({ row: 2, column: 23, pinned: true });
  });

  it('distinguishes a tap from a scrolling gesture', () => {
    expect(isTapGesture({ x: 10, y: 10 }, { x: 14, y: 15 })).toBe(true);
    expect(isTapGesture({ x: 10, y: 10 }, { x: 10, y: 30 })).toBe(false);
  });
});

describe('touch-readable accessible charts', () => {
  it('pins a timeline selection on touch and clears it on the second tap', () => {
    render(createElement(DailyTimelineChart, { rows: timelineRows }));
    const chart = screen.getByRole('img', { name: /每位玩家在所选日期的在线时段/ });
    mockSvgBounds(chart as unknown as SVGSVGElement);

    fireEvent.pointerDown(chart, { pointerType: 'touch', clientX: 480, clientY: 38 });
    fireEvent.pointerUp(chart, { pointerType: 'touch', clientX: 480, clientY: 38 });
    expect(screen.getByText(/Alice · 08:/)).toBeVisible();

    fireEvent.pointerDown(chart, { pointerType: 'touch', clientX: 480, clientY: 38 });
    fireEvent.pointerUp(chart, { pointerType: 'touch', clientX: 480, clientY: 38 });
    expect(screen.queryByText(/Alice · 08:/)).not.toBeInTheDocument();
  });

  it('leaves vertical touch movement to page scrolling', () => {
    render(createElement(DailyTimelineChart, { rows: timelineRows }));
    const chart = screen.getByRole('img', { name: /每位玩家在所选日期的在线时段/ });
    mockSvgBounds(chart as unknown as SVGSVGElement);

    fireEvent.pointerDown(chart, { pointerType: 'touch', clientX: 480, clientY: 38 });
    expect(fireEvent.pointerMove(chart, { pointerType: 'touch', clientX: 480, clientY: 90 })).toBe(true);
    fireEvent.pointerUp(chart, { pointerType: 'touch', clientX: 480, clientY: 90 });

    expect(document.querySelector('.chart-hover-line')).not.toBeInTheDocument();
  });

  it('shows a vertical line and row highlight while a mouse hovers', () => {
    render(createElement(DailyTimelineChart, { rows: timelineRows }));
    const chart = screen.getByRole('img', { name: /每位玩家在所选日期的在线时段/ });
    mockSvgBounds(chart as unknown as SVGSVGElement);

    fireEvent.pointerMove(chart, { pointerType: 'mouse', clientX: 480, clientY: 38 });
    expect(document.querySelector('.chart-hover-line')).toBeInTheDocument();
    expect(document.querySelector('.chart-row-hover')).toBeInTheDocument();

    fireEvent.pointerLeave(chart, { pointerType: 'mouse' });
    expect(document.querySelector('.chart-hover-line')).not.toBeInTheDocument();
  });

  it('supports keyboard inspection with a vertical line and row highlight', () => {
    render(createElement(DailyTimelineChart, { rows: timelineRows }));
    const chart = screen.getByRole('img', { name: /每位玩家在所选日期的在线时段/ });

    fireEvent.focus(chart);
    fireEvent.keyDown(chart, { key: 'Home' });
    fireEvent.keyDown(chart, { key: 'ArrowRight' });

    expect(chart).toHaveAttribute('tabindex', '0');
    expect(document.querySelector('.chart-hover-line')).toBeInTheDocument();
    expect(document.querySelector('.chart-row-hover')).toBeInTheDocument();
    expect(screen.getByText('Alice · 00:15')).toBeVisible();
  });

  it('keeps a null heatmap ratio unavailable and explains the recorded range naturally', () => {
    render(createElement(PresenceHeatmap, {
      rows: heatmapRows,
      observedMinutes: Array.from({ length: 24 }, () => 60),
    }));
    const chart = screen.getByRole('img', { name: /每位玩家每小时平均在线比例/ });

    fireEvent.focus(chart);
    fireEvent.keyDown(chart, { key: 'Home' });

    expect(screen.getAllByText('—').length).toBeGreaterThan(0);
    expect(screen.getByText('这一小时的数据还不够计算比例')).toBeVisible();
    expect(screen.getByText('已有记录 12 分钟 · 可记录 60 分钟')).toBeVisible();
    expect(screen.getByText('覆盖 1 / 30 天')).toBeVisible();
    expect(screen.queryByText(/证据|只读|协议/)).not.toBeInTheDocument();
  });

  it('mounts a native table only after its disclosure is expanded', async () => {
    const user = userEvent.setup();
    type TestRow = { person: string; start: string };
    const TestChartDataTable = ChartDataTable<TestRow>;
    render(createElement(TestChartDataTable, {
      label: '在线时间轴数据',
      summary: '查看数据表',
      columns: [
        { key: 'person', header: '玩家', rowHeader: true, render: (row) => row.person },
        { key: 'start', header: '开始', render: (row) => row.start },
      ],
      rows: [{ person: 'Alice', start: '08:00' }],
      getRowKey: (row) => row.person,
    }));

    const disclosure = screen.getByRole('button', { name: '查看数据表' });
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
    await user.click(disclosure);

    const table = await screen.findByRole('table', { name: '在线时间轴数据' });
    expect(within(table).getByRole('columnheader', { name: '玩家' })).toBeVisible();
    expect(within(table).getByRole('columnheader', { name: '开始' })).toBeVisible();
    expect(within(table).getByRole('rowheader', { name: 'Alice' })).toBeVisible();
    expect(within(table).getByRole('cell', { name: '08:00' })).toBeVisible();
  });
});
