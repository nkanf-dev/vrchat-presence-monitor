import { useState } from 'react';
import type { PointerEvent as ReactPointerEvent } from 'react';

import { formatClock, formatMinutes, presenceColor } from '../analytics';
import type { PresenceAnalytics } from '../api';
import { statusLabel } from '../format';
import { ChartViewport } from './ChartViewport';

const WIDTH = 1120;
const LEFT = 176;
const RIGHT = 62;
const TOP = 38;
const ROW_HEIGHT = 31;

type Hover = { minute: number; row: number };

const pointInSvg = (event: ReactPointerEvent<SVGSVGElement>, height: number) => {
  const bounds = event.currentTarget.getBoundingClientRect();
  return {
    x: ((event.clientX - bounds.left) / bounds.width) * WIDTH,
    y: ((event.clientY - bounds.top) / bounds.height) * height,
  };
};

function Tooltip({ x, lines }: { x: number; lines: string[] }) {
  const width = Math.min(330, Math.max(154, ...lines.map((line) => Array.from(line).length * 7 + 22)));
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

export function DailyTimelineChart({ rows }: { rows: PresenceAnalytics['timeline'] }) {
  const [hover, setHover] = useState<Hover | null>(null);
  const height = Math.max(210, TOP + rows.length * ROW_HEIGHT + 30);
  const chartWidth = WIDTH - LEFT - RIGHT;
  const hoveredRow = hover && hover.row >= 0 && hover.row < rows.length ? rows[hover.row] : null;
  const hoveredSpan = hoveredRow?.spans.find(
    (span) => hover && hover.minute >= span.start_minute && hover.minute <= span.end_minute,
  );

  return (
    <ChartViewport routeKey="dailyX" label="每日在线时间轴，可横向滚动查看全天">
      <svg
        className="analytics-chart"
        width={WIDTH}
        height={height}
        viewBox={`0 0 ${WIDTH} ${height}`}
        role="img"
        aria-label="每位玩家在所选日期的在线时段"
        onPointerMove={(event) => {
          if (event.pointerType === 'touch') return;
          const point = pointInSvg(event, height);
          const minute = Math.max(0, Math.min(1440, ((point.x - LEFT) / chartWidth) * 1440));
          const row = Math.floor((point.y - TOP + ROW_HEIGHT / 2) / ROW_HEIGHT);
          setHover({ minute, row });
        }}
        onPointerLeave={() => setHover(null)}
      >
        <TimeGrid bottom={TOP + rows.length * ROW_HEIGHT} />
        {rows.map((row, index) => {
          const y = TOP + index * ROW_HEIGHT;
          const highlighted = hover?.row === index;
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
        {hover && hover.minute >= 0 && (
          <>
            <line
              className="chart-hover-line"
              x1={LEFT + (chartWidth * hover.minute) / 1440}
              y1={TOP - 20}
              x2={LEFT + (chartWidth * hover.minute) / 1440}
              y2={TOP + rows.length * ROW_HEIGHT}
            />
            <Tooltip
              x={LEFT + (chartWidth * hover.minute) / 1440}
              lines={hoveredRow
                ? [
                    `${hoveredRow.name} · ${formatClock(hover.minute)}`,
                    hoveredSpan ? statusLabel(hoveredSpan.status) : '此时段没有在线记录',
                  ]
                : [formatClock(hover.minute)]}
            />
          </>
        )}
      </svg>
    </ChartViewport>
  );
}

const heatColor = (value: number) => {
  const intensity = Math.max(0, Math.min(1, value));
  const lightness = 13 + intensity * 59;
  const saturation = 22 + intensity * 58;
  return `hsl(88 ${saturation}% ${lightness}%)`;
};

export function PresenceHeatmap({
  rows,
  observedMinutes,
}: {
  rows: PresenceAnalytics['heatmap'];
  observedMinutes: PresenceAnalytics['heatmap_observed_minutes'];
}) {
  const [hover, setHover] = useState<{ row: number; hour: number } | null>(null);
  const height = Math.max(230, TOP + rows.length * ROW_HEIGHT + 24);
  const chartWidth = WIDTH - LEFT - 24;
  const cellWidth = chartWidth / 24;
  const hoveredRow = hover && hover.row >= 0 && hover.row < rows.length ? rows[hover.row] : null;
  const hoveredValue = hoveredRow && hover ? hoveredRow.values[hover.hour] ?? 0 : 0;
  const hoveredObserved = hover ? observedMinutes[hover.hour] ?? 0 : 0;
  const tooltipX = hover ? LEFT + cellWidth * (hover.hour + 0.5) : LEFT;

  return (
    <ChartViewport routeKey="heatX" label="每位玩家每小时平均在线比例热力图，可横向滚动">
      <svg
        className="analytics-chart"
        width={WIDTH}
        height={height}
        viewBox={`0 0 ${WIDTH} ${height}`}
        role="img"
        aria-label="每位玩家每小时平均在线比例"
        onPointerMove={(event) => {
          if (event.pointerType === 'touch') return;
          const point = pointInSvg(event, height);
          const row = Math.floor((point.y - TOP + ROW_HEIGHT / 2) / ROW_HEIGHT);
          const hour = Math.max(0, Math.min(23, Math.floor((point.x - LEFT) / cellWidth)));
          setHover({ row, hour });
        }}
        onPointerLeave={() => setHover(null)}
      >
        {Array.from({ length: 24 }, (_, hour) => (
          <text key={hour} className="chart-axis-label" x={LEFT + cellWidth * (hour + 0.5)} y={15}>
            {String(hour).padStart(2, '0')}
          </text>
        ))}
        {rows.map((row, rowIndex) => {
          const y = TOP + rowIndex * ROW_HEIGHT;
          const highlighted = hover?.row === rowIndex;
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
                {row.name}
              </text>
              {Array.from({ length: 24 }, (_, hour) => {
                const value = row.values[hour] ?? 0;
                const observed = (observedMinutes[hour] ?? 0) > 0;
                const selected = hover?.row === rowIndex && hover.hour === hour;
                return (
                  <g key={hour}>
                    <rect
                      className={`${selected ? 'heat-cell selected' : 'heat-cell'}${observed ? '' : ' unobserved'}`}
                      x={LEFT + hour * cellWidth + 1.5}
                      y={y - 11}
                      width={cellWidth - 3}
                      height={22}
                      rx={3}
                      fill={observed ? heatColor(value) : '#111821'}
                    />
                    {!observed ? (
                      <text className="heat-value unavailable" x={LEFT + cellWidth * (hour + 0.5)} y={y + 4}>—</text>
                    ) : value >= 0.08 && (
                      <text className={value > 0.55 ? 'heat-value dark' : 'heat-value'} x={LEFT + cellWidth * (hour + 0.5)} y={y + 4}>
                        {Math.round(value * 100)}
                      </text>
                    )}
                  </g>
                );
              })}
            </g>
          );
        })}
        {hoveredRow && hover && (
          <Tooltip
            x={tooltipX}
            lines={[
              `${hoveredRow.name} · ${String(hover.hour).padStart(2, '0')}:00–${String(hover.hour + 1).padStart(2, '0')}:00`,
              hoveredObserved > 0
                ? `在线 ${Math.round(hoveredValue * 100)}% · 已观测 ${Math.round(hoveredObserved)} 分钟`
                : '尚未到达 / 无观测',
            ]}
          />
        )}
      </svg>
    </ChartViewport>
  );
}
