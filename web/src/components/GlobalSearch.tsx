import { useQuery } from '@tanstack/react-query';
import {
  ArrowRight,
  Clock3,
  Command,
  CornerDownLeft,
  Map,
  Search,
  X,
} from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';

import { ApiError, getSearch, type SearchResults, worldImageUrl } from '../api';
import { formatDateTime, initials, statusLabel } from '../format';
import { navigateHashHref } from '../navigation';

type ResultItem = {
  key: string;
  group: 'people' | 'worlds' | 'history' | 'destinations';
  href: string;
  title: string;
  subtitle: string;
  image?: string | undefined;
  badge?: string | undefined;
};

const groupLabels: Record<ResultItem['group'], string> = {
  people: '玩家',
  worlds: '世界',
  history: '历史',
  destinations: '前往',
};

const recentKey = 'presence-monitor:recent-destinations';
const destinations = [
  { href: '#area=online', title: '在线', subtitle: '查看当前在线玩家' },
  { href: '#area=people', title: '玩家', subtitle: '浏览全部玩家' },
  { href: '#area=analysis&section=daily', title: '每日在线', subtitle: '查看每日在线规律' },
  { href: '#area=analysis&section=worlds', title: '世界时间轴', subtitle: '回看好友去过的世界' },
  { href: '#area=analysis&section=discover', title: '世界发现', subtitle: '发现好友最近常去的世界' },
  { href: '#area=more&section=history', title: '状态历史', subtitle: '查看全部状态变化' },
] as const;

const readRecent = () => {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(recentKey) ?? '[]');
    if (!Array.isArray(value)) return [];
    return value.filter((item): item is string => typeof item === 'string').slice(0, 4);
  } catch {
    return [];
  }
};

const rememberDestination = (href: string) => {
  if (!destinations.some((item) => item.href === href)) return;
  try {
    localStorage.setItem(recentKey, JSON.stringify([href, ...readRecent().filter((item) => item !== href)].slice(0, 4)));
  } catch {
    // Search still works when browser storage is unavailable.
  }
};

const flattenResults = (result: SearchResults | undefined): ResultItem[] => {
  if (!result) return [];
  return [
    ...result.groups.people.map((item) => ({
      key: `person:${item.id}`,
      group: 'people' as const,
      href: item.href,
      title: item.name || item.username || item.id,
      subtitle: [item.username ? `@${item.username}` : '', statusLabel(item.status)].filter(Boolean).join(' · '),
      image: item.avatar_url,
      ...(item.is_self || item.tags[0]?.name
        ? { badge: item.is_self ? '自己' : item.tags[0]?.name }
        : {}),
    })),
    ...result.groups.worlds.map((item) => ({
      key: `world:${item.id}`,
      group: 'worlds' as const,
      href: item.href,
      title: item.name || item.id,
      subtitle: item.author_name || `上次到访 ${formatDateTime(item.last_observed)}`,
      image: item.thumbnail_url ? worldImageUrl(item.thumbnail_url) : '',
    })),
    ...result.groups.history.map((item) => ({
      key: `history:${item.id}`,
      group: 'history' as const,
      href: item.href,
      title: item.name || item.username || item.friend_id,
      subtitle: `${statusLabel(item.old_status)} → ${statusLabel(item.new_status)} · ${formatDateTime(item.occurred_at)}`,
    })),
    ...result.groups.destinations.map((item) => ({
      key: `destination:${item.id}`,
      group: 'destinations' as const,
      href: item.href,
      title: item.name,
      subtitle: item.description,
    })),
  ];
};

function ResultIcon({ item }: { item: ResultItem }) {
  if (item.image) return <img src={item.image} alt="" loading="lazy" decoding="async" />;
  if (item.group === 'people') return <span>{initials(item.title)}</span>;
  if (item.group === 'worlds') return <Map size={18} aria-hidden="true" />;
  if (item.group === 'history') return <Clock3 size={18} aria-hidden="true" />;
  return <ArrowRight size={18} aria-hidden="true" />;
}

export function GlobalSearch() {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [debounced, setDebounced] = useState('');
  const [activeIndex, setActiveIndex] = useState(0);
  const [recent, setRecent] = useState(readRecent);

  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(query.trim()), 180);
    return () => window.clearTimeout(timer);
  }, [query]);

  useEffect(() => {
    const onShortcut = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        setOpen(true);
      }
    };
    window.addEventListener('keydown', onShortcut);
    return () => window.removeEventListener('keydown', onShortcut);
  }, []);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      dialog.showModal();
      inputRef.current?.focus();
      window.requestAnimationFrame(() => inputRef.current?.focus());
    } else if (!open && dialog.open) {
      dialog.close();
    }
  }, [open]);

  const result = useQuery({
    queryKey: ['search', debounced],
    queryFn: ({ signal }) => getSearch(debounced, signal),
    enabled: open && debounced.length > 0,
    staleTime: 30_000,
  });
  const normalizedQuery = query.trim();
  const debouncePending = normalizedQuery !== debounced;
  const searchLoading = debouncePending || (normalizedQuery.length > 0 && result.isPending);
  const items = useMemo(() => flattenResults(result.data), [result.data]);
  const recentItems = useMemo(() => {
    const ordered = recent
      .map((href) => destinations.find((item) => item.href === href))
      .filter((item): item is (typeof destinations)[number] => Boolean(item));
    const values = ordered.length ? ordered : destinations.slice(0, 4);
    return values.map((item) => ({
      key: `recent:${item.href}`,
      group: 'destinations' as const,
      ...item,
    }));
  }, [recent]);
  const displayed = debouncePending
    ? []
    : normalizedQuery
      ? (result.isPending || result.isError ? [] : items)
      : recentItems;

  useEffect(() => setActiveIndex(0), [debounced, result.data]);

  const close = () => {
    if (dialogRef.current?.open) dialogRef.current.close();
    setOpen(false);
    setQuery('');
    setDebounced('');
    setActiveIndex(0);
  };

  const select = (item: ResultItem) => {
    rememberDestination(item.href);
    setRecent(readRecent());
    close();
    navigateHashHref(item.href);
  };

  const grouped = displayed.reduce<Array<{ group: ResultItem['group']; items: ResultItem[] }>>((all, item) => {
    const current = all.at(-1);
    if (current?.group === item.group) current.items.push(item);
    else all.push({ group: item.group, items: [item] });
    return all;
  }, []);

  return (
    <>
      <button className="global-search-trigger" type="button" onClick={() => setOpen(true)} aria-label="搜索玩家、世界和历史">
        <Search size={17} aria-hidden="true" />
        <span>搜索</span>
        <kbd><Command size={12} aria-hidden="true" />K</kbd>
      </button>
      <dialog
        ref={dialogRef}
        className="global-search-dialog"
        aria-label="全局搜索"
        onClose={close}
        onCancel={(event) => {
          event.preventDefault();
          close();
        }}
        onClick={(event) => {
          if (event.target === dialogRef.current) close();
        }}
      >
        <div className="global-search-panel">
          <div className="global-search-input">
            <Search size={20} aria-hidden="true" />
            <input
              ref={inputRef}
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索玩家、世界、状态历史…"
              aria-label="搜索"
              aria-controls="global-search-results"
              aria-activedescendant={displayed[activeIndex] ? `search-result-${displayed[activeIndex].key}` : undefined}
              onKeyDown={(event) => {
                if (event.key === 'ArrowDown' && displayed.length) {
                  event.preventDefault();
                  setActiveIndex((value) => (value + 1) % displayed.length);
                } else if (event.key === 'ArrowUp' && displayed.length) {
                  event.preventDefault();
                  setActiveIndex((value) => (value - 1 + displayed.length) % displayed.length);
                } else if (event.key === 'Enter' && debouncePending) {
                  event.preventDefault();
                  setDebounced(normalizedQuery);
                  setActiveIndex(0);
                } else if (event.key === 'Enter' && searchLoading) {
                  event.preventDefault();
                } else if (event.key === 'Enter' && displayed[activeIndex]) {
                  event.preventDefault();
                  select(displayed[activeIndex]);
                } else if (event.key === 'Escape') {
                  event.preventDefault();
                  close();
                }
              }}
            />
            {query ? (
              <button type="button" className="icon-button" onClick={() => setQuery('')} aria-label="清空搜索">
                <X size={17} aria-hidden="true" />
              </button>
            ) : <span className="search-escape">ESC</span>}
          </div>

          <div id="global-search-results" className="global-search-results" role="listbox" aria-label="搜索结果">
            {!debouncePending && !normalizedQuery && <p className="search-section-label">最近前往</p>}
            {searchLoading ? (
              <div className="search-state" role="status">正在搜索…</div>
            ) : normalizedQuery && result.isError ? (
              <div className="search-state" role="alert">
                <strong>搜索暂时不可用</strong>
                <span>{result.error instanceof ApiError ? result.error.message : '请稍后再试'}</span>
                <button type="button" className="button button-secondary button-compact" onClick={() => void result.refetch()}>重试</button>
              </div>
            ) : normalizedQuery && !displayed.length ? (
              <div className="search-state">
                <strong>没有找到“{normalizedQuery}”</strong>
                <span>可以试试显示名、用户名、World ID 或状态。</span>
              </div>
            ) : (
              grouped.map((group) => (
                <section className="search-result-group" key={group.group} aria-label={groupLabels[group.group]}>
                  {normalizedQuery && <h2>{groupLabels[group.group]}</h2>}
                  {group.items.map((item) => {
                    const index = displayed.findIndex((candidate) => candidate.key === item.key);
                    return (
                      <button
                        key={item.key}
                        id={`search-result-${item.key}`}
                        type="button"
                        role="option"
                        aria-selected={index === activeIndex}
                        className={index === activeIndex ? 'search-result active' : 'search-result'}
                        onMouseMove={() => setActiveIndex(index)}
                        onClick={() => select(item)}
                      >
                        <span className="search-result-icon"><ResultIcon item={item} /></span>
                        <span className="search-result-copy">
                          <strong>{item.title}</strong>
                          <small>{item.subtitle}</small>
                        </span>
                        {item.badge && <span className="search-result-badge">{item.badge}</span>}
                        <CornerDownLeft className="search-result-enter" size={16} aria-hidden="true" />
                      </button>
                    );
                  })}
                </section>
              ))
            )}
          </div>
          <footer className="global-search-footer">
            <span><kbd>↑</kbd><kbd>↓</kbd> 选择</span>
            <span><kbd>Enter</kbd> 打开</span>
            <span><kbd>Esc</kbd> 关闭</span>
          </footer>
        </div>
      </dialog>
    </>
  );
}
