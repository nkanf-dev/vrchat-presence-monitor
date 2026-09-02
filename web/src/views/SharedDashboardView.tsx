import { useMutation, useQuery } from '@tanstack/react-query';
import { LockKeyhole, RefreshCw } from 'lucide-react';
import { useEffect, useState } from 'react';

import { ApiError, type DashboardShareAppearance, getPublicDashboard, unlockPublicDashboard } from '../api';
import { Brand } from '../components/Brand';
import { DashboardPanel } from '../components/DashboardPanel';
import { formatDateTime } from '../format';

function SharedPageMetadata({ appearance, fallbackTitle }: { appearance: DashboardShareAppearance; fallbackTitle: string }) {
  useEffect(() => {
    const previousTitle = document.title;
    const icon = document.querySelector<HTMLLinkElement>('link[rel~="icon"]');
    const previousIcon = icon?.href ?? '';
    document.title = appearance.page_title || appearance.heading || fallbackTitle;
    if (icon && appearance.avatar_url) icon.href = appearance.avatar_url;
    return () => {
      document.title = previousTitle;
      if (icon) icon.href = previousIcon || '/icon.svg';
    };
  }, [appearance, fallbackTitle]);
  return appearance.custom_css ? <style>{appearance.custom_css}</style> : null;
}

function SharedAvatar({ appearance }: { appearance: DashboardShareAppearance }) {
  return appearance.avatar_url
    ? <img className="shared-dashboard-avatar" src={appearance.avatar_url} alt="" referrerPolicy="no-referrer" />
    : <Brand />;
}

export function SharedDashboardView({ shareId }: { shareId: string }) {
  const [password, setPassword] = useState('');
  const shared = useQuery({
    queryKey: ['public-dashboard', shareId],
    queryFn: () => getPublicDashboard(shareId),
    retry: false,
    refetchInterval: (query) => query.state.data?.locked ? false : 60_000,
  });
  const unlock = useMutation({
    mutationFn: () => unlockPublicDashboard(shareId, password),
    onSuccess: async () => {
      setPassword('');
      await shared.refetch();
    },
  });

  if (shared.isPending) return <main className="shared-dashboard-state"><Brand /><span>正在打开仪表盘…</span></main>;
  if (shared.isError) return <main className="shared-dashboard-state"><Brand /><strong>这个分享暂时无法打开</strong><span>{shared.error instanceof ApiError ? shared.error.message : '请稍后重试'}</span><button type="button" className="button button-secondary" onClick={() => void shared.refetch()}>重试</button></main>;
  if (shared.data.locked) return (
    <div className={`shared-dashboard-shell share-theme-${shared.data.appearance.preset}`}>
      <SharedPageMetadata appearance={shared.data.appearance} fallbackTitle={shared.data.title} />
      <main className="shared-dashboard-unlock">
        <SharedAvatar appearance={shared.data.appearance} />
        <form onSubmit={(event) => { event.preventDefault(); unlock.mutate(); }}>
          <span className="shared-dashboard-lock"><LockKeyhole size={22} /></span>
          <h1>{shared.data.appearance.heading || shared.data.title}</h1>
          <p>{shared.data.appearance.description || '输入密码查看仪表盘'}</p>
          <input type="password" autoFocus autoComplete="current-password" value={password} maxLength={256} aria-label="访问密码" placeholder="访问密码" onChange={(event) => setPassword(event.target.value)} />
          <button type="submit" className="button button-primary" disabled={!password || unlock.isPending}>{unlock.isPending ? '正在进入…' : '进入仪表盘'}</button>
          {unlock.isError && <span className="dashboard-save-error" role="alert">{unlock.error instanceof ApiError ? unlock.error.message : '密码不正确'}</span>}
        </form>
      </main>
    </div>
  );

  const { document, data } = shared.data;
  const appearance = shared.data.appearance;
  return (
    <div className={`shared-dashboard-shell share-theme-${appearance.preset}`}>
      <SharedPageMetadata appearance={appearance} fallbackTitle={document.title} />
      <header className="shared-dashboard-topbar"><SharedAvatar appearance={appearance} /><span>{appearance.heading || document.title}</span><button type="button" className="icon-button" onClick={() => void shared.refetch()} aria-label="刷新"><RefreshCw className={shared.isFetching ? 'spinning' : ''} size={18} /></button></header>
      <main className="shared-dashboard-main">
        <header className="page-heading"><div><p className="kicker">Shared dashboard</p><h1>{appearance.heading || document.title}</h1><p>{appearance.description || `发布于 ${formatDateTime(shared.data.published_at)} · 数据持续更新`}</p></div></header>
        <div className="shared-dashboard-grid">
          {[...document.panels].sort((left, right) => left.y - right.y || left.x - right.x).map((panel) => <article
            key={panel.id}
            className="dashboard-grid-panel panel"
            style={{
              gridColumn: `${Math.min(12, Math.max(0, panel.x)) + 1} / span ${Math.min(12, Math.max(1, panel.w))}`,
              gridRow: `${Math.max(0, panel.y) + 1} / span ${Math.max(3, panel.h)}`,
            }}
          >
            <header className="dashboard-panel-heading"><div><h2>{panel.title}</h2><span>{panel.range_days ? `${panel.range_days} 天` : `跟随全局 · ${document.range_days} 天`}</span></div></header>
            <div className="dashboard-panel-body"><DashboardPanel panel={panel} globalRangeDays={document.range_days} {...(data[panel.id] ? { data: data[panel.id] } : {})} /></div>
          </article>)}
        </div>
      </main>
    </div>
  );
}
