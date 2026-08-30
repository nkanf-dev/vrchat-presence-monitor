import { useReducer, useRef } from 'react';
import type {
  KeyboardEvent as ReactKeyboardEvent,
  PointerEvent as ReactPointerEvent,
} from 'react';

import { formatClock, formatMinutes, presenceColor } from '../analytics';
import type { PresenceAnalytics } from '../api';
import { statusLabel } from '../format';
import { ChartDataTable } from './ChartDataTable';
import {
  isTapGesture,
  reduceChartSelection,
} from './ChartInteraction';
import type { PointerPosition } from './ChartInteraction';
import { ChartViewport } from './ChartViewport';

const WIDTH = 1120;
const LEFT = 176;
const RIGHT = 62;
const TOP = 38;
const ROW_HEIGHT = 31;

type Coverage = NonNullable<PresenceAnalytics['coverage']>;

const pointInSvg = (event: ReactPointerEvent<SVGSVGElement>, height: number) => {
  const bounds = event.currentTarget.getBoundingClientRect();
  return {
    x: ((event.clientX - bounds.left) / bounds.width) * WIDTH,
    y: ((event.clientY - bounds.top) / bounds.height) * height,
  };
};

const clientPoint = (event: ReactPointerEvent<SVGSVGElement>): PointerPosition => ({
  x: event.clientX,
  y: event.clientY,
});

const handleChartKey = (
  event: ReactKeyboardEvent<SVGSVGElement>,
  dispatch: (action: Parameters<typeof reduceChartSelection>[1]) => void,
  rowCount: number,
  columnCount: number,
  columnStep: number,
) => {
  let rowDelta = 0;
  let columnDelta = 0;

  switch (event.key) {
    case 'ArrowLeft':
      columnDelta = -columnStep;
      break;
    case 'ArrowRight':
      columnDelta = columnStep;
      break;
    case 'ArrowUp':
      rowDelta = -1;
      break;
    case 'ArrowDown':
      rowDelta = 1;
      break;
    case 'Home':
      columnDelta = -columnCount;
      break;
    case 'End':
      columnDelta = columnCount;
      break;
    case 'Escape':
      event.preventDefault();
      dispatch({ type: 'clear' });
      return;
    default:
      return;
  }

  event.preventDefault();
  dispatch({ type: 'move', rowDelta, columnDelta, rowCount, columnCount });
};

function Tooltip({ x, lines }: { x: number; lines: string[] }) {
  const width = Math.min(340, Math.max(154, ...lines.map((line) => Array.from(line).length * 7 + 22)));
  const left = Math.max(6, Math.min(WIDTH - width - 6, x - width / 2));
  const height = lines.length * 16 + 10;
  return (
    <g className="svg-tooltip" pointerEvents="none">
      <rect x={left} y={16} width={width} height={height} rx={7} />
      {lines.map((line, index) => (
        <text key={`${line}-${index}`} x={left + 10} y={32 + index * 16} className={index === 0 ? 'primary' : ''}>
          {line}
        </text>
      ))}
    </g>
  );
}

function TimeGrid({ bottom }: { bottom: number }) {
  const chartWidth = WIDTH - LEFT - RIGHT;
  return (
    <>
      {Array.from({ length: 25 }, (_, hour) => {
        const x = LEFT + (chartWidth * hour) / 24;
        return (
          <g key={hour}>
            <line className="chart-grid-line" x1={x} y1={TOP - 18} x2={x} y2={bottom} />
            {hour < 24 && <text className="chart-axis-label" x={x} y={15}>{String(hour).padStart(2, '0')}</text>}
          </g>
        );
      })}
    </>
  );
}

const coverageText = (coverage: Coverage | undefined) => {
  if (!coverage) return '—';
  return `${formatMinutes(coverage.observed_minutes)} / ${formatMinutes(coverage.expected_minutes)}`;
};

type TimelineTableRow = {
  key: string;
  person: string;
  start: string;
  end: string;
  duration: string;
  status: string;
  location: string;
  coverage: string;
};

export function DailyTimelineChart({
  rows,
  coverage,
}: {
  rows: PresenceAnalytics['timeline'];
  coverage?: Coverage;
}) {
  const [selection, dispatch] = useReducer(reduceChartSelection, null);
  const touchStart = useRef<PointerPosition | null>(null);
  const height = Math.max(210, TOP + rows.length * ROW_HEIGHT + 30);
  const chartWidth = WIDTH - LEFT - RIGHT;
  const selectedRow = selection && selection.row >= 0 && selection.row < rows.length
    ? rows[selection.row]
    : null;
  const selectedSpan = selectedRow?.spans.find(
    (span) => selection && selection.column >= span.start_minute && selection.column <= span.end_minute,
  );
  const tooltipLines = selection
    ? selectedRow
      ? [
          `${selectedRow.name || selectedRow.username} · ${formatClock(selection.column)}`,
          selectedSpan ? statusLabel(selectedSpan.status) : '这个时段没有在线记录',
          ...(coverage ? [`当天记录 ${coverageText(coverage)}`] : []),
        ]
      : [formatClock(selection.column)]
    : [];
  const tableRows: TimelineTableRow[] = rows.flatMap((row) => row.spans.map((span, index) => ({
    key: `${row.id}-${span.start_minute}-${span.end_minute}-${index}`,
    person: `${row.name || row.username}${row.is_self ? '（自己）' : ''}`,
    start: formatClock(span.start_minute),
    end: formatClock(span.end_minute),
    duration: formatMinutes(span.end_minute - span.start_minute),
    status: statusLabel(span.status),
    location: '—',
    coverage: coverageText(coverage),
  })));

  return (
    <>
      <ChartViewport
        routeKey="dailyX"
        label="每日在线时间轴，可横向滚动查看全天"
        stickyContext={{
          width: WIDTH,
          plotLeft: LEFT,
          plotRight: RIGHT,
          rows: rows.map((row, index) => ({
            key: row.id,
            label: `${row.name || row.username}${row.is_self ? '（自己）' : ''}`,
            top: TOP + index * ROW_HEIGHT - 12,
            height: 24,
            active: selection?.row === index,
          })),
        }}
      >
        <svg
          className="analytics-chart"
          width={WIDTH}
          height={height}
          viewBox={`0 0 ${WIDTH} ${height}`}
          role="img"
          tabIndex={0}
          aria-label={`每位玩家在所选日期的在线时段${tooltipLines.length ? `。${tooltipLines.join('。')}` : ''}`}
          onFocus={(event) => {
            if (rows.length && event.currentTarget.matches(':focus-visible')) {
              dispatch({ type: 'focus', row: 0, column: 0 });
            }
          }}
          onKeyDown={(event) => handleChartKey(event, dispatch, rows.length, 1441, 15)}
          onPointerDown={(event) => {
            if (event.pointerType === 'touch') touchStart.current = clientPoint(event);
          }}
          onPointerUp={(event) => {
            if (event.pointerType !== 'touch' || !touchStart.current) return;
            const end = clientPoint(event);
            const isTap = isTapGesture(touchStart.current, end);
            touchStart.current = null;
            if (!isTap) return;
            const point = pointInSvg(event, height);
            const row = Math.floor((point.y - TOP + ROW_HEIGHT / 2) / ROW_HEIGHT);
            if (row < 0 || row >= rows.length || point.x < LEFT || point.x > WIDTH - RIGHT) return;
            const minute = Math.round(Math.max(0, Math.min(1440, ((point.x - LEFT) / chartWidth) * 1440)));
            dispatch({ type: 'tap', row, column: minute });
          }}
          onPointerCancel={() => { touchStart.current = null; }}
          onPointerMove={(event) => {
            if (event.pointerType === 'touch') return;
            const point = pointInSvg(event, height);
            const minute = Math.max(0, Math.min(1440, ((point.x - LEFT) / chartWidth) * 1440));
            const row = Math.floor((point.y - TOP + ROW_HEIGHT / 2) / ROW_HEIGHT);
            dispatch({ type: 'hover', row, column: minute });
          }}
          onPointerLeave={() => dispatch({ type: 'leave' })}
        >
          <TimeGrid bottom={TOP + rows.length * ROW_HEIGHT} />
          {rows.map((row, index) => {
            const y = TOP + index * ROW_HEIGHT;
            const highlighted = selection?.row === index;
            return (
              <g key={row.id}>
                <rect
                  className={highlighted ? 'chart-row chart-row-hover' : 'chart-row'}
                  x={0}
                  y={y - 12}
                  width={WIDTH}
                  height={24}
                />
                <text className={highlighted ? 'chart-row-label highlighted' : 'chart-row-label'} x={10} y={y + 4}>
                  {`${row.name || row.username}${row.is_self ? '（自己）' : ''}`}
                </text>
                {row.spans.map((span, spanIndex) => {
                  const x = LEFT + (chartWidth * span.start_minute) / 1440;
                  const width = Math.max(3, (chartWidth * (span.end_minute - span.start_minute)) / 1440);
                  return (
                    <rect
                      key={`${span.start_minute}-${span.end_minute}-${spanIndex}`}
                      className="presence-span"
                      x={x}
                      y={y - 8}
                      width={width}
                      height={16}
                      rx={4}
                      fill={presenceColor(span.status)}
                    />
                  );
                })}
                <text className="chart-duration" x={WIDTH - 8} y={y + 4}>{formatMinutes(row.online_minutes)}</text>
              </g>
            );
          })}
          {selection && (
            <>
              <line
                className="chart-hover-line"
                x1={LEFT + (chartWidth * selection.column) / 1440}
                y1={TOP - 20}
                x2={LEFT + (chartWidth * selection.column) / 1440}
                y2={TOP + rows.length * ROW_HEIGHT}
              />
              <Tooltip x={LEFT + (chartWidth * selection.column) / 1440} lines={tooltipLines} />
            </>
          )}
        </svg>
      </ChartViewport>
      <ChartDataTable<TimelineTableRow>
        label="每日在线时间轴数据"
        columns={[
          { key: 'person', header: '玩家', rowHeader: true, render: (row) => row.person },
          { key: 'start', header: '开始', render: (row) => row.start },
          { key: 'end', header: '结束', render: (row) => row.end },
          { key: 'duration', header: '时长', render: (row) => row.duration },
          { key: 'status', header: '状态', render: (row) => row.status },
          { key: 'location', header: '位置 / 世界', render: (row) => row.location },
          { key: 'coverage', header: '当天记录', render: (row) => row.coverage },
        ]}
        rows={tableRows}
        getRowKey={(row) => row.key}
        emptyMessage="当天没有可列出的在线时段。"
      />
    </>
  );
}

const heatColor = (value: number) => {
  const intensity = Math.max(0, Math.min(1, value));
  const lightness = 13 + intensity * 59;
  const saturation = 22 + intensity * 58;
  return `hsl(88 ${saturation}% ${lightness}%)`;
};

type HeatmapCell = PresenceAnalytics['heatmap'][number]['cells'][number];

const heatmapCell = (
  row: PresenceAnalytics['heatmap'][number],
  hour: number,
  observedMinutes: PresenceAnalytics['heatmap_observed_minutes'],
): HeatmapCell => {
  const cell = row.cells[hour];
  if (cell) return cell;
  const observed = observedMinutes[hour] ?? 0;
  const legacyValue = row.values[hour];
  return {
    ratio: observed > 0 && legacyValue !== undefined ? legacyValue : null,
    online_minutes: observed > 0 && legacyValue !== undefined ? observed * legacyValue : 0,
    observed_minutes: observed,
    eligible_minutes: observed,
    covered_days: observed > 0 ? 1 : 0,
    range_days: 1,
  };
};

const heatmapCellLines = (cell: HeatmapCell) => {
  const timing = `已有记录 ${Math.round(cell.observed_minutes)} 分钟 · 可记录 ${Math.round(cell.eligible_minutes)} 分钟`;
  const days = `覆盖 ${cell.covered_days} / ${cell.range_days} 天`;
  if (cell.ratio === null) {
    return [
      cell.observed_minutes > 0 ? '这一小时的数据还不够计算比例' : '这一小时还没有记录',
      timing,
      days,
    ];
  }
  return [
    `在线 ${Math.round(cell.ratio * 100)}% · ${Math.round(cell.online_minutes)} 分钟`,
    timing,
    days,
  ];
};

const heatmapCellValue = (cell: HeatmapCell) => cell.ratio === null ? '—' : `${Math.round(cell.ratio * 100)}%`;

export function PresenceHeatmap({
  rows,
  observedMinutes,
}: {
  rows: PresenceAnalytics['heatmap'];
  observedMinutes: PresenceAnalytics['heatmap_observed_minutes'];
}) {
  const [selection, dispatch] = useReducer(reduceChartSelection, null);
  const touchStart = useRef<PointerPosition | null>(null);
  const height = Math.max(230, TOP + rows.length * ROW_HEIGHT + 24);
  const chartWidth = WIDTH - LEFT - 24;
  const cellWidth = chartWidth / 24;
  const selectedRow = selection && selection.row >= 0 && selection.row < rows.length
    ? rows[selection.row]
    : null;
  const selectedCell = selectedRow && selection
    ? heatmapCell(selectedRow, selection.column, observedMinutes)
    : null;
  const tooltipX = selection ? LEFT + cellWidth * (selection.column + 0.5) : LEFT;
  const tooltipLines = selectedRow && selection && selectedCell
    ? [
        `${selectedRow.name} · ${String(selection.column).padStart(2, '0')}:00–${String(selection.column + 1).padStart(2, '0')}:00`,
        ...heatmapCellLines(selectedCell),
      ]
    : [];

  return (
    <>
      <ChartViewport
        routeKey="heatX"
        label="每位玩家每小时平均在线比例热力图，可横向滚动"
        stickyContext={{
          width: WIDTH,
          plotLeft: LEFT,
          plotRight: 24,
          hourPosition: 'center',
          rows: rows.map((row, index) => ({
            key: row.id,
            label: `${row.name}${row.is_self ? '（自己）' : ''}`,
            top: TOP + index * ROW_HEIGHT - 13,
            height: 26,
            active: selection?.row === index,
          })),
        }}
      >
        <svg
          className="analytics-chart"
          width={WIDTH}
          height={height}
          viewBox={`0 0 ${WIDTH} ${height}`}
          role="img"
          tabIndex={0}
          aria-label={`每位玩家每小时平均在线比例${tooltipLines.length ? `。${tooltipLines.join('。')}` : ''}`}
          onFocus={(event) => {
            if (rows.length && event.currentTarget.matches(':focus-visible')) {
              dispatch({ type: 'focus', row: 0, column: 0 });
            }
          }}
          onKeyDown={(event) => handleChartKey(event, dispatch, rows.length, 24, 1)}
          onPointerDown={(event) => {
            if (event.pointerType === 'touch') touchStart.current = clientPoint(event);
          }}
          onPointerUp={(event) => {
            if (event.pointerType !== 'touch' || !touchStart.current) return;
            const end = clientPoint(event);
            const isTap = isTapGesture(touchStart.current, end);
            touchStart.current = null;
            if (!isTap) return;
            const point = pointInSvg(event, height);
            const row = Math.floor((point.y - TOP + ROW_HEIGHT / 2) / ROW_HEIGHT);
            if (row < 0 || row >= rows.length || point.x < LEFT || point.x > WIDTH - 24) return;
            const hour = Math.max(0, Math.min(23, Math.floor((point.x - LEFT) / cellWidth)));
            dispatch({ type: 'tap', row, column: hour });
          }}
          onPointerCancel={() => { touchStart.current = null; }}
          onPointerMove={(event) => {
            if (event.pointerType === 'touch') return;
            const point = pointInSvg(event, height);
            const row = Math.floor((point.y - TOP + ROW_HEIGHT / 2) / ROW_HEIGHT);
            const hour = Math.max(0, Math.min(23, Math.floor((point.x - LEFT) / cellWidth)));
            dispatch({ type: 'hover', row, column: hour });
          }}
          onPointerLeave={() => dispatch({ type: 'leave' })}
        >
          {Array.from({ length: 24 }, (_, hour) => (
            <text key={hour} className="chart-axis-label" x={LEFT + cellWidth * (hour + 0.5)} y={15}>
              {String(hour).padStart(2, '0')}
            </text>
          ))}
          {rows.map((row, rowIndex) => {
            const y = TOP + rowIndex * ROW_HEIGHT;
            const highlighted = selection?.row === rowIndex;
            return (
              <g key={row.id}>
                <rect
                  className={highlighted ? 'chart-row chart-row-hover' : 'chart-row'}
                  x={0}
                  y={y - 13}
                  width={WIDTH}
                  height={26}
                />
                <text className={highlighted ? 'chart-row-label highlighted' : 'chart-row-label'} x={10} y={y + 4}>
                  {`${row.name}${row.is_self ? '（自己）' : ''}`}
                </text>
                {Array.from({ length: 24 }, (_, hour) => {
                  const cell = heatmapCell(row, hour, observedMinutes);
                  const selected = selection?.row === rowIndex && selection.column === hour;
                  return (
                    <g key={hour}>
                      <rect
                        className={`${selected ? 'heat-cell selected' : 'heat-cell'}${cell.ratio === null ? ' unobserved' : ''}`}
                        x={LEFT + hour * cellWidth + 1.5}
                        y={y - 11}
                        width={cellWidth - 3}
                        height={22}
                        rx={3}
                        fill={cell.ratio === null ? '#111821' : heatColor(cell.ratio)}
                      />
                      {cell.ratio === null ? (
                        <text className="heat-value unavailable" x={LEFT + cellWidth * (hour + 0.5)} y={y + 4}>—</text>
                      ) : cell.ratio >= 0.08 && (
                        <text className={cell.ratio > 0.55 ? 'heat-value dark' : 'heat-value'} x={LEFT + cellWidth * (hour + 0.5)} y={y + 4}>
                          {Math.round(cell.ratio * 100)}
                        </text>
                      )}
                    </g>
                  );
                })}
              </g>
            );
          })}
          {selection && (
            <line
              className="chart-hover-line"
              x1={tooltipX}
              y1={TOP - 20}
              x2={tooltipX}
              y2={TOP + rows.length * ROW_HEIGHT}
            />
          )}
          {selectedRow && selection && selectedCell && <Tooltip x={tooltipX} lines={tooltipLines} />}
        </svg>
      </ChartViewport>
      <ChartDataTable<PresenceAnalytics['heatmap'][number]>
        label="每好友每小时在线热力图数据"
        columns={[
          { key: 'person', header: '玩家', rowHeader: true, render: (row) => `${row.name}${row.is_self ? '（自己）' : ''}` },
          ...Array.from({ length: 24 }, (_, hour) => ({
            key: `hour-${hour}`,
            header: `${String(hour).padStart(2, '0')}:00`,
            render: (row: PresenceAnalytics['heatmap'][number]) => {
              const cell = heatmapCell(row, hour, observedMinutes);
              return (
                <span className="chart-data-value" aria-label={heatmapCellLines(cell).join('，')}>
                  <strong>{heatmapCellValue(cell)}</strong>
                  <small>{Math.round(cell.observed_minutes)} / {Math.round(cell.eligible_minutes)} 分钟</small>
                </span>
              );
            },
          })),
        ]}
        rows={rows}
        getRowKey={(row) => row.id}
        emptyMessage="这个范围没有可列出的热力图数据。"
      />
    </>
  );
}
