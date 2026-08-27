import {
  Database,
  History,
  LayoutDashboard,
  LogOut,
  RefreshCw,
  Users,
} from 'lucide-react';
import type { ReactNode } from 'react';

import type { Identity, Overview } from '../api';
import { formatDateTime } from '../format';
import type { View } from '../navigation';
import { Brand } from './Brand';

const items = [
  { id: 'overview', label: '总览', icon: LayoutDashboard },
  { id: 'people', label: '玩家', icon: Users },
  { id: 'history', label: '历史', icon: History },
  { id: 'data', label: '数据', icon: Database },
] as const;

function Navigation({ view, onNavigate }: { view: View; onNavigate: (view: View) => void }) {
  return (
    <nav className="product-nav" aria-label="主要导航">
      {items.map((item) => {
        const Icon = item.icon;
        const active = item.id === view;
        return (
          <a
            key={item.id}
            href={`#view=${item.id}`}
            className={active ? 'nav-link active' : 'nav-link'}
            aria-current={active ? 'page' : undefined}
            onClick={(event) => {
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
  view,
  refreshing,
  onNavigate,
  onRefresh,
  onLogout,
  children,
}: {
  identity: Identity;
  overview: Overview | undefined;
  refreshFailed: boolean;
  view: View;
  refreshing: boolean;
  onNavigate: (view: View) => void;
  onRefresh: () => void;
  onLogout: () => void;
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
        <Navigation view={view} onNavigate={onNavigate} />
        <div className="sidebar-footer">
          <div className="connection-summary">
            <span className={connected ? 'signal-dot connected' : 'signal-dot'} aria-hidden="true" />
            <div>
              <strong>{stateLabel}</strong>
              <span>{overview?.last_sync ? formatDateTime(overview.last_sync) : '尚未同步'}</span>
            </div>
          </div>
          <button className="quiet-action" onClick={onLogout}>
            <LogOut size={17} aria-hidden="true" />
            退出这台设备
          </button>
        </div>
      </aside>

      <div className="workspace">
        <header className="topbar">
          <Brand compact />
          <div className="topbar-copy">
            <span>{identity.name}</span>
            <strong>远程查看器</strong>
          </div>
          <div className="topbar-actions">
            <button className="icon-button" onClick={onRefresh} disabled={refreshing} aria-label="刷新当前数据">
              <RefreshCw className={refreshing ? 'spinning' : ''} size={19} aria-hidden="true" />
            </button>
            <button className="avatar-menu" onClick={onLogout} aria-label="退出这台设备">
              {Array.from(identity.name)[0] ?? 'P'}
            </button>
          </div>
        </header>
        <main id="main-content" className="main-content" tabIndex={-1}>
          {children}
        </main>
      </div>

      <div className="mobile-nav-wrap">
        <Navigation view={view} onNavigate={onNavigate} />
      </div>
    </div>
  );
}
