import { act, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import {
  parametersForRoute,
  parseRoute,
  routeForArea,
  routeToView,
  useHashRoute,
} from './navigation';

describe('product navigation', () => {
  afterEach(() => {
    window.location.hash = '';
    window.sessionStorage.clear();
  });

  it.each([
    ['overview', 'online', undefined],
    ['people', 'people', undefined],
    ['daily', 'analysis', 'daily'],
    ['worlds', 'analysis', 'worlds'],
    ['history', 'more', 'history'],
    ['data', 'more', 'data'],
  ] as const)('opens the legacy %s hash in its product area', (legacy, area, section) => {
    const route = parseRoute(new URLSearchParams(`view=${legacy}`));
    expect(route.area).toBe(area);
    expect('section' in route ? route.section : undefined).toBe(section);
  });

  it.each([
    ['relationships', 'relationships'],
    ['discover', 'discover'],
  ] as const)('keeps the future analysis section %s typed', (section, expected) => {
    const route = parseRoute(new URLSearchParams(`area=analysis&section=${section}`));
    expect(route).toMatchObject({ area: 'analysis', section: expected });
  });

  it('defaults to online and supplies durable defaults for grouped areas', () => {
    expect(parseRoute(new URLSearchParams())).toEqual({ area: 'online' });
    expect(parseRoute(new URLSearchParams('area=analysis'))).toEqual({
      area: 'analysis',
      section: 'daily',
    });
    expect(parseRoute(new URLSearchParams('area=more'))).toEqual({
      area: 'more',
      section: 'history',
    });
  });

  it('preserves deep-link and unknown parameters when canonicalizing a route', () => {
    const current = new URLSearchParams({
      view: 'worlds',
      day: '2026-08-29',
      from: '2026-08-01',
      to: '2026-08-30',
      peopleQ: 'alice',
      peoplePage: '3',
      historyPage: '9',
      person: 'usr_alice',
      world: 'wrld_example',
      tab: 'activity',
      y: '418',
      worldAllX: '736',
      futureFilter: 'kept',
    });

    const next = parametersForRoute(current, { area: 'more', section: 'data' });

    expect(next.get('view')).toBeNull();
    expect(next.get('area')).toBe('more');
    expect(next.get('section')).toBe('data');
    for (const [key, value] of current) {
      if (key === 'view') continue;
      if (key === 'section') continue;
      expect(next.get(key), key).toBe(value);
    }
  });

  it('keeps the active child when re-entering an area and uses a natural default otherwise', () => {
    expect(routeForArea('analysis', { area: 'analysis', section: 'worlds' })).toEqual({
      area: 'analysis',
      section: 'worlds',
    });
    expect(routeForArea('analysis', { area: 'online' })).toEqual({
      area: 'analysis',
      section: 'daily',
    });
    expect(routeForArea('more', { area: 'people' })).toEqual({
      area: 'more',
      section: 'history',
    });
  });

  it('maps every implemented section to its existing page', () => {
    expect(routeToView({ area: 'online' })).toBe('overview');
    expect(routeToView({ area: 'people' })).toBe('people');
    expect(routeToView({ area: 'analysis', section: 'daily' })).toBe('daily');
    expect(routeToView({ area: 'analysis', section: 'worlds' })).toBe('worlds');
    expect(routeToView({ area: 'more', section: 'history' })).toBe('history');
    expect(routeToView({ area: 'more', section: 'data' })).toBe('data');
    expect(routeToView({ area: 'more', section: 'settings' })).toBe('settings');
  });

  it('restores the route and its parameters from browser history events', () => {
    window.location.hash = '#area=people&peopleQ=alice&peoplePage=3&y=212';
    const { result } = renderHook(() => useHashRoute());
    expect(result.current.route.area).toBe('people');

    act(() => {
      window.history.pushState(
        window.history.state,
        '',
        `${window.location.pathname}${window.location.search}#area=more&section=history&historyPage=8&y=74&kept=yes`,
      );
      window.dispatchEvent(new PopStateEvent('popstate'));
    });

    expect(result.current.route).toMatchObject({ area: 'more', section: 'history' });
    expect(result.current.parameters.get('historyPage')).toBe('8');
    expect(result.current.parameters.get('y')).toBe('74');
    expect(result.current.parameters.get('kept')).toBe('yes');
  });
});
