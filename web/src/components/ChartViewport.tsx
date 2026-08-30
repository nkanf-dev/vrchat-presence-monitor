import { UIEvent, useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';

export type ChartStickyRow = {
  key: string;
  label: string;
  top: number;
  height: number;
  active?: boolean;
};

export type ChartStickyContext = {
  width: number;
  plotLeft: number;
  plotRight: number;
  hourPosition?: 'boundary' | 'center';
  rows: ChartStickyRow[];
};

const hashNumber = (key: string) => {
  const value = Number(new URLSearchParams(window.location.hash.replace(/^#/, '')).get(key) ?? 0);
  return Number.isFinite(value) ? Math.max(0, value) : 0;
};

const replaceHashNumber = (key: string, value: number) => {
  const parameters = new URLSearchParams(window.location.hash.replace(/^#/, ''));
  if (value > 0) parameters.set(key, String(Math.round(value)));
  else parameters.delete(key);
  const serialized = parameters.toString();
  const suffix = serialized ? `#${serialized}` : '';
  window.history.replaceState(null, '', `${window.location.pathname}${window.location.search}${suffix}`);
};

export function ChartViewport({
  routeKey,
  label,
  stickyContext,
  children,
}: {
  routeKey: string;
  label: string;
  stickyContext?: ChartStickyContext;
  children: ReactNode;
}) {
  const viewport = useRef<HTMLDivElement>(null);
  const hourTrack = useRef<HTMLDivElement>(null);
  const frame = useRef<number | null>(null);
  const [showTouchHint, setShowTouchHint] = useState(false);

  const synchronizeHeader = (left: number) => {
    if (hourTrack.current) hourTrack.current.style.transform = `translate3d(${-left}px, 0, 0)`;
  };

  useLayoutEffect(() => {
    const element = viewport.current;
    if (!element) return;
    const restore = () => {
      element.scrollLeft = hashNumber(routeKey);
      synchronizeHeader(element.scrollLeft);
    };
    window.requestAnimationFrame(() => window.requestAnimationFrame(restore));
  }, [routeKey]);

  useEffect(() => {
    if (typeof window.matchMedia !== 'function' || !window.matchMedia('(pointer: coarse)').matches) return;
    const storageKey = `presence-monitor:chart-hint:${routeKey}`;
    try {
      if (window.sessionStorage.getItem(storageKey)) return;
      window.sessionStorage.setItem(storageKey, 'shown');
      setShowTouchHint(true);
    } catch {
      setShowTouchHint(true);
    }
  }, [routeKey]);

  const remember = (event: UIEvent<HTMLDivElement>) => {
    if (frame.current !== null) window.cancelAnimationFrame(frame.current);
    const left = event.currentTarget.scrollLeft;
    synchronizeHeader(left);
    frame.current = window.requestAnimationFrame(() => replaceHashNumber(routeKey, left));
  };

  const plotWidth = stickyContext
    ? stickyContext.width - stickyContext.plotLeft - stickyContext.plotRight
    : 0;

  return (
    <div className="chart-viewport-block">
      {showTouchHint && (
        <div className="chart-touch-hint" role="note">
          <span>左右滑动查看时间，轻点可固定详情。</span>
          <button type="button" onClick={() => setShowTouchHint(false)} aria-label="关闭图表操作提示">知道了</button>
        </div>
      )}
      <div className="chart-frame">
        {stickyContext && (
          <div className="chart-sticky-hours" aria-hidden="true">
            <div
              className="chart-sticky-hours-track"
              ref={hourTrack}
              style={{ width: stickyContext.width }}
            >
              {Array.from({ length: 24 }, (_, hour) => {
                const offset = stickyContext.hourPosition === 'center' ? hour + 0.5 : hour;
                const left = stickyContext.plotLeft + (plotWidth * offset) / 24;
                return (
                  <span key={hour} style={{ left }}>
                    {String(hour).padStart(2, '0')}
                  </span>
                );
              })}
            </div>
          </div>
        )}
        <div className="chart-viewport" ref={viewport} onScroll={remember} tabIndex={0} aria-label={label}>
          {children}
        </div>
        {stickyContext && (
          <div
            className="chart-sticky-rows"
            style={{ width: stickyContext.plotLeft }}
            aria-hidden="true"
          >
            {stickyContext.rows.map((row) => (
              <span
                key={row.key}
                className={row.active ? 'active' : undefined}
                style={{ top: row.top, height: row.height }}
                title={row.label}
              >
                {row.label}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
