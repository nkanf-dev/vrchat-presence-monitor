import { useCallback, useEffect, useMemo, useState } from 'react';

export const views = ['overview', 'people', 'history', 'data'] as const;
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
    update({ view: next });
  };

  return { view, navigate };
}
