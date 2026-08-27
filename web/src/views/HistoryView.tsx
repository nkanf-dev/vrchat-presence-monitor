import { useQuery } from '@tanstack/react-query';
import { History, Search } from 'lucide-react';
import { FormEvent, useEffect, useRef, useState } from 'react';

import { ApiError, getEvents } from '../api';
import {
  eventName,
  formatDateTime,
  locationLabel,
  platformLabel,
  statusLabel,
  statusTone,
} from '../format';
import { Pagination } from '../components/Pagination';
import { useHashParameters } from '../navigation';

const PAGE_SIZE = 30;

export function HistoryView() {
  const { parameters, update } = useHashParameters();
  const query = parameters.get('historyQ') ?? '';
  const requestedPage = Number.parseInt(parameters.get('historyPage') ?? '1', 10);
  const page = Number.isFinite(requestedPage) && requestedPage > 0 ? requestedPage - 1 : 0;
  const [draft, setDraft] = useState(query);
  const resultsTitle = useRef<HTMLHeadingElement>(null);
  const focusAfterLoad = useRef(false);
  useEffect(() => setDraft(query), [query]);
  const result = useQuery({
    queryKey: ['events', query, page],
    queryFn: () => getEvents({ query, limit: PAGE_SIZE, offset: page * PAGE_SIZE }),
  });
  const pageCount = result.data ? Math.max(1, Math.ceil(result.data.total / PAGE_SIZE)) : null;
  const displayedPageCount = pageCount ?? Math.max(1, page + 1);

  useEffect(() => {
    if (pageCount !== null && page >= pageCount) {
      update({ historyPage: pageCount > 1 ? pageCount : null }, true);
    }
  }, [page, pageCount, update]);

  useEffect(() => {
    if (!focusAfterLoad.current || result.isFetching) return;
    focusAfterLoad.current = false;
    resultsTitle.current?.focus({ preventScroll: true });
  }, [page, query, result.isFetching]);

  const setPage = (next: number) => {
    focusAfterLoad.current = true;
    update({ historyPage: next > 0 ? next + 1 : null });
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    focusAfterLoad.current = true;
    update({ historyQ: draft.trim() || null, historyPage: null });
  };

  return (
    <>
      <header className="page-heading">
        <div>
          <p className="kicker">Activity history</p>
          <h1 tabIndex={-1}>状态历史</h1>
          <p>搜索所有已保存变化。分页只影响当前显示，不会改变顶部的历史总数。</p>
        </div>
      </header>

      <section className="panel data-panel" aria-labelledby="history-table-title">
        <header className="data-toolbar">
          <div>
            <h2 id="history-table-title" ref={resultsTitle} tabIndex={-1}>全部记录</h2>
            <span aria-live="polite">
              {result.data
                ? `${result.data.total.toLocaleString('zh-CN')} 条`
                : result.isError
                  ? '读取失败'
                  : '正在读取…'}
            </span>
          </div>
          <form className="filters" onSubmit={submit} role="search">
            <label className="search-field wide-search">
              <span className="sr-only">搜索状态历史</span>
              <Search size={17} aria-hidden="true" />
              <input
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                placeholder="搜索玩家、状态、位置或来源"
              />
            </label>
            <button className="button button-secondary" type="submit">
              搜索
            </button>
          </form>
        </header>

        {result.isError && result.data && (
          <div className="panel-state panel-state-stale" role="alert">
            <span>刷新失败，正在显示上次加载的历史记录。</span>
            <button className="button button-secondary button-compact" onClick={() => void result.refetch()}>重试</button>
          </div>
        )}

        {result.isPending ? (
          <div className="panel-state panel-state-loading" role="status">正在加载状态历史…</div>
        ) : result.isError && !result.data ? (
          <div className="inline-error" role="alert">
            <strong>历史记录加载失败</strong>
            <span>{result.error instanceof ApiError ? result.error.message : '请稍后重试'}</span>
            <button className="button button-secondary" onClick={() => void result.refetch()}>
              重试
            </button>
          </div>
        ) : result.data?.items.length ? (
          <div className={result.isFetching ? 'table-wrap is-updating' : 'table-wrap'} aria-busy={result.isFetching}>
            <table>
              <caption className="sr-only">好友状态变化历史</caption>
              <thead>
                <tr>
                  <th scope="col">玩家</th>
                  <th scope="col">变化</th>
                  <th scope="col">位置</th>
                  <th scope="col">设备</th>
                  <th scope="col">发生时间</th>
                </tr>
              </thead>
              <tbody>
                {result.data.items.map((event) => (
                  <tr key={event.client_event_id}>
                    <td data-label="玩家">
                      <strong>{eventName(event)}</strong>
                    </td>
                    <td data-label="变化">
                      <span className="transition">
                        <span>{statusLabel(event.old_status)}</span>
                        <span aria-hidden="true">→</span>
                        <span className={`status-badge tone-${statusTone(event.new_status)}`}>
                          {statusLabel(event.new_status)}
                        </span>
                      </span>
                    </td>
                    <td data-label="位置">{locationLabel(event.location, event.new_status)}</td>
                    <td data-label="设备">{platformLabel(event.platform)}</td>
                    <td data-label="发生时间">
                      <time dateTime={event.occurred_at}>{formatDateTime(event.occurred_at)}</time>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="empty-state roomy">
            <History size={28} aria-hidden="true" />
            <strong>{query ? '没有匹配的记录' : '还没有状态变化'}</strong>
            <p>{query ? '换个关键词，或清除搜索后查看全部。' : '首次快照之后发生的变化会出现在这里。'}</p>
            {query && (
              <button
                className="button button-secondary"
                onClick={() => {
                  focusAfterLoad.current = true;
                  setDraft('');
                  update({ historyQ: null, historyPage: null });
                }}
              >
                清除搜索
              </button>
            )}
          </div>
        )}

        <Pagination
          page={Math.min(page, displayedPageCount - 1)}
          pageCount={displayedPageCount}
          busy={result.isFetching}
          label="历史"
          onPageChange={setPage}
        />
      </section>
    </>
  );
}
