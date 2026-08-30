import { useQuery } from '@tanstack/react-query';
import { Search, Users } from 'lucide-react';
import { FormEvent, useEffect, useMemo, useRef, useState } from 'react';

import { formatSecondsCompact } from '../analytics';
import { ApiError, type Friend, getAnalyticsStats, getFriends } from '../api';
import {
  formatDateTime,
  friendName,
  platformLabel,
  statusLabel,
  statusTone,
} from '../format';
import { Avatar } from '../components/Avatar';
import { LocationText } from '../components/LocationText';
import { Pagination } from '../components/Pagination';
import { useHashParameters } from '../navigation';

const PAGE_SIZE = 24;

export function PeopleView({ onOpenFriend }: { onOpenFriend: (friend: Friend) => void }) {
  const { parameters, update } = useHashParameters();
  const query = parameters.get('peopleQ') ?? '';
  const status = parameters.get('peopleStatus') ?? '';
  const requestedPage = Number.parseInt(parameters.get('peoplePage') ?? '1', 10);
  const page = Number.isFinite(requestedPage) && requestedPage > 0 ? requestedPage - 1 : 0;
  const [draft, setDraft] = useState(query);
  const resultsTitle = useRef<HTMLHeadingElement>(null);
  const focusAfterLoad = useRef(false);
  useEffect(() => setDraft(query), [query]);
  const result = useQuery({
    queryKey: ['friends', query, status, page],
    queryFn: () => getFriends({ query, status, limit: PAGE_SIZE, offset: page * PAGE_SIZE }),
  });
  const stats = useQuery({
    queryKey: ['analytics', 'stats', 30],
    queryFn: () => getAnalyticsStats(30),
    staleTime: 60_000,
  });
  const playtime = useMemo(
    () => new Map((stats.data?.online_hours_all ?? stats.data?.online_hours ?? []).map((item) => [item.id, item.seconds])),
    [stats.data],
  );

  const pageCount = result.data ? Math.max(1, Math.ceil(result.data.total / PAGE_SIZE)) : null;
  const displayedPageCount = pageCount ?? Math.max(1, page + 1);
  useEffect(() => {
    // Do not clamp a deep-linked page while its request is still pending. With
    // no data yet, treating the total as zero would bounce every page to one.
    if (pageCount !== null && page >= pageCount) {
      update({ peoplePage: pageCount > 1 ? pageCount : null }, true);
    }
  }, [page, pageCount, update]);

  useEffect(() => {
    if (!focusAfterLoad.current || result.isFetching) return;
    focusAfterLoad.current = false;
    resultsTitle.current?.focus({ preventScroll: true });
  }, [page, query, result.isFetching, status]);

  const setPage = (next: number) => {
    focusAfterLoad.current = true;
    update({ peoplePage: next > 0 ? next + 1 : null });
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    focusAfterLoad.current = true;
    update({ peopleQ: draft.trim() || null, peoplePage: null });
  };

  return (
    <>
      <header className="page-heading">
        <div>
          <p className="kicker">People</p>
          <h1 tabIndex={-1}>玩家列表</h1>
          <p>搜索好友与自己的账号；点击玩家可查看活动、世界、名称、备注和标签。</p>
        </div>
      </header>

      <section className="panel data-panel" aria-labelledby="people-table-title">
        <header className="data-toolbar">
          <div>
            <h2 id="people-table-title" ref={resultsTitle} tabIndex={-1}>全部玩家</h2>
            <span aria-live="polite">
              {result.data
                ? `${result.data.total.toLocaleString('zh-CN')} 位`
                : result.isError
                  ? '读取失败'
                  : '正在读取…'}
            </span>
          </div>
          <form className="filters" onSubmit={submit} role="search">
            <label className="search-field">
              <span className="sr-only">搜索玩家</span>
              <Search size={17} aria-hidden="true" />
              <input
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                placeholder="搜索显示名、用户名或 ID"
              />
            </label>
            <select
              className="select-field"
              value={status}
              onChange={(event) => {
                focusAfterLoad.current = true;
                update({ peopleStatus: event.target.value || null, peoplePage: null });
              }}
              aria-label="按状态筛选"
            >
              <option value="">全部状态</option>
              <option value="online">当前在线</option>
              <option value="offline">离线</option>
              <option value="active">游戏中</option>
              <option value="join me">可加入</option>
              <option value="ask me">先询问</option>
              <option value="busy">忙碌</option>
            </select>
            <button className="button button-secondary" type="submit">
              <Search size={16} aria-hidden="true" />
              搜索
            </button>
          </form>
        </header>

        {result.isError && result.data && (
          <div className="panel-state panel-state-stale" role="alert">
            <span>刷新失败，正在显示上次加载的玩家列表。</span>
            <button className="button button-secondary button-compact" onClick={() => void result.refetch()}>重试</button>
          </div>
        )}

        {result.isPending ? (
          <div className="panel-state panel-state-loading" role="status">正在加载玩家列表…</div>
        ) : result.isError && !result.data ? (
          <div className="inline-error" role="alert">
            <strong>玩家列表加载失败</strong>
            <span>{result.error instanceof ApiError ? result.error.message : '请稍后重试'}</span>
            <button className="button button-secondary" onClick={() => void result.refetch()}>
              重试
            </button>
          </div>
        ) : result.data?.items.length ? (
          <div className={result.isFetching ? 'table-wrap is-updating' : 'table-wrap'} aria-busy={result.isFetching}>
            <table>
              <caption className="sr-only">追踪玩家状态列表</caption>
              <thead>
                <tr>
                  <th scope="col">玩家</th>
                  <th scope="col">状态</th>
                  <th scope="col">位置</th>
                  <th scope="col">设备</th>
                  <th scope="col">近 30 天时长</th>
                  <th scope="col">更新时间</th>
                </tr>
              </thead>
              <tbody>
                {result.data.items.map((friend) => {
                  const name = friendName(friend);
                  return (
                    <tr key={friend.id}>
                      <td data-label="玩家">
                        <button
                          className="person-cell"
                          onClick={() => onOpenFriend(friend)}
                          aria-label={`查看 ${name} 的资料`}
                        >
                          <Avatar friend={friend} size="small" />
                          <span>
                            <strong>{name}</strong>
                            <small>{friend.username ? `@${friend.username}` : friend.id}</small>
                          </span>
                          {Boolean(friend.is_self) && <em>自己</em>}
                        </button>
                      </td>
                      <td data-label="状态">
                        <span className={`status-badge tone-${statusTone(friend.status)}`}>
                          {statusLabel(friend.status)}
                        </span>
                      </td>
                      <td data-label="位置"><LocationText location={friend.location} status={friend.status} /></td>
                      <td data-label="设备">{platformLabel(friend.platform)}</td>
                      <td data-label="近 30 天时长" className="playtime-cell">
                        {stats.isPending ? '读取中…' : formatSecondsCompact(playtime.get(friend.id) ?? 0)}
                      </td>
                      <td data-label="更新时间">
                        <time dateTime={friend.updated_at}>{formatDateTime(friend.updated_at)}</time>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="empty-state roomy">
            <Users size={28} aria-hidden="true" />
            <strong>{query || status ? '没有匹配的玩家' : '还没有玩家数据'}</strong>
            <p>{query || status ? '换个关键词或清除筛选后再试。' : '云端完成首次采集后，玩家会出现在这里。'}</p>
            {(query || status) && (
              <button
                className="button button-secondary"
                onClick={() => {
                  focusAfterLoad.current = true;
                  setDraft('');
                  update({ peopleQ: null, peopleStatus: null, peoplePage: null });
                }}
              >
                清除筛选
              </button>
            )}
          </div>
        )}

        <Pagination
          page={Math.min(page, displayedPageCount - 1)}
          pageCount={displayedPageCount}
          busy={result.isFetching}
          label="玩家"
          onPageChange={setPage}
        />
      </section>
    </>
  );
}
