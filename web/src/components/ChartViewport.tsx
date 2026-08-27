import { UIEvent, useLayoutEffect, useRef } from 'react';
import type { ReactNode } from 'react';

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
  children,
}: {
  routeKey: string;
  label: string;
  children: ReactNode;
}) {
  const viewport = useRef<HTMLDivElement>(null);
  const frame = useRef<number | null>(null);

  useLayoutEffect(() => {
    const element = viewport.current;
    if (!element) return;
    const restore = () => {
      element.scrollLeft = hashNumber(routeKey);
    };
    window.requestAnimationFrame(() => window.requestAnimationFrame(restore));
  }, [routeKey]);

  const remember = (event: UIEvent<HTMLDivElement>) => {
    if (frame.current !== null) window.cancelAnimationFrame(frame.current);
    const left = event.currentTarget.scrollLeft;
    frame.current = window.requestAnimationFrame(() => replaceHashNumber(routeKey, left));
  };

  return (
    <div className="chart-viewport" ref={viewport} onScroll={remember} tabIndex={0} aria-label={label}>
      {children}
    </div>
  );
}
