import { act, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  getDetailHistoryMarker,
  parametersForRoute,
  parseRoute,
  routeForArea,
  routeToView,
  useHashParameters,
  useHashRoute,
} from './navigation';

describe('product navigation', () => {
  afterEach(() => {
    window.history.replaceState(
      null,
      '',
      `${window.location.pathname}${window.location.search}`,
    );
    window.sessionStorage.clear();
    vi.restoreAllMocks();
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

  it('marks nested person and world entries and unwinds one matching layer at a time', () => {
    window.history.replaceState(
      { preserved: 'base' },
      '',
      `${window.location.pathname}${window.location.search}#area=people`,
    );
    const { result } = renderHook(() => useHashParameters());

    act(() => result.current.openDetail('person', 'usr_alice', { personTab: null }));

    expect(getDetailHistoryMarker()).toEqual({ type: 'person', id: 'usr_alice' });
    expect(window.history.state).toMatchObject({ preserved: 'base' });
    const personUrl = `${window.location.pathname}${window.location.search}${window.location.hash}`;
    const personState = window.history.state;

    act(() => result.current.openDetail('world', 'wrld_coffee'));

    expect(getDetailHistoryMarker()).toEqual({ type: 'world', id: 'wrld_coffee' });
    expect(result.current.parameters.get('personDetail')).toBe('usr_alice');
    expect(result.current.parameters.get('worldDetail')).toBe('wrld_coffee');

    const back = vi.spyOn(window.history, 'back').mockImplementation(() => undefined);
    let closeMode = '';
    act(() => {
      closeMode = result.current.closeDetail('world', 'wrld_coffee');
    });
    expect(closeMode).toBe('back');
    expect(back).toHaveBeenCalledTimes(1);
    expect(getDetailHistoryMarker()).toEqual({ type: 'world', id: 'wrld_coffee' });

    act(() => {
      closeMode = result.current.closeDetail('world', 'wrld_coffee');
    });
    expect(closeMode).toBe('back-pending');
    expect(back).toHaveBeenCalledTimes(1);

    act(() => {
      window.history.replaceState(personState, '', personUrl);
      window.dispatchEvent(new PopStateEvent('popstate', { state: personState }));
    });
    expect(getDetailHistoryMarker()).toEqual({ type: 'person', id: 'usr_alice' });
    expect(result.current.parameters.get('worldDetail')).toBeNull();

    act(() => {
      closeMode = result.current.closeDetail('person', 'usr_alice', { personTab: null });
    });
    expect(closeMode).toBe('back');
    expect(back).toHaveBeenCalledTimes(2);
  });

  it('cleans a direct detail deep-link in place instead of leaving the page', () => {
    window.history.replaceState(
      { preserved: 'deep-link' },
      '',
      `${window.location.pathname}${window.location.search}#area=people&personDetail=usr_deep&personTab=worlds`,
    );
    const back = vi.spyOn(window.history, 'back').mockImplementation(() => undefined);
    const { result } = renderHook(() => useHashParameters());
    let closeMode = '';

    act(() => {
      closeMode = result.current.closeDetail('person', 'usr_deep', { personTab: null });
    });

    expect(closeMode).toBe('replace');
    expect(back).not.toHaveBeenCalled();
    expect(result.current.parameters.get('personDetail')).toBeNull();
    expect(result.current.parameters.get('personTab')).toBeNull();
    expect(window.history.state).toEqual({ preserved: 'deep-link' });
  });

  it('does not copy a detail marker into an ordinary hash navigation entry', () => {
    window.history.replaceState(
      { preserved: 'base' },
      '',
      `${window.location.pathname}${window.location.search}#area=people`,
    );
    const { result } = renderHook(() => useHashParameters());

    act(() => result.current.openDetail('person', 'usr_alice'));
    expect(getDetailHistoryMarker()).toEqual({ type: 'person', id: 'usr_alice' });

    act(() => result.current.update({ peopleQ: 'bob' }));

    expect(getDetailHistoryMarker()).toBeNull();
    expect(window.history.state).toEqual({ preserved: 'base' });
    expect(result.current.parameters.get('peopleQ')).toBe('bob');
  });
});
