import { ArrowRight, Clock3, Radio, Users } from 'lucide-react';

import type { EventPage, Friend, FriendPage, Overview } from '../api';
import {
  eventName,
  formatDateTime,
  formatNumber,
  friendName,
  platformLabel,
  statusLabel,
  statusTone,
} from '../format';
import { Avatar } from '../components/Avatar';
import { LocationText } from '../components/LocationText';

function EmptyPanel({ title, copy }: { title: string; copy: string }) {
  return (
    <div className="empty-state">
      <span className="empty-mark" aria-hidden="true" />
      <strong>{title}</strong>
      <p>{copy}</p>
    </div>
  );
}

function PreviewState({
  pending,
  failed,
  hasData,
  onRetry,
  label,
}: {
  pending: boolean;
  failed: boolean;
  hasData: boolean;
  onRetry: () => void;
  label: string;
}) {
  if (pending) return <div className="panel-state" role="status">正在加载{label}…</div>;
  if (!failed) return null;
  return (
    <div className={hasData ? 'panel-state panel-state-stale' : 'panel-state panel-state-error'} role="alert">
      <span>{hasData ? `${label}刷新失败，下面保留上次结果。` : `${label}暂时没有加载出来。`}</span>
      <button className="button button-secondary button-compact" onClick={onRetry}>重试</button>
    </div>
  );
}

function PersonButton({ friend, onOpen }: { friend: Friend; onOpen: (friend: Friend) => void }) {
  const name = friendName(friend);
  return (
    <button className="presence-item" onClick={() => onOpen(friend)} aria-label={`查看 ${name} 的资料`}>
      <Avatar friend={friend} />
      <span className="presence-copy">
        <strong>
          {name}
          {Boolean(friend.is_self) && <em>自己</em>}
        </strong>
        <span>
          <LocationText location={friend.location} status={friend.status} /> · {platformLabel(friend.platform)}
        </span>
      </span>
      <span className={`status-badge tone-${statusTone(friend.status)}`}>{statusLabel(friend.status)}</span>
    </button>
  );
}

export function OverviewView({
  overview,
  people,
  events,
  peoplePending,
  peopleFailed,
  peopleFetching,
  eventsPending,
  eventsFailed,
  eventsFetching,
  onRetryPeople,
  onRetryEvents,
  onOpenFriend,
  onNavigatePeople,
  onNavigateHistory,
}: {
  overview: Overview;
  people: FriendPage | undefined;
  events: EventPage | undefined;
  peoplePending: boolean;
  peopleFailed: boolean;
  peopleFetching: boolean;
  eventsPending: boolean;
  eventsFailed: boolean;
  eventsFetching: boolean;
  onRetryPeople: () => void;
  onRetryEvents: () => void;
  onOpenFriend: (friend: Friend) => void;
  onNavigatePeople: () => void;
  onNavigateHistory: () => void;
}) {
  return (
    <>
      <header className="page-heading">
        <div>
          <p className="kicker">Presence overview</p>
          <h1 tabIndex={-1}>状态总览</h1>
          <p>最近一次采集快照与已保存历史。所有数字都来自完整统计，而不是当前列表页。</p>
        </div>
        <span className="page-time">采集于 {formatDateTime(overview.last_sync)}</span>
      </header>

      <section className="metric-grid" aria-label="状态摘要">
        <article className="metric-card metric-primary">
          <span className="metric-icon" aria-hidden="true">
            <Radio size={18} />
          </span>
          <p>当前在线</p>
          <strong>{formatNumber(overview.online_count)}</strong>
          <span>{overview.tracked_count ? Math.round((overview.online_count / overview.tracked_count) * 100) : 0}% 的追踪对象</span>
        </article>
        <article className="metric-card">
          <span className="metric-icon" aria-hidden="true">
            <Users size={18} />
          </span>
          <p>追踪人数</p>
          <strong>{formatNumber(overview.tracked_count)}</strong>
          <span>好友与自己的账号</span>
        </article>
        <article className="metric-card">
          <span className="metric-icon" aria-hidden="true">
            <Clock3 size={18} />
          </span>
          <p>近 7 天变化</p>
          <strong>{formatNumber(overview.change_count_7d)}</strong>
          <span>严格按最近 7×24 小时统计</span>
        </article>
        <article className="metric-card">
          <span className="metric-icon" aria-hidden="true">
            <Clock3 size={18} />
          </span>
          <p>历史记录</p>
          <strong>{formatNumber(overview.event_total)}</strong>
          <span>完整总数，不受分页影响</span>
        </article>
      </section>

      <div className="overview-grid">
        <section className="panel" aria-labelledby="online-title">
          <header className="panel-heading">
            <div>
              <p className="kicker">Online now</p>
              <h2 id="online-title">当前在线玩家</h2>
            </div>
            <button className="text-action" onClick={onNavigatePeople}>
              查看全部 <ArrowRight size={16} aria-hidden="true" />
            </button>
          </header>
          <PreviewState
            pending={peoplePending}
            failed={peopleFailed}
            hasData={Boolean(people)}
            onRetry={onRetryPeople}
            label="玩家快照"
          />
          {!peoplePending && (people || !peopleFailed) && <div className="presence-list" aria-busy={peopleFetching}>
            {people?.items.length ? (
              people.items.map((friend) => (
                <PersonButton key={friend.id} friend={friend} onOpen={onOpenFriend} />
              ))
            ) : (
              <EmptyPanel title="当前没有在线玩家" copy="有人上线后会立即出现在这里。" />
            )}
          </div>}
        </section>

        <section className="panel" aria-labelledby="activity-title">
          <header className="panel-heading">
            <div>
              <p className="kicker">Activity history</p>
              <h2 id="activity-title">最近变化</h2>
            </div>
            <button className="text-action" onClick={onNavigateHistory}>
              全部历史 <ArrowRight size={16} aria-hidden="true" />
            </button>
          </header>
          <PreviewState
            pending={eventsPending}
            failed={eventsFailed}
            hasData={Boolean(events)}
            onRetry={onRetryEvents}
            label="最近变化"
          />
          {!eventsPending && (events || !eventsFailed) && <ol className="activity-list" aria-busy={eventsFetching}>
            {events?.items.length ? (
              events.items.map((event) => (
                <li key={event.client_event_id}>
                  <span className={`activity-dot tone-${statusTone(event.new_status)}`} aria-hidden="true" />
                  <div>
                    <strong>{eventName(event)}</strong>
                    <span>
                      {statusLabel(event.old_status)} → {statusLabel(event.new_status)}
                    </span>
                    <small><LocationText location={event.location} status={event.new_status} /></small>
                  </div>
                  <time dateTime={event.occurred_at}>{formatDateTime(event.occurred_at)}</time>
                </li>
              ))
            ) : (
              <EmptyPanel title="还没有状态变化" copy="首次快照之后发生的上线、离线和位置变化会保存在这里。" />
            )}
          </ol>}
        </section>
      </div>
    </>
  );
}
