import { useId, useState } from 'react';
import type { PointerEvent as ReactPointerEvent } from 'react';

import { formatClock, formatMinutes, worldName } from '../analytics';
import type { WorldAnalytics, WorldInfo, WorldSpan } from '../api';
import { worldImageUrl } from '../api';
import { statusLabel } from '../format';
import { ChartViewport } from './ChartViewport';

const WIDTH = 1180;
const LEFT = 210;
const RIGHT = 68;
const TOP = 42;

const svgPoint = (event: ReactPointerEvent<SVGSVGElement>, height: number) => {
  const bounds = event.currentTarget.getBoundingClientRect();
  return {
    x: ((event.clientX - bounds.left) / bounds.width) * WIDTH,
    y: ((event.clientY - bounds.top) / bounds.height) * height,
  };
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
  const width = Math.min(370, Math.max(176, ...lines.map((line) => Array.from(line).length * 7 + 24)));
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
        if (event.key === 'Enter' || event.key === ' ') onOpen(span.world_id);
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

export function PersonWorldChart({
  person,
  info,
  colors,
  onOpenWorld,
}: {
  person: WorldAnalytics['friends'][number];
  info: Map<string, WorldInfo>;
  colors: Map<string, string>;
  onOpenWorld: (worldId: string) => void;
}) {
  const prefix = useId().replace(/:/g, '');
  const [hover, setHover] = useState<{ minute: number; row: number } | null>(null);
  const worldIds = [...new Set(person.spans.map((span) => span.world_id))];
  const rows = worldIds.length ? worldIds : [''];
  const rowHeight = 56;
  const height = Math.max(190, TOP + rows.length * rowHeight + 38);
  const chartWidth = WIDTH - LEFT - RIGHT;
  const hoveredWorldId = hover && hover.row >= 0 && hover.row < rows.length ? rows[hover.row] : '';
  const hoveredSpan = person.spans.find((span) =>
    hover
    && span.world_id === hoveredWorldId
    && hover.minute >= span.start_minute
    && hover.minute <= span.end_minute,
  );

  return (
    <ChartViewport routeKey="worldPersonX" label="所选玩家的世界时间轴，可横向滚动查看全天">
      <svg
        className="analytics-chart world-detail-chart"
        width={WIDTH}
        height={height}
        viewBox={`0 0 ${WIDTH} ${height}`}
        role="img"
        aria-label={`${person.name} 的在线与游玩世界时间轴`}
        onPointerMove={(event) => {
          if (event.pointerType === 'touch') return;
          const point = svgPoint(event, height);
          setHover({
            minute: Math.max(0, Math.min(1440, ((point.x - LEFT) / chartWidth) * 1440)),
            row: Math.floor((point.y - TOP + rowHeight / 2) / rowHeight),
          });
        }}
        onPointerLeave={() => setHover(null)}
      >
        <TimeGrid bottom={TOP + rows.length * rowHeight} />
        {rows.map((worldId, rowIndex) => {
          const y = TOP + rowIndex * rowHeight;
          const worldInfo = info.get(worldId);
          const highlighted = hover?.row === rowIndex;
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
        {hover && (
          <>
            <line
              className="chart-hover-line"
              x1={LEFT + (chartWidth * hover.minute) / 1440}
              y1={TOP - 25}
              x2={LEFT + (chartWidth * hover.minute) / 1440}
              y2={TOP + rows.length * rowHeight}
            />
            <WorldTooltip
              x={LEFT + (chartWidth * hover.minute) / 1440}
              lines={hoveredSpan
                ? [
                    `${person.name} · ${formatClock(hover.minute)}`,
                    `${worldName(hoveredSpan.world_id, info.get(hoveredSpan.world_id))} · ${statusLabel(hoveredSpan.status)}`,
                    hoveredSpan.location || hoveredSpan.platform || '在线（位置隐藏）',
                  ]
                : [`${person.name} · ${formatClock(hover.minute)}`, '此时段没有在线记录']}
            />
          </>
        )}
      </svg>
    </ChartViewport>
  );
}

export function EveryoneWorldChart({
  people,
  info,
  colors,
  worldFilter,
  onOpenWorld,
}: {
  people: WorldAnalytics['friends'];
  info: Map<string, WorldInfo>;
  colors: Map<string, string>;
  worldFilter: string;
  onOpenWorld: (worldId: string) => void;
}) {
  const prefix = useId().replace(/:/g, '');
  const [hover, setHover] = useState<{ minute: number; row: number } | null>(null);
  const rows = worldFilter
    ? people.filter((person) => person.spans.some((span) => span.world_id === worldFilter))
    : people;
  const rowHeight = 34;
  const height = Math.max(210, TOP + rows.length * rowHeight + 34);
  const chartWidth = WIDTH - LEFT - RIGHT;
  const hoveredPerson = hover && hover.row >= 0 && hover.row < rows.length ? rows[hover.row] : null;
  const hoveredSpan = hoveredPerson?.spans.find((span) =>
    hover
    && (!worldFilter || span.world_id === worldFilter)
    && hover.minute >= span.start_minute
    && hover.minute <= span.end_minute,
  );

  return (
    <ChartViewport routeKey="worldAllX" label="所有玩家的世界时间带，可横向滚动查看全天">
      <svg
        className="analytics-chart"
        width={WIDTH}
        height={height}
        viewBox={`0 0 ${WIDTH} ${height}`}
        role="img"
        aria-label="所有玩家与自己的世界时间带"
        onPointerMove={(event) => {
          if (event.pointerType === 'touch') return;
          const point = svgPoint(event, height);
          setHover({
            minute: Math.max(0, Math.min(1440, ((point.x - LEFT) / chartWidth) * 1440)),
            row: Math.floor((point.y - TOP + rowHeight / 2) / rowHeight),
          });
        }}
        onPointerLeave={() => setHover(null)}
      >
        <TimeGrid bottom={TOP + rows.length * rowHeight} />
        {rows.map((person, rowIndex) => {
          const y = TOP + rowIndex * rowHeight;
          const highlighted = hover?.row === rowIndex;
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
        {hover && (
          <>
            <line
              className="chart-hover-line"
              x1={LEFT + (chartWidth * hover.minute) / 1440}
              y1={TOP - 25}
              x2={LEFT + (chartWidth * hover.minute) / 1440}
              y2={TOP + rows.length * rowHeight}
            />
            <WorldTooltip
              x={LEFT + (chartWidth * hover.minute) / 1440}
              lines={hoveredPerson
                ? [
                    `${hoveredPerson.name} · ${formatClock(hover.minute)}`,
                    hoveredSpan
                      ? `${worldName(hoveredSpan.world_id, info.get(hoveredSpan.world_id))} · ${statusLabel(hoveredSpan.status)}`
                      : '此时段没有在线记录',
                    ...(hoveredSpan ? [hoveredSpan.location || hoveredSpan.platform || '在线（位置隐藏）'] : []),
                  ]
                : [formatClock(hover.minute)]}
            />
          </>
        )}
      </svg>
    </ChartViewport>
  );
}
