import { useCallback, useEffect, useLayoutEffect, useMemo, useState } from 'react';

export const views = ['overview', 'daily', 'worlds', 'people', 'history', 'data'] as const;
export type View = (typeof views)[number];

const readHash = () => window.location.hash.replace(/^#/, '');

export function useHashParameters() {
  const [serialized, setSerialized] = useState(readHash);

  useEffect(() => {
    const updateFromLocation = () => setSerialized(readHash());
    window.addEventListener('hashchange', updateFromLocation);
    window.addEventListener('popstate', updateFromLocation);
    return () => {
      window.removeEventListener('hashchange', updateFromLocation);
      window.removeEventListener('popstate', updateFromLocation);
    };
  }, []);

  const parameters = useMemo(() => new URLSearchParams(serialized), [serialized]);
  const update = useCallback((values: Record<string, string | number | null>, replace = false) => {
    const next = new URLSearchParams(readHash());
    for (const [key, value] of Object.entries(values)) {
      if (value === null || value === '') next.delete(key);
      else next.set(key, String(value));
    }
    const nextSerialized = next.toString();
    if (nextSerialized === readHash()) return;
    if (replace) {
      const suffix = nextSerialized ? `#${nextSerialized}` : '';
      window.history.replaceState(null, '', `${window.location.pathname}${window.location.search}${suffix}`);
      setSerialized(nextSerialized);
    } else {
      window.location.hash = nextSerialized;
    }
  }, []);

  return { parameters, update };
}

export function useHashView() {
  const { parameters, update } = useHashParameters();
  const value = parameters.get('view');
  const view = views.includes(value as View) ? (value as View) : 'overview';

  const navigate = (next: View) => {
    update({ view: next, y: null });
  };

  return { view, navigate };
}

const replaceScrollPosition = (value: number) => {
  const parameters = new URLSearchParams(readHash());
  if (value > 0) parameters.set('y', String(Math.round(value)));
  else parameters.delete('y');
  const serialized = parameters.toString();
  const suffix = serialized ? `#${serialized}` : '';
  window.history.replaceState(null, '', `${window.location.pathname}${window.location.search}${suffix}`);
};

export function usePageScrollRestoration(view: View) {
  useLayoutEffect(() => {
    const value = Number(new URLSearchParams(readHash()).get('y') ?? 0);
    const top = Number.isFinite(value) ? Math.max(0, value) : 0;
    const restore = () => window.scrollTo({ top, behavior: 'instant' });
    const first = window.requestAnimationFrame(() => window.requestAnimationFrame(restore));
    const delayed = window.setTimeout(restore, 700);
    return () => {
      window.cancelAnimationFrame(first);
      window.clearTimeout(delayed);
    };
  }, [view]);

  useEffect(() => {
    let timer = 0;
    const remember = () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => replaceScrollPosition(window.scrollY), 180);
    };
    window.addEventListener('scroll', remember, { passive: true });
    return () => {
      window.removeEventListener('scroll', remember);
      window.clearTimeout(timer);
    };
  }, [view]);
}
