import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Bookmark,
  Check,
  Clock3,
  ExternalLink,
  History,
  MapPin,
  Monitor,
  Plus,
  RefreshCw,
  Save,
  Sparkles,
  Tag as TagIcon,
  UserRound,
  UsersRound,
  X,
} from 'lucide-react';
import { FormEvent, useEffect, useRef, useState } from 'react';

import { formatSecondsCompact, isDateKey, todayKey } from '../analytics';
import {
  ApiError,
  assignFriendTag,
  createTag,
  getFriendAnnotation,
  getFriendInsight,
  getTags,
  getWorld,
  type Annotation,
  type Friend,
  type Tag,
  unassignFriendTag,
  updateFriendAnnotation,
  worldImageUrl,
} from '../api';
import {
  formatDateTime,
  friendName,
  parseBioLinks,
  platformLabel,
  statusLabel,
  statusTone,
} from '../format';
import { activityDetail, ratioLabel } from '../intelligence';
import { useHashParameters } from '../navigation';
import { Avatar } from './Avatar';
import { LocationText } from './LocationText';

type PlayerTab = 'overview' | 'activity' | 'worlds' | 'names' | 'notes';

const tabs: Array<{ id: PlayerTab; label: string }> = [
  { id: 'overview', label: '概览' },
  { id: 'activity', label: '活动' },
  { id: 'worlds', label: '世界' },
  { id: 'names', label: '名称' },
  { id: 'notes', label: '备注与标签' },
];

const isPlayerTab = (value: string | null): value is PlayerTab =>
  tabs.some((tab) => tab.id === value);

const shiftDate = (value: string, days: number) => {
  const date = new Date(`${value}T12:00:00`);
  date.setDate(date.getDate() + days);
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
};

const formatMinutes = (minutes: number) => formatSecondsCompact(Math.max(0, minutes) * 60);

const annotationFromConflict = (error: unknown): Annotation | null => {
  if (!(error instanceof ApiError) || error.status !== 409) return null;
  const details = error.details;
  if (!details || typeof details !== 'object' || !('server' in details)) return null;
  const value = details.server;
  if (!value || typeof value !== 'object') return null;
  const candidate = value as Partial<Annotation>;
  if (typeof candidate.friend_id !== 'string' || typeof candidate.note !== 'string') return null;
  return {
    friend_id: candidate.friend_id,
    note: candidate.note,
    pinned: Boolean(candidate.pinned),
    revision: typeof candidate.revision === 'string' ? candidate.revision : null,
    updated_at: typeof candidate.updated_at === 'string' ? candidate.updated_at : null,
    tags: Array.isArray(candidate.tags) ? candidate.tags : [],
  };
};

function PlayerMetrics({
  onlineMinutes,
  overlapMinutes,
  togetherMinutes,
  firstRecorded,
}: {
  onlineMinutes: number;
  overlapMinutes: number;
  togetherMinutes: number;
  firstRecorded: string | null;
}) {
  return (
    <dl className="player-metric-grid">
      <div><dt><Clock3 size={16} aria-hidden="true" />在线时长</dt><dd>{formatMinutes(onlineMinutes)}</dd></div>
      <div><dt><UsersRound size={16} aria-hidden="true" />同时在线</dt><dd>{formatMinutes(overlapMinutes)}</dd></div>
      <div><dt><Sparkles size={16} aria-hidden="true" />一起游玩</dt><dd>{formatMinutes(togetherMinutes)}</dd></div>
      <div><dt><History size={16} aria-hidden="true" />开始记录</dt><dd>{firstRecorded ? formatDateTime(firstRecorded) : '这段时间内'}</dd></div>
    </dl>
  );
}

function OverviewPanel({ friend }: { friend: Friend }) {
  const links = parseBioLinks(friend.bio_links);
  return (
    <div className="player-tab-content player-overview-grid">
      <section className="player-section" aria-labelledby="player-current-title">
        <h3 id="player-current-title">当前状态</h3>
        <dl className="profile-facts">
          <div>
            <dt><MapPin size={16} aria-hidden="true" />位置</dt>
            <dd><LocationText location={friend.location} status={friend.status} /></dd>
          </div>
          <div><dt><Monitor size={16} aria-hidden="true" />设备</dt><dd>{platformLabel(friend.platform)}</dd></div>
          <div><dt><UserRound size={16} aria-hidden="true" />状态文字</dt><dd>{friend.status_description || '没有公开状态文字'}</dd></div>
        </dl>
      </section>

      <section className="player-section" aria-labelledby="player-bio-title">
        <h3 id="player-bio-title">简介</h3>
        <p className={friend.bio ? 'bio-copy' : 'muted'}>{friend.bio || '这个玩家没有公开简介。'}</p>
        {links.length > 0 && (
          <ul className="link-list player-links">
            {links.map((link) => (
              <li key={link}>
                <a href={link} target="_blank" rel="noreferrer noopener">
                  <span>{new URL(link).hostname}</span><ExternalLink size={15} aria-hidden="true" />
                </a>
              </li>
            ))}
          </ul>
        )}
      </section>

      <details className="technical-details player-details">
        <summary>更多信息</summary>
        <dl>
          <div><dt>用户 ID</dt><dd>{friend.id}</dd></div>
          <div><dt>位置</dt><dd>{friend.location || '—'}</dd></div>
          <div><dt>平台</dt><dd>{friend.platform || '—'}</dd></div>
        </dl>
      </details>
    </div>
  );
}

function ActivityPanel({ cells }: { cells: Array<{
  hour: number;
  ratio: number | null;
  online_minutes: number;
  observed_minutes: number;
  eligible_minutes: number;
}> }) {
  return (
    <section className="player-tab-content player-section" aria-labelledby="player-activity-title">
      <div className="player-section-heading">
        <div><h3 id="player-activity-title">一天中的活跃时段</h3><p>颜色越亮，通常越容易在这个时段遇到。</p></div>
      </div>
      <div className="player-hourly-chart" role="list" aria-label="24 小时活动分布">
        {cells.map((cell) => (
          <div className="player-hour" role="listitem" key={cell.hour}>
            <span
              className={cell.ratio === null ? 'player-hour-cell no-data' : 'player-hour-cell'}
              style={cell.ratio === null ? undefined : { '--activity': Math.max(0.08, cell.ratio) } as React.CSSProperties}
              title={`${String(cell.hour).padStart(2, '0')}:00 · ${activityDetail(cell)}`}
              aria-label={`${String(cell.hour).padStart(2, '0')} 点，${activityDetail(cell)}`}
            />
            <small>{String(cell.hour).padStart(2, '0')}</small>
            <strong>{ratioLabel(cell.ratio)}</strong>
          </div>
        ))}
      </div>
      <p className="player-chart-note">“—”表示这个时段还没有足够记录。</p>
    </section>
  );
}

function WorldsPanel({
  worlds,
  onOpenWorld,
}: {
  worlds: Array<{ world_id: string; name: string; minutes: number; visits: number; last_observed: string }>;
  onOpenWorld: (worldId: string) => void;
}) {
  const details = useQueries({
    queries: worlds.map((world) => ({
      queryKey: ['world', world.world_id],
      queryFn: () => getWorld(world.world_id),
      staleTime: 60 * 60_000,
      retry: 1,
    })),
  });
  return (
    <section className="player-tab-content player-section" aria-labelledby="player-worlds-title">
      <div className="player-section-heading">
        <div><h3 id="player-worlds-title">常去世界</h3><p>按这段时间内的游玩时长排列。</p></div>
      </div>
      {worlds.length ? (
        <div className="player-world-grid">
          {worlds.map((world, index) => {
            const info = details[index]?.data;
            const image = info?.thumbnail_url || info?.image_url || '';
            return (
              <button key={world.world_id} type="button" className="player-world-card" onClick={() => onOpenWorld(world.world_id)}>
                {image ? <img src={worldImageUrl(image)} alt="" loading="lazy" decoding="async" /> : <span className="player-world-placeholder"><MapPin size={22} /></span>}
                <span>
                  <strong>{info?.name || world.name || world.world_id}</strong>
                  <small>{info?.author_name || world.world_id}</small>
                  <em>{formatMinutes(world.minutes)} · {world.visits} 次到访</em>
                </span>
              </button>
            );
          })}
        </div>
      ) : (
        <div className="empty-state"><MapPin size={26} aria-hidden="true" /><strong>这段时间没有世界记录</strong></div>
      )}
    </section>
  );
}

function NamesPanel({ events }: { events: Array<{
  event_id: string;
  field: 'username' | 'display_name';
  old_value: string;
  new_value: string;
  occurred_at: string;
}> }) {
  return (
    <section className="player-tab-content player-section" aria-labelledby="player-names-title">
      <div className="player-section-heading"><div><h3 id="player-names-title">名称变化</h3><p>曾经记录到的用户名和显示名。</p></div></div>
      {events.length ? (
        <ol className="identity-timeline">
          {events.map((event) => (
            <li key={event.event_id}>
              <span>{event.field === 'username' ? '用户名' : '显示名'}</span>
              <div><strong>{event.old_value || '—'} → {event.new_value || '—'}</strong><time dateTime={event.occurred_at}>{formatDateTime(event.occurred_at)}</time></div>
            </li>
          ))}
        </ol>
      ) : (
        <div className="empty-state"><History size={26} aria-hidden="true" /><strong>还没有名称变化</strong></div>
      )}
    </section>
  );
}

function NotesPanel({ friendId }: { friendId: string }) {
  const queryClient = useQueryClient();
  const annotation = useQuery({
    queryKey: ['annotation', friendId],
    queryFn: () => getFriendAnnotation(friendId),
    enabled: Boolean(friendId),
  });
  const tags = useQuery({ queryKey: ['tags'], queryFn: getTags, staleTime: 60_000 });
  const [loadedFriend, setLoadedFriend] = useState('');
  const [note, setNote] = useState('');
  const [pinned, setPinned] = useState(false);
  const [baseline, setBaseline] = useState<{ note: string; pinned: boolean; revision: string | null }>({
    note: '', pinned: false, revision: null,
  });
  const [saveState, setSaveState] = useState<'idle' | 'saving' | 'saved' | 'error' | 'conflict'>('idle');
  const [conflict, setConflict] = useState<Annotation | null>(null);
  const [newTag, setNewTag] = useState('');
  const [tagColor, setTagColor] = useState('#8bd450');

  useEffect(() => {
    if (!annotation.data || loadedFriend === friendId) return;
    setLoadedFriend(friendId);
    setNote(annotation.data.note);
    setPinned(annotation.data.pinned);
    setBaseline({ note: annotation.data.note, pinned: annotation.data.pinned, revision: annotation.data.revision });
    setSaveState('idle');
    setConflict(null);
  }, [annotation.data, friendId, loadedFriend]);

  const save = useMutation({
    mutationFn: (value: { note: string; pinned: boolean; revision: string | null }) =>
      updateFriendAnnotation(friendId, value),
    onMutate: () => setSaveState('saving'),
    onSuccess: (result, variables) => {
      setBaseline({ note: variables.note, pinned: variables.pinned, revision: result.revision });
      setSaveState('saved');
      setConflict(null);
      queryClient.setQueryData(['annotation', friendId], result);
    },
    onError: (error) => {
      const server = annotationFromConflict(error);
      if (server) {
        setConflict(server);
        setSaveState('conflict');
      } else {
        setSaveState('error');
      }
    },
  });

  const dirty = note !== baseline.note || pinned !== baseline.pinned;
  const saveAnnotation = save.mutate;
  const savePending = save.isPending;
  useEffect(() => {
    if (!loadedFriend || !dirty || savePending || conflict) return;
    const timer = window.setTimeout(() => {
      saveAnnotation({ note, pinned, revision: baseline.revision });
    }, 700);
    return () => window.clearTimeout(timer);
  }, [baseline.revision, conflict, dirty, loadedFriend, note, pinned, saveAnnotation, savePending]);

  const refreshOrganization = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['annotation', friendId] }),
      queryClient.invalidateQueries({ queryKey: ['tags'] }),
      queryClient.invalidateQueries({ queryKey: ['search'] }),
    ]);
  };

  const tagMutation = useMutation({
    mutationFn: async ({ tag, active }: { tag: Tag; active: boolean }) => {
      if (active) await unassignFriendTag(friendId, tag.id);
      else await assignFriendTag(friendId, tag.id);
    },
    onSuccess: refreshOrganization,
  });

  const createMutation = useMutation({
    mutationFn: async (value: { name: string; color: string }) => {
      const tag = await createTag(value);
      await assignFriendTag(friendId, tag.id);
      return tag;
    },
    onSuccess: async () => {
      setNewTag('');
      await refreshOrganization();
    },
  });

  const activeTagIds = new Set(annotation.data?.tags.map((tag) => tag.id) ?? []);
  const saveLabel = {
    idle: dirty ? '等待保存' : '已保存',
    saving: '正在保存',
    saved: dirty ? '正在保存新修改' : '已保存',
    error: '保存失败',
    conflict: '发现另一份修改',
  }[saveState];

  const createNewTag = (event: FormEvent) => {
    event.preventDefault();
    const name = newTag.trim();
    if (name) createMutation.mutate({ name, color: tagColor });
  };

  return (
    <div className="player-tab-content notes-layout">
      <section className="player-section" aria-labelledby="player-note-title">
        <div className="player-section-heading note-heading">
          <div><h3 id="player-note-title">我的备注</h3><p>记下称呼、共同话题或下次想做的事。</p></div>
          <span className={`save-state save-${saveState}`} role="status">
            {saveState === 'saving' ? <RefreshCw className="spinning" size={14} /> : saveState === 'saved' && !dirty ? <Check size={14} /> : <Save size={14} />}
            {saveLabel}
          </span>
        </div>
        {annotation.isPending ? (
          <div className="panel-state" role="status">正在打开备注…</div>
        ) : annotation.isError && !annotation.data ? (
          <div className="inline-note-error" role="alert">
            <span>备注暂时没有加载出来。</span>
            <button className="button button-secondary button-compact" onClick={() => void annotation.refetch()}>重试</button>
          </div>
        ) : (
          <>
            <label className="note-field">
              <span className="sr-only">玩家备注</span>
              <textarea value={note} onChange={(event) => setNote(event.target.value)} maxLength={20_000} placeholder="写点只有你能看到的备注…" />
            </label>
            <button type="button" className={pinned ? 'pin-toggle active' : 'pin-toggle'} aria-pressed={pinned} onClick={() => setPinned((value) => !value)}>
              <Bookmark size={16} fill={pinned ? 'currentColor' : 'none'} aria-hidden="true" />
              {pinned ? '已置顶这位玩家' : '置顶这位玩家'}
            </button>
          </>
        )}

        {saveState === 'error' && (
          <div className="inline-note-error" role="alert">
            <span>这次修改还没有保存。</span>
            <button className="button button-secondary button-compact" onClick={() => save.mutate({ note, pinned, revision: baseline.revision })}>重试</button>
          </div>
        )}
        {conflict && (
          <div className="note-conflict" role="alert">
            <strong>另一台设备刚刚也修改了备注</strong>
            <p>{conflict.note || '另一份备注为空。'}</p>
            <div>
              <button type="button" className="button button-secondary button-compact" onClick={() => {
                setNote(conflict.note);
                setPinned(conflict.pinned);
                setBaseline({ note: conflict.note, pinned: conflict.pinned, revision: conflict.revision });
                setConflict(null);
                setSaveState('saved');
              }}>使用另一份</button>
              <button type="button" className="button button-primary button-compact" onClick={() => {
                const revision = conflict.revision;
                setConflict(null);
                save.mutate({ note, pinned, revision });
              }}>保留我的修改</button>
            </div>
          </div>
        )}
      </section>

      <section className="player-section" aria-labelledby="player-tags-title">
        <div className="player-section-heading"><div><h3 id="player-tags-title">标签</h3><p>用自己的方式整理玩家。</p></div></div>
        {tags.isPending ? <div className="panel-state" role="status">正在加载标签…</div> : (
          <div className="tag-picker">
            {tags.data?.map((tag) => {
              const active = activeTagIds.has(tag.id);
              return (
                <button
                  key={tag.id}
                  type="button"
                  className={active ? 'tag-choice active' : 'tag-choice'}
                  aria-pressed={active}
                  disabled={tagMutation.isPending}
                  onClick={() => tagMutation.mutate({ tag, active })}
                  style={{ '--tag-color': tag.color } as React.CSSProperties}
                >
                  <span aria-hidden="true" />{tag.name}{active && <Check size={13} aria-hidden="true" />}
                </button>
              );
            })}
            {!tags.data?.length && <p className="muted">还没有标签，可以在下面新建一个。</p>}
          </div>
        )}
        <form className="new-tag-form" onSubmit={createNewTag}>
          <input type="color" value={tagColor} onChange={(event) => setTagColor(event.target.value)} aria-label="标签颜色" />
          <label className="text-field-wrap">
            <TagIcon size={16} aria-hidden="true" />
            <input value={newTag} onChange={(event) => setNewTag(event.target.value)} maxLength={80} placeholder="新标签名称" aria-label="新标签名称" />
          </label>
          <button type="submit" className="button button-secondary" disabled={!newTag.trim() || createMutation.isPending}>
            <Plus size={16} aria-hidden="true" />添加
          </button>
        </form>
        {(tagMutation.isError || createMutation.isError) && <p className="form-error" role="alert">标签没有更新，请重试。</p>}
      </section>
    </div>
  );
}

export function FriendDialog({
  friendId,
  initialFriend,
  onClose,
  onOpenWorld,
}: {
  friendId: string | null;
  initialFriend?: Friend | null;
  onClose: () => void;
  onOpenWorld: (worldId: string) => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const { parameters, update } = useHashParameters();
  const today = todayKey();
  const requestedFrom = parameters.get('personFrom');
  const requestedTo = parameters.get('personTo');
  const rangeTo = isDateKey(requestedTo) && requestedTo <= today ? requestedTo : today;
  const rangeFrom = isDateKey(requestedFrom) && requestedFrom <= rangeTo ? requestedFrom : shiftDate(rangeTo, -29);
  const tab = isPlayerTab(parameters.get('personTab')) ? parameters.get('personTab') as PlayerTab : 'overview';
  const insight = useQuery({
    queryKey: ['friend-insight', friendId, rangeFrom, rangeTo],
    queryFn: () => getFriendInsight(friendId ?? '', rangeFrom, rangeTo),
    enabled: Boolean(friendId),
    staleTime: 60_000,
  });

  useEffect(() => {
    const element = dialog.current;
    if (!element) return;
    if (friendId && !element.open) element.showModal();
    if (!friendId && element.open) element.close();
  }, [friendId]);

  if (!friendId) return null;
  const friend = insight.data?.friend ?? initialFriend;
  const name = friend ? friendName(friend) : friendId;

  return (
    <dialog
      className="dialog player-dialog"
      ref={dialog}
      aria-labelledby="player-dialog-title"
      onClose={onClose}
      onCancel={onClose}
      onClick={(event) => {
        if (event.target === dialog.current) dialog.current?.close();
      }}
    >
      <div className="player-dialog-shell">
        <button className="icon-button dialog-close player-dialog-close" onClick={() => dialog.current?.close()} aria-label="关闭玩家详情">
          <X size={20} aria-hidden="true" />
        </button>

        <header className="player-dialog-header">
          {friend ? <Avatar friend={friend} size="large" /> : <span className="avatar avatar-large"><UserRound size={28} /></span>}
          <div className="player-dialog-title">
            {friend && <span className={`status-badge tone-${statusTone(friend.status)}`}>{statusLabel(friend.status)}</span>}
            <h2 id="player-dialog-title">{name}</h2>
            {friend?.username && friend.username !== name && <p>@{friend.username}</p>}
            {Boolean(friend?.is_self) && <span className="self-label">自己</span>}
          </div>
          <div className="player-range">
            <label><span>从</span><input type="date" value={rangeFrom} max={rangeTo} onChange={(event) => update({ personFrom: event.target.value || null })} /></label>
            <label><span>到</span><input type="date" value={rangeTo} min={rangeFrom} max={today} onChange={(event) => update({ personTo: event.target.value || null })} /></label>
          </div>
        </header>

        <nav className="player-tabs" aria-label="玩家详情">
          {tabs.map((item) => (
            <button key={item.id} type="button" className={tab === item.id ? 'active' : ''} aria-current={tab === item.id ? 'page' : undefined} onClick={() => update({ personTab: item.id })}>
              {item.label}
            </button>
          ))}
        </nav>

        <div className="player-dialog-body">
          {insight.isPending ? (
            <div className="dialog-loading" role="status"><RefreshCw className="spinning" size={24} /><strong>正在打开玩家详情…</strong></div>
          ) : insight.isError || !insight.data || !friend ? (
            <div className="inline-error" role="alert">
              <UserRound size={26} aria-hidden="true" />
              <strong>玩家详情暂时没有加载出来</strong>
              <span>{insight.error instanceof ApiError ? insight.error.message : '请稍后重试'}</span>
              <button type="button" className="button button-secondary" onClick={() => void insight.refetch()}><RefreshCw size={16} />重新加载</button>
            </div>
          ) : (
            <>
              <PlayerMetrics
                onlineMinutes={insight.data.online_minutes}
                overlapMinutes={insight.data.online_overlap_minutes}
                togetherMinutes={insight.data.co_presence_minutes}
                firstRecorded={insight.data.first_recorded_at}
              />
              {tab === 'overview' && <OverviewPanel friend={friend} />}
              {tab === 'activity' && <ActivityPanel cells={insight.data.hourly_activity} />}
              {tab === 'worlds' && <WorldsPanel worlds={insight.data.most_visited_worlds} onOpenWorld={onOpenWorld} />}
              {tab === 'names' && <NamesPanel events={insight.data.identity_events} />}
              {tab === 'notes' && <NotesPanel friendId={friendId} />}
            </>
          )}
        </div>
      </div>
    </dialog>
  );
}
