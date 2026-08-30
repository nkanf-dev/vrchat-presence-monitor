import { keepPreviousData, useQuery } from '@tanstack/react-query';
import {
  ArrowLeft,
  ArrowRight,
  BarChart3,
  BookOpen,
  Clock3,
  Compass,
  Image as ImageIcon,
  RefreshCw,
  Search,
  Sparkles,
  TrendingUp,
  UsersRound,
} from 'lucide-react';
import { FormEvent, useEffect, useMemo, useRef, useState } from 'react';

import {
  getDiscovery,
  getFriends,
  getTags,
  getWorldLibrary,
  worldImageUrl,
} from '../api';
import type {
  DiscoveryWorld,
  Friend,
  RisingWorld,
  Tag,
  WorldLibraryItem,
} from '../api';
import { friendName } from '../format';
import { useHashParameters } from '../navigation';

type DiscoveryTab = 'hot' | 'rising' | 'library';
type RankedWorld = DiscoveryWorld | RisingWorld;
type WorldCardSource = RankedWorld | WorldLibraryItem;

const tabs: Array<{
  id: DiscoveryTab;
  label: string;
  icon: typeof Sparkles;
}> = [
  { id: 'hot', label: '热门', icon: Sparkles },
  { id: 'rising', label: '上升', icon: TrendingUp },
  { id: 'library', label: '世界库', icon: BookOpen },
];

const isDiscoveryTab = (value: string | null): value is DiscoveryTab =>
  value === 'hot' || value === 'rising' || value === 'library';

const selectedDays = (value: string | null): 7 | 30 => (value === '30' ? 30 : 7);

const worldIdOf = (world: WorldCardSource) => {
  const rankedId = (world as RankedWorld).world_id;
  return typeof rankedId === 'string' && rankedId ? rankedId : world.id;
};

const isRisingWorld = (world: RankedWorld): world is RisingWorld =>
  typeof (world as RisingWorld).current === 'object'
  && (world as RisingWorld).current !== null;

const worldTitle = (world: WorldCardSource) => {
  const name = world.name?.trim();
  return name || worldIdOf(world);
};

const worldAuthor = (world: WorldCardSource) => {
  const author = world.author_name?.trim();
  if (author) return author;
  return world.resolution_status && world.resolution_status !== 'ready'
    ? '资料稍后补全'
    : '作者暂未收录';
};

const thumbnailOf = (world: WorldCardSource) =>
  world.thumbnail_url?.trim() || world.image_url?.trim() || '';

const formatMinutes = (value: number) => {
  const minutes = Math.max(0, Math.round(value));
  if (minutes < 60) return `${minutes.toLocaleString('zh-CN')} 分钟`;
  const hours = Math.floor(minutes / 60);
  const remainder = minutes % 60;
  return remainder
    ? `${hours.toLocaleString('zh-CN')} 小时 ${remainder} 分`
    : `${hours.toLocaleString('zh-CN')} 小时`;
};

const dateTimeFormatter = new Intl.DateTimeFormat('zh-CN', {
  year: 'numeric',
  month: 'short',
  day: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
});

const formatDateTime = (value?: string | null) => {
  if (!value) return '还没有到访时间';
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? '到访时间暂不可用' : dateTimeFormatter.format(date);
};

const errorMessage = (error: unknown) =>
  error instanceof Error && error.message ? error.message : '请稍后再试';

function WorldThumbnail({ world }: { world: WorldCardSource }) {
  const source = thumbnailOf(world);
  const [failed, setFailed] = useState(false);

  useEffect(() => setFailed(false), [source]);

  if (!source || failed) {
    return (
      <span className="discovery-world-image discovery-world-image-placeholder" aria-hidden="true">
        <ImageIcon size={24} />
      </span>
    );
  }

  return (
    <img
      className="discovery-world-image"
      src={worldImageUrl(source)}
      alt=""
      loading="lazy"
      decoding="async"
      onError={() => setFailed(true)}
    />
  );
}

function timelineHref(
  parameters: URLSearchParams,
  worldId: string,
  lastObserved: string | null | undefined,
  friendId: string,
) {
  const next = new URLSearchParams(parameters);
  next.delete('view');
  next.set('area', 'analysis');
  next.set('section', 'worlds');
  next.set('world', worldId);
  if (friendId) next.set('person', friendId);
  if (!next.get('day') && lastObserved) {
    const day = lastObserved.slice(0, 10);
    if (/^\d{4}-\d{2}-\d{2}$/.test(day)) next.set('day', day);
  }
  return `#${next.toString()}`;
}

function RankingCard({
  world,
  parameters,
  friendId,
  onOpenWorld,
}: {
  world: RankedWorld;
  parameters: URLSearchParams;
  friendId: string;
  onOpenWorld: (worldId: string) => void;
}) {
  const rising = isRisingWorld(world);
  const stats = rising
    ? world.current
    : {
        minutes: world.minutes,
        unique_people: world.unique_people,
        visit_count: world.visit_count,
        last_observed: world.last_observed,
      };
  const worldId = worldIdOf(world);
  const title = worldTitle(world);
  const delta = rising ? Math.round(world.delta.minutes) : null;

  return (
    <article className="discovery-world-card">
      <button
        type="button"
        className="discovery-world-card-main"
        onClick={() => onOpenWorld(worldId)}
        aria-label={`查看 ${title} 的世界详情`}
      >
        <span className="discovery-world-rank" aria-label={`第 ${world.rank} 名`}>
          <small>排名</small>
          <strong>{world.rank}</strong>
        </span>
        <WorldThumbnail world={world} />
        <span className="discovery-world-copy">
          <strong>{title}</strong>
          <small>{worldAuthor(world)}</small>
          {delta !== null && (
            <em className={delta > 0 ? 'is-positive' : delta < 0 ? 'is-negative' : ''}>
              {delta > 0 ? '+' : ''}{formatMinutes(Math.abs(delta))}{delta < 0 ? ' 回落' : delta > 0 ? ' 增长' : ' 与前期持平'}
            </em>
          )}
        </span>
      </button>

      <dl className="discovery-world-metrics">
        <div>
          <dt><Clock3 size={15} aria-hidden="true" />游玩</dt>
          <dd>{formatMinutes(stats.minutes)}</dd>
        </div>
        <div>
          <dt><UsersRound size={15} aria-hidden="true" />玩家</dt>
          <dd>{stats.unique_people.toLocaleString('zh-CN')} 位</dd>
        </div>
        <div>
          <dt><Compass size={15} aria-hidden="true" />到访</dt>
          <dd>{stats.visit_count.toLocaleString('zh-CN')} 次</dd>
        </div>
      </dl>

      <footer className="discovery-world-card-footer">
        <span>{formatDateTime(stats.last_observed)}</span>
        <a href={timelineHref(parameters, worldId, stats.last_observed, friendId)}>
          <BarChart3 size={16} aria-hidden="true" />
          查看时间轴
        </a>
      </footer>
    </article>
  );
}

function LibraryCard({
  world,
  onOpenWorld,
}: {
  world: WorldLibraryItem;
  onOpenWorld: (worldId: string) => void;
}) {
  const worldId = worldIdOf(world);
  const title = worldTitle(world);
  return (
    <article className="discovery-world-card discovery-library-card">
      <button
        type="button"
        className="discovery-world-card-main"
        onClick={() => onOpenWorld(worldId)}
        aria-label={`查看 ${title} 的世界详情`}
      >
        <WorldThumbnail world={world} />
        <span className="discovery-world-copy">
          <strong>{title}</strong>
          <small>{worldAuthor(world)}</small>
          {world.description?.trim() && <span>{world.description.trim()}</span>}
        </span>
      </button>
      <dl className="discovery-world-metrics discovery-library-metrics">
        <div>
          <dt>最后到访</dt>
          <dd>{formatDateTime(world.last_observed)}</dd>
        </div>
        <div>
          <dt>记录次数</dt>
          <dd>{world.event_count.toLocaleString('zh-CN')} 次</dd>
        </div>
      </dl>
    </article>
  );
}

function RefreshNotice({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="panel-state panel-state-stale discovery-refresh-notice" role="alert">
      <span>{message}</span>
      <button type="button" className="button button-secondary button-compact" onClick={onRetry}>
        <RefreshCw size={15} aria-hidden="true" />
        再试一次
      </button>
    </div>
  );
}

function HardError({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  return (
    <div className="inline-error discovery-error" role="alert">
      <Compass size={26} aria-hidden="true" />
      <strong>世界列表暂时没有加载出来</strong>
      <span>{errorMessage(error)}</span>
      <button type="button" className="button button-secondary" onClick={onRetry}>
        <RefreshCw size={16} aria-hidden="true" />
        重新加载
      </button>
    </div>
  );
}

function EmptyWorlds({ filtered, library }: { filtered: boolean; library: boolean }) {
  return (
    <div className="empty-state roomy discovery-empty">
      {library ? <BookOpen size={28} aria-hidden="true" /> : <Compass size={28} aria-hidden="true" />}
      <strong>{filtered ? '没有符合当前筛选的世界' : library ? '世界库还没有内容' : '这段时间还没有世界记录'}</strong>
      <p>{filtered ? '换一组筛选条件再看看。' : '有新的到访后，这里会自动整理出来。'}</p>
    </div>
  );
}

export function DiscoveryView({ onOpenWorld }: { onOpenWorld: (worldId: string) => void }) {
  const { parameters, update } = useHashParameters();
  const requestedTab = parameters.get('discoverTab');
  const tab: DiscoveryTab = isDiscoveryTab(requestedTab) ? requestedTab : 'hot';
  const days = selectedDays(parameters.get('discoverDays'));
  const friendId = parameters.get('discoverFriend') ?? '';
  const tagId = parameters.get('discoverTag') ?? '';
  const includeSelf = parameters.get('discoverSelf') !== '0';
  const libraryQuery = parameters.get('libraryQ') ?? '';
  const libraryAuthor = parameters.get('libraryAuthor') ?? '';
  const libraryCursor = parameters.get('libraryCursor') ?? '';
  const [libraryDraft, setLibraryDraft] = useState(libraryQuery);
  const [authorDraft, setAuthorDraft] = useState(libraryAuthor);
  const [cursorStack, setCursorStack] = useState<string[]>([]);
  const previousCursor = useRef(libraryCursor);

  useEffect(() => setLibraryDraft(libraryQuery), [libraryQuery]);
  useEffect(() => setAuthorDraft(libraryAuthor), [libraryAuthor]);
  useEffect(() => {
    if (previousCursor.current !== libraryCursor) {
      setCursorStack((current) =>
        current.at(-1) === libraryCursor ? current.slice(0, -1) : current,
      );
      previousCursor.current = libraryCursor;
    }
  }, [libraryCursor]);

  const friends = useQuery({
    queryKey: ['friends', 'discovery-filters'],
    queryFn: () => getFriends({ limit: 200, offset: 0 }),
    staleTime: 5 * 60_000,
  });
  const tags = useQuery({
    queryKey: ['tags'],
    queryFn: getTags,
    staleTime: 5 * 60_000,
  });
  const discovery = useQuery({
    queryKey: ['discovery', days, friendId, tagId, includeSelf],
    queryFn: () => getDiscovery({
      days,
      includeSelf,
      ...(friendId ? { friendId } : {}),
      ...(tagId ? { tagId } : {}),
    }),
    enabled: tab !== 'library',
    placeholderData: keepPreviousData,
    staleTime: 60_000,
  });
  const library = useQuery({
    queryKey: ['world-library', libraryQuery, libraryAuthor, friendId, tagId, libraryCursor],
    queryFn: () => getWorldLibrary({
      query: libraryQuery,
      author: libraryAuthor,
      friendId,
      tagId,
      cursor: libraryCursor,
      limit: 36,
    }),
    enabled: tab === 'library',
    placeholderData: keepPreviousData,
    staleTime: 60_000,
  });

  const sortedFriends = useMemo(
    () => [...(friends.data?.items ?? [])].sort((left, right) =>
      friendName(left).localeCompare(friendName(right), 'zh-CN'),
    ),
    [friends.data?.items],
  );
  const sortedTags = useMemo(
    () => [...(tags.data ?? [])].sort((left, right) => left.name.localeCompare(right.name, 'zh-CN')),
    [tags.data],
  );

  const changeSharedFilter = (values: Record<string, string | null>) => {
    setCursorStack([]);
    update({ ...values, libraryCursor: null });
  };

  const submitLibrarySearch = (event: FormEvent) => {
    event.preventDefault();
    setCursorStack([]);
    update({
      libraryQ: libraryDraft.trim() || null,
      libraryAuthor: authorDraft.trim() || null,
      libraryCursor: null,
    });
  };

  const clearFilters = () => {
    setLibraryDraft('');
    setAuthorDraft('');
    setCursorStack([]);
    update({
      discoverFriend: null,
      discoverTag: null,
      discoverSelf: null,
      libraryQ: null,
      libraryAuthor: null,
      libraryCursor: null,
    });
  };

  const goToNextLibraryPage = () => {
    const next = library.data?.next_cursor;
    if (!next) return;
    setCursorStack((current) => [...current, libraryCursor]);
    update({ libraryCursor: next });
  };

  const goToPreviousLibraryPage = () => {
    const previous = cursorStack.at(-1);
    if (previous === undefined) return;
    setCursorStack((current) => current.slice(0, -1));
    update({ libraryCursor: previous || null });
  };

  const activeRankedWorlds = tab === 'rising' ? discovery.data?.rising : discovery.data?.hot;
  const rankingFiltered = Boolean(friendId || tagId || !includeSelf);
  const libraryFiltered = Boolean(friendId || tagId || libraryQuery || libraryAuthor);
  const filtersUnavailable = (friends.isError && !friends.data) || (tags.isError && !tags.data);

  return (
    <>
      <header className="page-heading discovery-heading">
        <div>
          <p className="kicker">Discover</p>
          <h1 tabIndex={-1}>世界发现</h1>
          <p>看看好友最近常去哪里，也可以翻阅你们去过的全部世界。</p>
        </div>
      </header>

      <section className="panel discovery-panel" aria-labelledby="discovery-content-title">
        <div className="discovery-tabs" role="tablist" aria-label="世界发现视图">
          {tabs.map((item) => {
            const Icon = item.icon;
            return (
              <button
                key={item.id}
                type="button"
                role="tab"
                id={`discovery-tab-${item.id}`}
                aria-controls="discovery-tabpanel"
                aria-selected={tab === item.id}
                tabIndex={tab === item.id ? 0 : -1}
                className={tab === item.id ? 'is-active' : ''}
                onClick={() => update({ discoverTab: item.id })}
                onKeyDown={(event) => {
                  const currentIndex = tabs.findIndex((candidate) => candidate.id === item.id);
                  let nextIndex = currentIndex;
                  if (event.key === 'ArrowRight') nextIndex = (currentIndex + 1) % tabs.length;
                  else if (event.key === 'ArrowLeft') nextIndex = (currentIndex - 1 + tabs.length) % tabs.length;
                  else if (event.key === 'Home') nextIndex = 0;
                  else if (event.key === 'End') nextIndex = tabs.length - 1;
                  else return;
                  event.preventDefault();
                  const nextTab = tabs[nextIndex];
                  if (!nextTab) return;
                  update({ discoverTab: nextTab.id });
                  window.requestAnimationFrame(() => {
                    document.getElementById(`discovery-tab-${nextTab.id}`)?.focus();
                  });
                }}
              >
                <Icon size={17} aria-hidden="true" />
                {item.label}
              </button>
            );
          })}
        </div>

        <div className="discovery-controls">
          {tab !== 'library' && (
            <div className="discovery-period" role="group" aria-label="统计时间范围">
              <span>时间范围</span>
              <button
                type="button"
                className={days === 7 ? 'is-active' : ''}
                aria-pressed={days === 7}
                onClick={() => update({ discoverDays: '7' })}
              >
                近 7 天
              </button>
              <button
                type="button"
                className={days === 30 ? 'is-active' : ''}
                aria-pressed={days === 30}
                onClick={() => update({ discoverDays: '30' })}
              >
                近 30 天
              </button>
            </div>
          )}

          <div className="discovery-filter-row">
            <label className="select-control">
              <span>玩家</span>
              <select
                value={friendId}
                onChange={(event) => changeSharedFilter({ discoverFriend: event.target.value || null })}
                disabled={friends.isPending && !friends.data}
              >
                <option value="">全部玩家</option>
                {sortedFriends.map((friend: Friend) => (
                  <option key={friend.id} value={friend.id}>
                    {friendName(friend)}{friend.is_self ? '（自己）' : ''}
                  </option>
                ))}
              </select>
            </label>

            <label className="select-control">
              <span>标签</span>
              <select
                value={tagId}
                onChange={(event) => changeSharedFilter({ discoverTag: event.target.value || null })}
                disabled={tags.isPending && !tags.data}
              >
                <option value="">全部标签</option>
                {sortedTags.map((tag: Tag) => (
                  <option key={tag.id} value={tag.id}>{tag.name}</option>
                ))}
              </select>
            </label>

            {tab !== 'library' && (
              <label className="discovery-self-toggle">
                <input
                  type="checkbox"
                  checked={includeSelf}
                  onChange={(event) => changeSharedFilter({ discoverSelf: event.target.checked ? '1' : '0' })}
                />
                <span>包含我的账号</span>
              </label>
            )}
          </div>

          {tab === 'library' && (
            <form className="discovery-library-search" role="search" onSubmit={submitLibrarySearch}>
              <label className="search-field">
                <Search size={17} aria-hidden="true" />
                <span className="sr-only">搜索世界</span>
                <input
                  value={libraryDraft}
                  onChange={(event) => setLibraryDraft(event.target.value)}
                  placeholder="搜索世界名称或 ID"
                />
              </label>
              <label className="search-field">
                <UsersRound size={17} aria-hidden="true" />
                <span className="sr-only">按作者筛选</span>
                <input
                  value={authorDraft}
                  onChange={(event) => setAuthorDraft(event.target.value)}
                  placeholder="作者名称"
                />
              </label>
              <button type="submit" className="button button-secondary">
                <Search size={16} aria-hidden="true" />
                查找
              </button>
            </form>
          )}

          {filtersUnavailable && (
            <RefreshNotice
              message="部分筛选项暂时没有加载出来。"
              onRetry={() => {
                void friends.refetch();
                void tags.refetch();
              }}
            />
          )}
        </div>

        <div
          id="discovery-tabpanel"
          role="tabpanel"
          aria-labelledby={`discovery-tab-${tab}`}
          className="discovery-tabpanel"
        >
          <header className="panel-heading discovery-results-heading">
            <div>
              <h2 id="discovery-content-title">
                {tab === 'hot' ? '好友热门世界' : tab === 'rising' ? '最近上升' : '到访世界库'}
              </h2>
              <span aria-live="polite">
                {tab === 'library'
                  ? library.data
                    ? `${library.data.total.toLocaleString('zh-CN')} 个世界`
                    : '整理中…'
                  : discovery.data
                    ? `${discovery.data.selected_people.toLocaleString('zh-CN')} 位玩家 · 近 ${days} 天`
                    : '整理中…'}
              </span>
            </div>
            {(rankingFiltered || libraryFiltered) && (
              <button type="button" className="button button-ghost button-compact" onClick={clearFilters}>
                清除筛选
              </button>
            )}
          </header>

          {tab === 'library' ? (
            <>
              {(library.isRefetchError || (library.isError && Boolean(library.data))) && (
                <RefreshNotice message="更新失败，正在显示上次加载的世界。" onRetry={() => void library.refetch()} />
              )}
              {library.isPending ? (
                <div className="panel-state panel-state-loading discovery-loading" role="status">正在打开世界库…</div>
              ) : library.isError && !library.data ? (
                <HardError error={library.error} onRetry={() => void library.refetch()} />
              ) : library.data?.items.length ? (
                <div
                  className={library.isFetching ? 'discovery-world-grid is-updating' : 'discovery-world-grid'}
                  aria-busy={library.isFetching}
                >
                  {library.data.items.map((world) => (
                    <LibraryCard key={world.id} world={world} onOpenWorld={onOpenWorld} />
                  ))}
                </div>
              ) : (
                <EmptyWorlds filtered={libraryFiltered} library />
              )}

              {library.data && (cursorStack.length > 0 || Boolean(library.data.next_cursor)) && (
                <nav className="discovery-library-pagination" aria-label="世界库分页">
                  <button
                    type="button"
                    className="button button-secondary"
                    disabled={!cursorStack.length || library.isFetching}
                    onClick={goToPreviousLibraryPage}
                  >
                    <ArrowLeft size={16} aria-hidden="true" />
                    上一页
                  </button>
                  <span aria-live="polite">{library.isFetching ? '正在翻页…' : '继续浏览世界库'}</span>
                  <button
                    type="button"
                    className="button button-secondary"
                    disabled={!library.data.next_cursor || library.isFetching}
                    onClick={goToNextLibraryPage}
                  >
                    下一页
                    <ArrowRight size={16} aria-hidden="true" />
                  </button>
                </nav>
              )}
            </>
          ) : (
            <>
              {(discovery.isRefetchError || (discovery.isError && Boolean(discovery.data))) && (
                <RefreshNotice message="更新失败，正在显示上次加载的结果。" onRetry={() => void discovery.refetch()} />
              )}
              {discovery.isPending ? (
                <div className="panel-state panel-state-loading discovery-loading" role="status">正在整理好友去过的世界…</div>
              ) : discovery.isError && !discovery.data ? (
                <HardError error={discovery.error} onRetry={() => void discovery.refetch()} />
              ) : activeRankedWorlds?.length ? (
                <div
                  className={discovery.isFetching ? 'discovery-world-grid is-updating' : 'discovery-world-grid'}
                  aria-busy={discovery.isFetching}
                >
                  {activeRankedWorlds.map((world) => (
                    <RankingCard
                      key={world.world_id}
                      world={world}
                      parameters={parameters}
                      friendId={friendId}
                      onOpenWorld={onOpenWorld}
                    />
                  ))}
                </div>
              ) : (
                <EmptyWorlds filtered={rankingFiltered} library={false} />
              )}
            </>
          )}
        </div>
      </section>
    </>
  );
}
