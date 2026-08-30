import { useId, useReducer, useRef } from 'react';
import type {
  KeyboardEvent as ReactKeyboardEvent,
  PointerEvent as ReactPointerEvent,
} from 'react';

import { formatClock, formatMinutes, worldName } from '../analytics';
import type { WorldAnalytics, WorldInfo, WorldSpan } from '../api';
import { worldImageUrl } from '../api';
import { statusLabel } from '../format';
import { ChartDataTable } from './ChartDataTable';
import { isTapGesture, reduceChartSelection } from './ChartInteraction';
import type { PointerPosition } from './ChartInteraction';
import { ChartViewport } from './ChartViewport';

const WIDTH = 1180;
const LEFT = 210;
const RIGHT = 68;
const TOP = 42;

type Coverage = NonNullable<WorldAnalytics['coverage']>;

const svgPoint = (event: ReactPointerEvent<SVGSVGElement>, height: number) => {
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
) => {
  if (event.target !== event.currentTarget) return;
  let rowDelta = 0;
  let columnDelta = 0;

  switch (event.key) {
    case 'ArrowLeft':
      columnDelta = -15;
      break;
    case 'ArrowRight':
      columnDelta = 15;
      break;
    case 'ArrowUp':
      rowDelta = -1;
      break;
    case 'ArrowDown':
      rowDelta = 1;
      break;
    case 'Home':
      columnDelta = -1441;
      break;
    case 'End':
      columnDelta = 1441;
      break;
    case 'Escape':
      event.preventDefault();
      dispatch({ type: 'clear' });
      return;
    default:
      return;
  }

  event.preventDefault();
  dispatch({ type: 'move', rowDelta, columnDelta, rowCount, columnCount: 1441 });
};

function TimeGrid({ bottom }: { bottom: number }) {
  const chartWidth = WIDTH - LEFT - RIGHT;
  return (
    <>
      {Array.from({ length: 25 }, (_, hour) => {
        const x = LEFT + (chartWidth * hour) / 24;
        return (
          <g key={hour}>
            <line className="chart-grid-line" x1={x} y1={TOP - 23} x2={x} y2={bottom} />
            {hour < 24 && <text className="chart-axis-label" x={x} y={15}>{String(hour).padStart(2, '0')}</text>}
          </g>
        );
      })}
    </>
  );
}

function WorldTooltip({ x, lines }: { x: number; lines: string[] }) {
  const width = Math.min(390, Math.max(176, ...lines.map((line) => Array.from(line).length * 7 + 24)));
  const left = Math.max(6, Math.min(WIDTH - width - 6, x - width / 2));
  const height = lines.length * 16 + 12;
  return (
    <g className="svg-tooltip" pointerEvents="none">
      <rect x={left} y={18} width={width} height={height} rx={8} />
      {lines.map((line, index) => (
        <text key={`${line}-${index}`} x={left + 11} y={35 + index * 16} className={index === 0 ? 'primary' : ''}>
          {line}
        </text>
      ))}
    </g>
  );
}

function WorldBar({
  span,
  x,
  y,
  width,
  height,
  color,
  info,
  gradientPrefix,
  index,
  onOpen,
}: {
  span: WorldSpan;
  x: number;
  y: number;
  width: number;
  height: number;
  color: string;
  info: WorldInfo | undefined;
  gradientPrefix: string;
  index: number;
  onOpen: (worldId: string) => void;
}) {
  const visibleWidth = Math.max(4, width);
  const gradientId = `${gradientPrefix}-gradient-${index}`;
  const clipId = `${gradientPrefix}-clip-${index}`;
  const source = info?.thumbnail_url || info?.image_url || '';
  const imageWidth = Math.min(120, Math.max(44, visibleWidth * 0.46));
  return (
    <g
      className="world-bar"
      role="button"
      tabIndex={0}
      aria-label={`查看世界：${worldName(span.world_id, info)}`}
      onClick={() => onOpen(span.world_id)}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          onOpen(span.world_id);
        }
      }}
    >
      <defs>
        <clipPath id={clipId}>
          <rect x={x} y={y - height / 2} width={visibleWidth} height={height} rx={5} />
        </clipPath>
        <linearGradient id={gradientId} x1="0" x2="1">
          <stop offset="0" stopColor={color} stopOpacity="0.16" />
          <stop offset="0.36" stopColor={color} stopOpacity="0.46" />
          <stop offset="1" stopColor={color} stopOpacity="0.82" />
        </linearGradient>
      </defs>
      <rect x={x} y={y - height / 2} width={visibleWidth} height={height} rx={5} fill={color} opacity="0.26" />
      {source && visibleWidth >= 10 && (
        <image
          href={worldImageUrl(source)}
          x={x}
          y={y - height / 2}
          width={imageWidth}
          height={height}
          preserveAspectRatio="xMidYMid slice"
          clipPath={`url(#${clipId})`}
          opacity="0.96"
        />
      )}
      <rect x={x} y={y - height / 2} width={visibleWidth} height={height} rx={5} fill={`url(#${gradientId})`} />
      <rect x={x + 0.5} y={y - height / 2 + 0.5} width={Math.max(3, visibleWidth - 1)} height={height - 1} rx={4.5} fill="none" stroke={color} strokeOpacity="0.72" />
    </g>
  );
}

const coverageText = (coverage: Coverage | undefined) => {
  if (!coverage) return '—';
  return `${formatMinutes(coverage.observed_minutes)} / ${formatMinutes(coverage.expected_minutes)}`;
};

const locationText = (span: WorldSpan, info: Map<string, WorldInfo>) => {
  const name = worldName(span.world_id, info.get(span.world_id));
  const detail = span.location || span.platform;
  return detail && detail !== span.world_id ? `${name} · ${detail}` : name;
};

type WorldTableRow = {
  key: string;
  person: string;
  start: string;
  end: string;
  duration: string;
  status: string;
  location: string;
  coverage: string;
};

const worldTableRow = (
  person: WorldAnalytics['friends'][number],
  span: WorldSpan,
  index: number,
  info: Map<string, WorldInfo>,
  coverage: Coverage | undefined,
): WorldTableRow => ({
  key: `${person.id}-${span.world_id}-${span.start_minute}-${span.end_minute}-${index}`,
  person: `${person.name}${person.is_self ? '（自己）' : ''}`,
  start: formatClock(span.start_minute),
  end: formatClock(span.end_minute),
  duration: formatMinutes(span.end_minute - span.start_minute),
  status: statusLabel(span.status),
  location: locationText(span, info),
  coverage: coverageText(coverage),
});

const worldTableColumns = [
  { key: 'person', header: '玩家', rowHeader: true, render: (row: WorldTableRow) => row.person },
  { key: 'start', header: '开始', render: (row: WorldTableRow) => row.start },
  { key: 'end', header: '结束', render: (row: WorldTableRow) => row.end },
  { key: 'duration', header: '时长', render: (row: WorldTableRow) => row.duration },
  { key: 'status', header: '状态', render: (row: WorldTableRow) => row.status },
  { key: 'location', header: '位置 / 世界', render: (row: WorldTableRow) => row.location },
  { key: 'coverage', header: '当天记录', render: (row: WorldTableRow) => row.coverage },
];

export function PersonWorldChart({
  person,
  info,
  colors,
  coverage,
  onOpenWorld,
}: {
  person: WorldAnalytics['friends'][number];
  info: Map<string, WorldInfo>;
  colors: Map<string, string>;
  coverage?: Coverage;
  onOpenWorld: (worldId: string) => void;
}) {
  const prefix = useId().replace(/:/g, '');
  const [selection, dispatch] = useReducer(reduceChartSelection, null);
  const touchStart = useRef<PointerPosition | null>(null);
  const worldIds = [...new Set(person.spans.map((span) => span.world_id))];
  const rows = worldIds.length ? worldIds : [''];
  const rowHeight = 56;
  const height = Math.max(190, TOP + rows.length * rowHeight + 38);
  const chartWidth = WIDTH - LEFT - RIGHT;
  const selectedWorldId = selection && selection.row >= 0 && selection.row < rows.length
    ? rows[selection.row] ?? ''
    : '';
  const selectedSpan = person.spans.find((span) =>
    selection
    && span.world_id === selectedWorldId
    && selection.column >= span.start_minute
    && selection.column <= span.end_minute,
  );
  const tooltipLines = selection
    ? selectedSpan
      ? [
          `${person.name} · ${formatClock(selection.column)}`,
          `${worldName(selectedSpan.world_id, info.get(selectedSpan.world_id))} · ${statusLabel(selectedSpan.status)}`,
          selectedSpan.location || selectedSpan.platform || '位置未公开',
          ...(coverage ? [`当天记录 ${coverageText(coverage)}`] : []),
        ]
      : [`${person.name} · ${formatClock(selection.column)}`, '这个时段没有在线记录']
    : [];
  const tableRows = person.spans.map((span, index) => worldTableRow(person, span, index, info, coverage));

  return (
    <>
      <ChartViewport
        routeKey="worldPersonX"
        label="所选玩家的世界时间轴，可横向滚动查看全天"
        stickyContext={{
          width: WIDTH,
          plotLeft: LEFT,
          plotRight: RIGHT,
          rows: rows.map((worldId, rowIndex) => ({
            key: worldId || 'empty',
            label: worldId ? worldName(worldId, info.get(worldId)) : '当天没有世界记录',
            top: TOP + rowIndex * rowHeight - 21,
            height: 42,
            active: selection?.row === rowIndex,
          })),
        }}
      >
        <svg
          className="analytics-chart world-detail-chart"
          width={WIDTH}
          height={height}
          viewBox={`0 0 ${WIDTH} ${height}`}
          role="img"
          tabIndex={0}
          aria-label={`${person.name} 的在线与游玩世界时间轴${tooltipLines.length ? `。${tooltipLines.join('。')}` : ''}`}
          onFocus={(event) => {
            if (rows.length && event.currentTarget.matches(':focus-visible')) {
              dispatch({ type: 'focus', row: 0, column: 0 });
            }
          }}
          onKeyDown={(event) => handleChartKey(event, dispatch, rows.length)}
          onPointerDown={(event) => {
            if (event.pointerType === 'touch') touchStart.current = clientPoint(event);
          }}
          onPointerUp={(event) => {
            if (event.pointerType !== 'touch' || !touchStart.current) return;
            const end = clientPoint(event);
            const isTap = isTapGesture(touchStart.current, end);
            touchStart.current = null;
            if (!isTap) return;
            const point = svgPoint(event, height);
            const row = Math.floor((point.y - TOP + rowHeight / 2) / rowHeight);
            if (row < 0 || row >= rows.length || point.x < LEFT || point.x > WIDTH - RIGHT) return;
            const minute = Math.round(Math.max(0, Math.min(1440, ((point.x - LEFT) / chartWidth) * 1440)));
            dispatch({ type: 'tap', row, column: minute });
          }}
          onPointerCancel={() => { touchStart.current = null; }}
          onPointerMove={(event) => {
            if (event.pointerType === 'touch') return;
            const point = svgPoint(event, height);
            dispatch({
              type: 'hover',
              column: Math.max(0, Math.min(1440, ((point.x - LEFT) / chartWidth) * 1440)),
              row: Math.floor((point.y - TOP + rowHeight / 2) / rowHeight),
            });
          }}
          onPointerLeave={() => dispatch({ type: 'leave' })}
        >
          <TimeGrid bottom={TOP + rows.length * rowHeight} />
          {rows.map((worldId, rowIndex) => {
            const y = TOP + rowIndex * rowHeight;
            const worldInfo = info.get(worldId);
            const highlighted = selection?.row === rowIndex;
            return (
              <g key={worldId || 'empty'}>
                <rect className={highlighted ? 'chart-row chart-row-hover' : 'chart-row'} x={0} y={y - 21} width={WIDTH} height={42} />
                <text className={highlighted ? 'chart-row-label highlighted' : 'chart-row-label'} x={10} y={y + 4}>
                  {worldId ? worldName(worldId, worldInfo) : '当天没有世界记录'}
                </text>
                {person.spans.filter((span) => span.world_id === worldId).map((span, index) => {
                  const x = LEFT + (chartWidth * span.start_minute) / 1440;
                  const width = (chartWidth * (span.end_minute - span.start_minute)) / 1440;
                  return (
                    <WorldBar
                      key={`${span.start_minute}-${span.end_minute}-${index}`}
                      span={span}
                      x={x}
                      y={y}
                      width={width}
                      height={36}
                      color={colors.get(worldId) ?? '#687381'}
                      info={worldInfo}
                      gradientPrefix={prefix}
                      index={rowIndex * 1000 + index}
                      onOpen={onOpenWorld}
                    />
                  );
                })}
              </g>
            );
          })}
          <text className="chart-duration" x={WIDTH - 8} y={height - 12}>{formatMinutes(person.online_minutes)} 在线</text>
          {selection && (
            <>
              <line
                className="chart-hover-line"
                x1={LEFT + (chartWidth * selection.column) / 1440}
                y1={TOP - 25}
                x2={LEFT + (chartWidth * selection.column) / 1440}
                y2={TOP + rows.length * rowHeight}
              />
              <WorldTooltip x={LEFT + (chartWidth * selection.column) / 1440} lines={tooltipLines} />
            </>
          )}
        </svg>
      </ChartViewport>
      <ChartDataTable<WorldTableRow>
        label={`${person.name} 的世界时间轴数据`}
        columns={worldTableColumns}
        rows={tableRows}
        getRowKey={(row) => row.key}
        emptyMessage="当天没有可列出的世界时段。"
      />
    </>
  );
}

export function EveryoneWorldChart({
  people,
  info,
  colors,
  worldFilter,
  coverage,
  onOpenWorld,
}: {
  people: WorldAnalytics['friends'];
  info: Map<string, WorldInfo>;
  colors: Map<string, string>;
  worldFilter: string;
  coverage?: Coverage;
  onOpenWorld: (worldId: string) => void;
}) {
  const prefix = useId().replace(/:/g, '');
  const [selection, dispatch] = useReducer(reduceChartSelection, null);
  const touchStart = useRef<PointerPosition | null>(null);
  const rows = worldFilter
    ? people.filter((person) => person.spans.some((span) => span.world_id === worldFilter))
    : people;
  const rowHeight = 34;
  const height = Math.max(210, TOP + rows.length * rowHeight + 34);
  const chartWidth = WIDTH - LEFT - RIGHT;
  const selectedPerson = selection && selection.row >= 0 && selection.row < rows.length
    ? rows[selection.row]
    : null;
  const selectedSpan = selectedPerson?.spans.find((span) =>
    selection
    && (!worldFilter || span.world_id === worldFilter)
    && selection.column >= span.start_minute
    && selection.column <= span.end_minute,
  );
  const tooltipLines = selection
    ? selectedPerson
      ? [
          `${selectedPerson.name} · ${formatClock(selection.column)}`,
          selectedSpan
            ? `${worldName(selectedSpan.world_id, info.get(selectedSpan.world_id))} · ${statusLabel(selectedSpan.status)}`
            : '这个时段没有在线记录',
          ...(selectedSpan ? [selectedSpan.location || selectedSpan.platform || '位置未公开'] : []),
          ...(coverage ? [`当天记录 ${coverageText(coverage)}`] : []),
        ]
      : [formatClock(selection.column)]
    : [];
  const tableRows = rows.flatMap((person) => {
    const spans = worldFilter ? person.spans.filter((span) => span.world_id === worldFilter) : person.spans;
    return spans.map((span, index) => worldTableRow(person, span, index, info, coverage));
  });

  return (
    <>
      <ChartViewport
        routeKey="worldAllX"
        label="所有玩家的世界时间带，可横向滚动查看全天"
        stickyContext={{
          width: WIDTH,
          plotLeft: LEFT,
          plotRight: RIGHT,
          rows: rows.map((person, rowIndex) => ({
            key: person.id,
            label: `${person.name}${person.is_self ? '（自己）' : ''}`,
            top: TOP + rowIndex * rowHeight - 13,
            height: 26,
            active: selection?.row === rowIndex,
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
          aria-label={`所有玩家与自己的世界时间带${tooltipLines.length ? `。${tooltipLines.join('。')}` : ''}`}
          onFocus={(event) => {
            if (rows.length && event.currentTarget.matches(':focus-visible')) {
              dispatch({ type: 'focus', row: 0, column: 0 });
            }
          }}
          onKeyDown={(event) => handleChartKey(event, dispatch, rows.length)}
          onPointerDown={(event) => {
            if (event.pointerType === 'touch') touchStart.current = clientPoint(event);
          }}
          onPointerUp={(event) => {
            if (event.pointerType !== 'touch' || !touchStart.current) return;
            const end = clientPoint(event);
            const isTap = isTapGesture(touchStart.current, end);
            touchStart.current = null;
            if (!isTap) return;
            const point = svgPoint(event, height);
            const row = Math.floor((point.y - TOP + rowHeight / 2) / rowHeight);
            if (row < 0 || row >= rows.length || point.x < LEFT || point.x > WIDTH - RIGHT) return;
            const minute = Math.round(Math.max(0, Math.min(1440, ((point.x - LEFT) / chartWidth) * 1440)));
            dispatch({ type: 'tap', row, column: minute });
          }}
          onPointerCancel={() => { touchStart.current = null; }}
          onPointerMove={(event) => {
            if (event.pointerType === 'touch') return;
            const point = svgPoint(event, height);
            dispatch({
              type: 'hover',
              column: Math.max(0, Math.min(1440, ((point.x - LEFT) / chartWidth) * 1440)),
              row: Math.floor((point.y - TOP + rowHeight / 2) / rowHeight),
            });
          }}
          onPointerLeave={() => dispatch({ type: 'leave' })}
        >
          <TimeGrid bottom={TOP + rows.length * rowHeight} />
          {rows.map((person, rowIndex) => {
            const y = TOP + rowIndex * rowHeight;
            const highlighted = selection?.row === rowIndex;
            const spans = worldFilter ? person.spans.filter((span) => span.world_id === worldFilter) : person.spans;
            const minutes = spans.reduce((total, span) => total + span.end_minute - span.start_minute, 0);
            return (
              <g key={person.id}>
                <rect className={highlighted ? 'chart-row chart-row-hover' : 'chart-row'} x={0} y={y - 13} width={WIDTH} height={26} />
                <text className={highlighted ? 'chart-row-label highlighted' : 'chart-row-label'} x={10} y={y + 4}>
                  {`${person.name}${person.is_self ? '（自己）' : ''}`}
                </text>
                {spans.map((span, spanIndex) => {
                  const x = LEFT + (chartWidth * span.start_minute) / 1440;
                  const width = (chartWidth * (span.end_minute - span.start_minute)) / 1440;
                  return (
                    <WorldBar
                      key={`${span.start_minute}-${span.end_minute}-${spanIndex}`}
                      span={span}
                      x={x}
                      y={y}
                      width={width}
                      height={22}
                      color={colors.get(span.world_id) ?? '#687381'}
                      info={info.get(span.world_id)}
                      gradientPrefix={prefix}
                      index={rowIndex * 1000 + spanIndex}
                      onOpen={onOpenWorld}
                    />
                  );
                })}
                <text className="chart-duration" x={WIDTH - 8} y={y + 4}>{Math.round(minutes)}m</text>
              </g>
            );
          })}
          {selection && (
            <>
              <line
                className="chart-hover-line"
                x1={LEFT + (chartWidth * selection.column) / 1440}
                y1={TOP - 25}
                x2={LEFT + (chartWidth * selection.column) / 1440}
                y2={TOP + rows.length * rowHeight}
              />
              <WorldTooltip x={LEFT + (chartWidth * selection.column) / 1440} lines={tooltipLines} />
            </>
          )}
        </svg>
      </ChartViewport>
      <ChartDataTable<WorldTableRow>
        label="所有玩家的世界时间带数据"
        columns={worldTableColumns}
        rows={tableRows}
        getRowKey={(row) => row.key}
        emptyMessage="当前筛选下没有可列出的世界时段。"
      />
    </>
  );
}
