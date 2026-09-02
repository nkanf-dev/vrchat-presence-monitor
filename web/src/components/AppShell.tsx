import {
  Activity,
  ChartNoAxesCombined,
  Ellipsis,
  LayoutDashboard,
  RefreshCw,
  Users,
} from 'lucide-react';
import type { MouseEvent, ReactNode } from 'react';

import type { Identity, Overview } from '../api';
import { formatDateTime } from '../format';
import { routeForArea, routeHref, type Area, type Route } from '../navigation';
import { AccountMenu } from './AccountMenu';
import { Brand } from './Brand';
import { GlobalSearch } from './GlobalSearch';

const items = [
  { id: 'online', label: '在线', icon: Activity },
  { id: 'people', label: '玩家', icon: Users },
  { id: 'dashboard', label: '仪表盘', icon: LayoutDashboard },
  { id: 'analysis', label: '分析', icon: ChartNoAxesCombined },
  { id: 'more', label: '更多', icon: Ellipsis },
] as const satisfies ReadonlyArray<{ id: Area; label: string; icon: typeof Activity }>;

const shouldHandleNavigation = (event: MouseEvent<HTMLAnchorElement>) =>
  event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey;

function Navigation({
  route,
  parameters,
  label,
  onNavigate,
}: {
  route: Route;
  parameters: URLSearchParams;
  label: string;
  onNavigate: (area: Area) => void;
}) {
  return (
    <nav className="product-nav" aria-label={label}>
      {items.map((item) => {
        const Icon = item.icon;
        const active = item.id === route.area;
        return (
          <a
            key={item.id}
            href={routeHref(parameters, routeForArea(item.id, route))}
            className={active ? 'nav-link active' : 'nav-link'}
            aria-current={active ? 'page' : undefined}
            onClick={(event) => {
              if (!shouldHandleNavigation(event)) return;
              event.preventDefault();
              onNavigate(item.id);
            }}
          >
            <Icon size={19} strokeWidth={1.8} aria-hidden="true" />
            <span>{item.label}</span>
          </a>
        );
      })}
    </nav>
  );
}

export function AppShell({
  identity,
  overview,
  refreshFailed,
  route,
  parameters,
  refreshing,
  accountBusy,
  onNavigate,
  onRefresh,
  onLogout,
  onDisconnect,
  children,
}: {
  identity: Identity;
  overview: Overview | undefined;
  refreshFailed: boolean;
  route: Route;
  parameters: URLSearchParams;
  refreshing: boolean;
  accountBusy: boolean;
  onNavigate: (area: Area) => void;
  onRefresh: () => void;
  onLogout: () => Promise<void> | void;
  onDisconnect: () => Promise<void> | void;
  children: ReactNode;
}) {
  const state = refreshFailed ? 'error' : (overview?.collector_state ?? 'never');
  const connected = state === 'fresh';
  const stateLabel = {
    fresh: '数据已连接',
    stale: '数据已过期',
    error: '连接异常',
    never: '等待数据',
  }[state];

  return (
    <div className="product-shell">
      <a className="skip-link" href="#main-content">
        跳到主要内容
      </a>
      <aside className="sidebar">
        <Brand />
        <div className="sidebar-workspace">
          <span className="sidebar-label">你的空间</span>
          <strong>{identity.name}</strong>
        </div>
        <Navigation route={route} parameters={parameters} label="主要导航" onNavigate={onNavigate} />
        <div className="sidebar-footer">
          <div className="connection-summary">
            <span className={connected ? 'signal-dot connected' : 'signal-dot'} aria-hidden="true" />
            <div>
              <strong>{stateLabel}</strong>
              <span>{overview?.last_sync ? formatDateTime(overview.last_sync) : '尚未同步'}</span>
            </div>
          </div>
        </div>
      </aside>

      <div className="workspace">
        <header className="topbar">
          <Brand compact />
          <div className="topbar-copy">
            <span>{identity.name}</span>
            <strong>云端状态空间</strong>
          </div>
          <div className="topbar-actions">
            <GlobalSearch />
            <button className="icon-button" onClick={onRefresh} disabled={refreshing} aria-label="刷新当前数据">
              <RefreshCw className={refreshing ? 'spinning' : ''} size={19} aria-hidden="true" />
            </button>
            <AccountMenu
              identity={identity}
              overview={overview}
              busy={accountBusy}
              onLogout={onLogout}
              onDisconnect={onDisconnect}
            />
          </div>
        </header>
        <main id="main-content" className="main-content" tabIndex={-1}>
          {children}
        </main>
      </div>

      <div className="mobile-nav-wrap">
        <Navigation
          route={route}
          parameters={parameters}
          label="移动端主要导航"
          onNavigate={onNavigate}
        />
      </div>
    </div>
  );
}
