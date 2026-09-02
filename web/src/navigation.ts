import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';

export const areas = ['online', 'people', 'analysis', 'more'] as const;
export type Area = (typeof areas)[number];

export const analysisSections = ['daily', 'relationships', 'worlds', 'discover', 'dashboard'] as const;
export type AnalysisSection = (typeof analysisSections)[number];

export const moreSections = ['history', 'data', 'settings'] as const;
export type MoreSection = (typeof moreSections)[number];

export const views = [
  'overview',
  'people',
  'daily',
  'relationships',
  'worlds',
  'discover',
  'dashboard',
  'history',
  'data',
  'settings',
] as const;
export type View = (typeof views)[number];

type RouteDetails = {
  person?: string;
  world?: string;
  tab?: string;
};

export type Route =
  | ({ area: 'online' } & RouteDetails)
  | ({ area: 'people' } & RouteDetails)
  | ({ area: 'analysis'; section: AnalysisSection } & RouteDetails)
  | ({ area: 'more'; section: MoreSection } & RouteDetails);

const legacyRoutes: Record<string, Route> = {
  overview: { area: 'online' },
  people: { area: 'people' },
  daily: { area: 'analysis', section: 'daily' },
  dashboard: { area: 'analysis', section: 'dashboard' },
  worlds: { area: 'analysis', section: 'worlds' },
  history: { area: 'more', section: 'history' },
  data: { area: 'more', section: 'data' },
};

const readHash = () => window.location.hash.replace(/^#/, '');
const HASH_PARAMETERS_CHANGED = 'presence-monitor:hash-parameters-changed';
const DETAIL_HISTORY_STATE_KEY = '__presenceMonitorDetailEntry';
const DETAIL_HISTORY_BASE_STATE_KEY = '__presenceMonitorDetailBaseState';

export type DetailHistoryType = 'person' | 'world';

export type DetailHistoryMarker = {
  type: DetailHistoryType;
  id: string;
};

type HashParameterPatch = Record<string, string | number | null>;

const detailParameterKey: Record<DetailHistoryType, 'personDetail' | 'worldDetail'> = {
  person: 'personDetail',
  world: 'worldDetail',
};

const isStateRecord = (value: unknown): value is Record<string, unknown> =>
  Boolean(value) && typeof value === 'object' && !Array.isArray(value);

export function getDetailHistoryMarker(
  state: unknown = window.history.state,
): DetailHistoryMarker | null {
  if (!isStateRecord(state)) return null;
  const marker = state[DETAIL_HISTORY_STATE_KEY];
  if (!isStateRecord(marker)) return null;
  const { type, id } = marker;
  if ((type !== 'person' && type !== 'world') || typeof id !== 'string' || !id) return null;
  return { type, id };
}

const withoutDetailHistoryMarker = (state: unknown) => {
  if (!isStateRecord(state) || !getDetailHistoryMarker(state)) return state;
  if (DETAIL_HISTORY_BASE_STATE_KEY in state) {
    return state[DETAIL_HISTORY_BASE_STATE_KEY] ?? null;
  }
  const next = { ...state };
  delete next[DETAIL_HISTORY_STATE_KEY];
  return Object.keys(next).length ? next : null;
};

const withDetailHistoryMarker = (type: DetailHistoryType, id: string) => {
  const base = withoutDetailHistoryMarker(window.history.state);
  const marker: DetailHistoryMarker = { type, id };
  if (isStateRecord(base)) return { ...base, [DETAIL_HISTORY_STATE_KEY]: marker };
  return {
    [DETAIL_HISTORY_BASE_STATE_KEY]: base,
    [DETAIL_HISTORY_STATE_KEY]: marker,
  };
};

const applyHashPatch = (parameters: URLSearchParams, values: HashParameterPatch) => {
  for (const [key, value] of Object.entries(values)) {
    if (value === null || value === '') parameters.delete(key);
    else parameters.set(key, String(value));
  }
};

export function isCurrentDetailHistoryEntry(
  type: DetailHistoryType,
  id: string,
  state: unknown = window.history.state,
  parameters = new URLSearchParams(readHash()),
) {
  const marker = getDetailHistoryMarker(state);
  return marker?.type === type && marker.id === id && parameters.get(detailParameterKey[type]) === id;
}

const routeDetails = (parameters: URLSearchParams): RouteDetails => {
  const details: RouteDetails = {};
  const person = parameters.get('person');
  const world = parameters.get('world');
  const tab = parameters.get('tab');
  if (person) details.person = person;
  if (world) details.world = world;
  if (tab) details.tab = tab;
  return details;
};

export function parseRoute(parameters: URLSearchParams): Route {
  const details = routeDetails(parameters);
  const requestedArea = parameters.get('area');

  if (requestedArea === 'online') return { area: 'online', ...details };
  if (requestedArea === 'people') return { area: 'people', ...details };
  if (requestedArea === 'analysis') {
    const requestedSection = parameters.get('section');
    const section = analysisSections.includes(requestedSection as AnalysisSection)
      ? (requestedSection as AnalysisSection)
      : 'daily';
    return { area: 'analysis', section, ...details };
  }
  if (requestedArea === 'more') {
    const requestedSection = parameters.get('section');
    const section = moreSections.includes(requestedSection as MoreSection)
      ? (requestedSection as MoreSection)
      : 'history';
    return { area: 'more', section, ...details };
  }

  const legacy = legacyRoutes[parameters.get('view') ?? ''];
  return legacy ? { ...legacy, ...details } : { area: 'online', ...details };
}

export function routeToView(route: Route): View {
  if (route.area === 'online') return 'overview';
  if (route.area === 'people') return 'people';
  return route.section;
}

const copyDetails = (route: Route): RouteDetails => ({
  ...(route.person ? { person: route.person } : {}),
  ...(route.world ? { world: route.world } : {}),
  ...(route.tab ? { tab: route.tab } : {}),
});

export function routeForArea(area: Area, current: Route): Route {
  const details = copyDetails(current);
  if (area === 'online') return { area, ...details };
  if (area === 'people') return { area, ...details };
  if (area === 'analysis') {
    return {
      area,
      section: current.area === 'analysis' ? current.section : 'daily',
      ...details,
    };
  }
  return {
    area,
    section: current.area === 'more' ? current.section : 'history',
    ...details,
  };
}

export function routeForView(view: View, current: Route): Route {
  const details = copyDetails(current);
  if (view === 'overview') return { area: 'online', ...details };
  if (view === 'people') return { area: 'people', ...details };
  if (analysisSections.includes(view as AnalysisSection)) {
    return { area: 'analysis', section: view as AnalysisSection, ...details };
  }
  return { area: 'more', section: view as MoreSection, ...details };
}

export function parametersForRoute(parameters: URLSearchParams, route: Route) {
  const next = new URLSearchParams(parameters);
  next.delete('view');
  next.set('area', route.area);
  if ('section' in route) next.set('section', route.section);
  else next.delete('section');
  if (route.person) next.set('person', route.person);
  if (route.world) next.set('world', route.world);
  if (route.tab) next.set('tab', route.tab);
  return next;
}

export function routeHref(parameters: URLSearchParams, route: Route) {
  const serialized = parametersForRoute(parameters, route).toString();
  return serialized ? `#${serialized}` : '#';
}

export function routeKey(route: Route) {
  if (route.area === 'analysis' || route.area === 'more') return `${route.area}:${route.section}`;
  return route.area;
}

const hashUrl = (serialized: string) => {
  const suffix = serialized ? `#${serialized}` : '';
  return `${window.location.pathname}${window.location.search}${suffix}`;
};

export function navigateHashHref(href: string) {
  const serialized = href.replace(/^#/, '');
  if (serialized === readHash()) {
    window.dispatchEvent(new HashChangeEvent('hashchange'));
    return;
  }
  const parameters = new URLSearchParams(serialized);
  const worldId = parameters.get(detailParameterKey.world)?.trim() ?? '';
  const personId = parameters.get(detailParameterKey.person)?.trim() ?? '';
  const nextState = worldId
    ? withDetailHistoryMarker('world', worldId)
    : personId
      ? withDetailHistoryMarker('person', personId)
      : withoutDetailHistoryMarker(window.history.state);
  window.history.pushState(nextState, '', hashUrl(serialized));
  window.dispatchEvent(new Event(HASH_PARAMETERS_CHANGED));
}

const routeScrollStorageKey = (key: string) => `presence-monitor:scroll:${key}`;

const readRouteScroll = (key: string) => {
  try {
    const value = Number(window.sessionStorage.getItem(routeScrollStorageKey(key)) ?? 0);
    return Number.isFinite(value) ? Math.max(0, value) : 0;
  } catch {
    return 0;
  }
};

const rememberRouteScroll = (key: string, value: number) => {
  const top = Number.isFinite(value) ? Math.max(0, Math.round(value)) : 0;
  try {
    if (top > 0) window.sessionStorage.setItem(routeScrollStorageKey(key), String(top));
    else window.sessionStorage.removeItem(routeScrollStorageKey(key));
  } catch {
    // Navigation remains fully represented by the hash when storage is unavailable.
  }
};

const replaceScrollPosition = (value: number, key: string) => {
  const top = Number.isFinite(value) ? Math.max(0, Math.round(value)) : 0;
  rememberRouteScroll(key, top);
  const parameters = new URLSearchParams(readHash());
  if (top > 0) parameters.set('y', String(top));
  else parameters.delete('y');
  window.history.replaceState(window.history.state, '', hashUrl(parameters.toString()));
};

export function useHashParameters() {
  const [serialized, setSerialized] = useState(readHash);
  const pendingDetailBack = useRef<string | null>(null);

  useEffect(() => {
    const updateFromLocation = () => {
      pendingDetailBack.current = null;
      setSerialized(readHash());
    };
    window.addEventListener('hashchange', updateFromLocation);
    window.addEventListener('popstate', updateFromLocation);
    window.addEventListener(HASH_PARAMETERS_CHANGED, updateFromLocation);
    return () => {
      window.removeEventListener('hashchange', updateFromLocation);
      window.removeEventListener('popstate', updateFromLocation);
      window.removeEventListener(HASH_PARAMETERS_CHANGED, updateFromLocation);
    };
  }, []);

  const parameters = useMemo(() => new URLSearchParams(serialized), [serialized]);
  const setParameters = useCallback((next: URLSearchParams, replace = false) => {
    const nextSerialized = next.toString();
    if (nextSerialized === readHash()) return;
    if (replace) {
      window.history.replaceState(window.history.state, '', hashUrl(nextSerialized));
    } else {
      window.history.pushState(
        withoutDetailHistoryMarker(window.history.state),
        '',
        hashUrl(nextSerialized),
      );
    }
    setSerialized(nextSerialized);
    window.dispatchEvent(new Event(HASH_PARAMETERS_CHANGED));
  }, []);

  const update = useCallback(
    (values: HashParameterPatch, replace = false) => {
      const next = new URLSearchParams(readHash());
      applyHashPatch(next, values);
      const keys = Object.keys(values);
      const detailLocalUpdate =
        keys.length > 0 &&
        Boolean(next.get('personDetail')) &&
        keys.every((key) => key === 'personTab' || key === 'personFrom' || key === 'personTo');
      setParameters(next, replace || detailLocalUpdate);
    },
    [setParameters],
  );

  const openDetail = useCallback(
    (type: DetailHistoryType, id: string, values: HashParameterPatch = {}) => {
      const normalizedId = id.trim();
      if (!normalizedId) return;
      const next = new URLSearchParams(readHash());
      next.set(detailParameterKey[type], normalizedId);
      applyHashPatch(next, values);
      const nextSerialized = next.toString();
      if (
        nextSerialized === readHash() &&
        isCurrentDetailHistoryEntry(type, normalizedId)
      ) {
        return;
      }
      window.history.pushState(
        withDetailHistoryMarker(type, normalizedId),
        '',
        hashUrl(nextSerialized),
      );
      setSerialized(nextSerialized);
      window.dispatchEvent(new Event(HASH_PARAMETERS_CHANGED));
    },
    [],
  );

  const closeDetail = useCallback(
    (type: DetailHistoryType, id: string, values: HashParameterPatch = {}) => {
      const normalizedId = id.trim();
      const current = new URLSearchParams(readHash());
      if (!normalizedId || current.get(detailParameterKey[type]) !== normalizedId) return 'noop' as const;
      if (isCurrentDetailHistoryEntry(type, normalizedId, window.history.state, current)) {
        const pendingKey = `${type}:${normalizedId}`;
        if (pendingDetailBack.current === pendingKey) return 'back-pending' as const;
        pendingDetailBack.current = pendingKey;
        window.history.back();
        return 'back' as const;
      }
      current.delete(detailParameterKey[type]);
      applyHashPatch(current, values);
      setParameters(current, true);
      return 'replace' as const;
    },
    [setParameters],
  );

  return { parameters, update, setParameters, openDetail, closeDetail };
}

export function useHashRoute() {
  const { parameters, setParameters, update, openDetail, closeDetail } = useHashParameters();
  const route = useMemo(() => parseRoute(parameters), [parameters]);
  const currentRouteKey = routeKey(route);

  const navigateRoute = useCallback(
    (nextRoute: Route) => {
      replaceScrollPosition(window.scrollY, currentRouteKey);
      const next = parametersForRoute(new URLSearchParams(readHash()), nextRoute);
      const nextRouteKey = routeKey(nextRoute);
      const targetTop = nextRouteKey === currentRouteKey ? window.scrollY : readRouteScroll(nextRouteKey);
      if (targetTop > 0) next.set('y', String(Math.round(targetTop)));
      else next.delete('y');
      setParameters(next);
    },
    [currentRouteKey, setParameters],
  );

  const navigateArea = useCallback(
    (area: Area) => navigateRoute(routeForArea(area, route)),
    [navigateRoute, route],
  );
  const navigateAnalysis = useCallback(
    (section: AnalysisSection) => navigateRoute({ area: 'analysis', section, ...copyDetails(route) }),
    [navigateRoute, route],
  );
  const navigateMore = useCallback(
    (section: MoreSection) => navigateRoute({ area: 'more', section, ...copyDetails(route) }),
    [navigateRoute, route],
  );
  const navigateView = useCallback(
    (view: View) => navigateRoute(routeForView(view, route)),
    [navigateRoute, route],
  );

  return {
    parameters,
    update,
    openDetail,
    closeDetail,
    route,
    routeKey: currentRouteKey,
    view: routeToView(route),
    navigateArea,
    navigateAnalysis,
    navigateMore,
    navigateView,
  };
}

export function useHashView() {
  const state = useHashRoute();
  return { ...state, navigate: state.navigateView };
}

export function usePageScrollRestoration(key: string) {
  useLayoutEffect(() => {
    const hashValue = Number(new URLSearchParams(readHash()).get('y') ?? Number.NaN);
    const top = Number.isFinite(hashValue) ? Math.max(0, hashValue) : readRouteScroll(key);
    rememberRouteScroll(key, top);
    const restore = () => window.scrollTo({ top, behavior: 'instant' });
    const first = window.requestAnimationFrame(() => window.requestAnimationFrame(restore));
    const delayed = window.setTimeout(restore, 700);
    return () => {
      window.cancelAnimationFrame(first);
      window.clearTimeout(delayed);
    };
  }, [key]);

  useEffect(() => {
    let frame = 0;
    const remember = () => {
      rememberRouteScroll(key, window.scrollY);
      window.cancelAnimationFrame(frame);
      frame = window.requestAnimationFrame(() => replaceScrollPosition(window.scrollY, key));
    };
    window.addEventListener('scroll', remember, { passive: true });
    return () => {
      window.removeEventListener('scroll', remember);
      window.cancelAnimationFrame(frame);
    };
  }, [key]);

  useEffect(() => {
    let frame = 0;
    const restoreHistoryPosition = () => {
      const value = Number(new URLSearchParams(readHash()).get('y') ?? 0);
      const top = Number.isFinite(value) ? Math.max(0, value) : 0;
      window.cancelAnimationFrame(frame);
      frame = window.requestAnimationFrame(() => window.scrollTo({ top, behavior: 'instant' }));
    };
    window.addEventListener('popstate', restoreHistoryPosition);
    return () => {
      window.removeEventListener('popstate', restoreHistoryPosition);
      window.cancelAnimationFrame(frame);
    };
  }, []);
}
