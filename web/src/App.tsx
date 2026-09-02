import { useIsFetching, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { RefreshCw } from 'lucide-react';
import { lazy, Suspense, useEffect, useState } from 'react';

import {
  ApiError,
  AUTH_REQUIRED_EVENT,
  type Friend,
  disconnectVrchat,
  getEvents,
  getFriends,
  getMe,
  getOverview,
  loginVrchat,
  logout,
  syncNow,
  verifyVrchat2fa,
} from './api';
import { AppShell } from './components/AppShell';
import { AnalysisNav } from './components/AnalysisNav';
import { FriendDialog } from './components/FriendDialog';
import { LoadingScreen, LoginScreen, OfflineScreen, VrchatReconnectDialog } from './components/AuthScreens';
import { MoreNav } from './components/MoreNav';
import { StatusBanner } from './components/StatusBanner';
import { WorldDialog } from './components/WorldDialog';
import { useHashRoute, usePageScrollRestoration } from './navigation';
import { DataView } from './views/DataView';
import { HistoryView } from './views/HistoryView';
import { OverviewView } from './views/OverviewView';
import { PeopleView } from './views/PeopleView';
import { DailyView } from './views/DailyView';
import { DiscoveryView } from './views/DiscoveryView';
import { WorldsView } from './views/WorldsView';

const DashboardView = lazy(() => import('./views/DashboardView').then((module) => ({ default: module.DashboardView })));

function InitialContentError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <section className="panel page-error" role="alert">
      <span className="error-orbit" aria-hidden="true" />
      <h1>数据暂时没有加载出来</h1>
      <p>{message}。登录仍然有效，可以稍后重试。</p>
      <button className="button button-primary" onClick={onRetry}>
        <RefreshCw size={17} aria-hidden="true" />
        重新加载
      </button>
    </section>
  );
}

function AnalysisLanding() {
  const content = {
    kicker: 'Relationships',
    title: '好友关系',
    copy: '从共同在线和一起游玩的记录中查看好友之间的联系。',
  };
  return (
    <>
      <header className="page-heading">
        <div>
          <p className="kicker">{content.kicker}</p>
          <h1 tabIndex={-1}>{content.title}</h1>
          <p>{content.copy}</p>
        </div>
      </header>
      <section className="panel empty-state">
        <strong>选择一个分析视图</strong>
        <p>前往每日在线查看时段规律，或在世界时间轴中回看游玩地点。</p>
      </section>
    </>
  );
}

function SettingsView() {
  return (
    <>
      <header className="page-heading">
        <div>
          <p className="kicker">Settings</p>
          <h1 tabIndex={-1}>设置</h1>
          <p>管理当前账户与数据采集。</p>
        </div>
      </header>
      <section className="panel empty-state" aria-labelledby="account-settings-title">
        <strong id="account-settings-title">账户与连接</strong>
        <p>点击右上角头像，可以重新连接 VRChat、切换账号或退出当前设备。</p>
      </section>
    </>
  );
}

function Dashboard({ identity }: { identity: { tenant_id: string; name: string } }) {
  const queryClient = useQueryClient();
  const {
    parameters,
    update,
    openDetail,
    closeDetail,
    route,
    routeKey,
    view,
    navigateArea,
    navigateAnalysis,
    navigateMore,
    navigateView,
  } = useHashRoute();
  usePageScrollRestoration(routeKey);
  const [friendPreview, setFriendPreview] = useState<Friend | null>(null);
  const [loggingOut, setLoggingOut] = useState(false);
  const [reconnectOpen, setReconnectOpen] = useState(false);
  const [reconnectTwoFactor, setReconnectTwoFactor] = useState(false);
  const reconnectMutation = useMutation({ mutationFn: loginVrchat });
  const reconnectTwoFactorMutation = useMutation({ mutationFn: verifyVrchat2fa });
  const disconnectMutation = useMutation({ mutationFn: disconnectVrchat });
  const syncMutation = useMutation({ mutationFn: syncNow });
  const overviewQuery = useQuery({
    queryKey: ['overview'],
    queryFn: getOverview,
    refetchInterval: 60_000,
  });
  const peopleQuery = useQuery({
    queryKey: ['friends', 'overview'],
    queryFn: () => getFriends({ status: 'online', limit: 50, offset: 0 }),
    refetchInterval: 60_000,
    enabled: route.area === 'online',
  });
  const eventsQuery = useQuery({
    queryKey: ['events', 'overview'],
    queryFn: () => getEvents({ limit: 8, offset: 0 }),
    refetchInterval: 60_000,
    enabled: route.area === 'online',
  });
  const activeFetches = useIsFetching();

  const errors = [overviewQuery.error, peopleQuery.error, eventsQuery.error];
  const unauthorized = errors.some((error) => error instanceof ApiError && error.status === 401);
  useEffect(() => {
    if (unauthorized) void queryClient.invalidateQueries({ queryKey: ['me'] });
  }, [queryClient, unauthorized]);

  useEffect(() => {
    if (route.area === 'online' && overviewQuery.isPending) return;
    const frame = window.requestAnimationFrame(() => {
      const title = document.querySelector<HTMLElement>('#main-content h1');
      title?.focus({ preventScroll: true });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [overviewQuery.isPending, route.area, routeKey]);

  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['overview'] }),
      queryClient.invalidateQueries({ queryKey: ['friends'] }),
      queryClient.invalidateQueries({ queryKey: ['events'] }),
      queryClient.invalidateQueries({ queryKey: ['analytics'] }),
      queryClient.invalidateQueries({ queryKey: ['world'] }),
      queryClient.invalidateQueries({ queryKey: ['capabilities'] }),
      queryClient.invalidateQueries({ queryKey: ['dashboard-data'] }),
    ]);
  };

  const signOut = async () => {
    if (loggingOut) return;
    setLoggingOut(true);
    try {
      await logout();
      queryClient.removeQueries();
      window.location.hash = '';
      window.location.reload();
    } catch (error) {
      setLoggingOut(false);
      throw error;
    }
  };

  const disconnect = async () => {
    await disconnectMutation.mutateAsync();
    setReconnectOpen(false);
    setReconnectTwoFactor(false);
    await refresh();
  };

  const syncAndRefresh = async () => {
    try {
      await syncMutation.mutateAsync();
    } catch {
      // The overview banner reflects the last successful collection while the retry is pending.
    } finally {
      await refresh();
    }
  };

  const refreshing = activeFetches > 0 || syncMutation.isPending;
  // Preview failures stay inside their own panels. Only the overview request
  // controls the global connection state shown by the shell and banner.
  const refreshFailed = overviewQuery.isError;
  const viewLabel = {
    overview: '状态总览',
    daily: '每日在线',
    worlds: '在线与世界',
    people: '玩家列表',
    history: '状态历史',
    data: '数据与备份',
    settings: '设置',
    relationships: '好友关系',
    discover: '世界发现',
    dashboard: '自定义图表',
  }[view];
  const friendDetailId = parameters.get('personDetail');
  const worldDetailId = parameters.get('worldDetail');
  const openFriend = (friend: Friend) => {
    setFriendPreview(friend);
    openDetail('person', friend.id, { personTab: null });
  };
  const openWorld = (worldId: string) => openDetail('world', worldId);

  return (
    <AppShell
      identity={identity}
      overview={overviewQuery.data}
      refreshFailed={refreshFailed}
      route={route}
      parameters={parameters}
      refreshing={refreshing}
      accountBusy={loggingOut || disconnectMutation.isPending}
      onNavigate={navigateArea}
      onRefresh={() => void syncAndRefresh()}
      onLogout={signOut}
      onDisconnect={disconnect}
    >
      <span className="sr-only" role="status" aria-live="polite">已进入{viewLabel}</span>
      {!overviewQuery.isPending &&
        (overviewQuery.data || (overviewQuery.isError && view !== 'overview')) && (
        <StatusBanner
          overview={overviewQuery.data}
          refreshFailed={refreshFailed}
          onRetry={() => void refresh()}
          onReconnect={() => {
            reconnectMutation.reset();
            reconnectTwoFactorMutation.reset();
            setReconnectTwoFactor(false);
            setReconnectOpen(true);
          }}
        />
      )}
      {route.area === 'analysis' && (
        <AnalysisNav section={route.section} parameters={parameters} onNavigate={navigateAnalysis} />
      )}
      {route.area === 'more' && (
        <MoreNav section={route.section} parameters={parameters} onNavigate={navigateMore} />
      )}
      {view === 'overview' && overviewQuery.isPending ? (
        <section className="dashboard-skeleton" aria-label="正在加载状态总览" aria-busy="true">
          <div className="skeleton skeleton-title" />
          <div className="skeleton-grid">
            {Array.from({ length: 4 }, (_, index) => (
              <div className="skeleton skeleton-card" key={index} />
            ))}
          </div>
          <div className="skeleton skeleton-panel" />
        </section>
      ) : view === 'overview' && overviewQuery.isError && !overviewQuery.data ? (
        <InitialContentError
          message={overviewQuery.error instanceof ApiError ? overviewQuery.error.message : '未知错误'}
          onRetry={() => void overviewQuery.refetch()}
        />
      ) : view === 'overview' && overviewQuery.data ? (
        <OverviewView
          overview={overviewQuery.data}
          people={peopleQuery.data}
          events={eventsQuery.data}
          peoplePending={peopleQuery.isPending}
          peopleFailed={peopleQuery.isError}
          peopleFetching={peopleQuery.isFetching}
          eventsPending={eventsQuery.isPending}
          eventsFailed={eventsQuery.isError}
          eventsFetching={eventsQuery.isFetching}
          onRetryPeople={() => void peopleQuery.refetch()}
          onRetryEvents={() => void eventsQuery.refetch()}
          onOpenFriend={openFriend}
          onNavigatePeople={() => navigateView('people')}
          onNavigateHistory={() => navigateView('history')}
        />
      ) : null}
      {view === 'daily' && <DailyView />}
      {view === 'worlds' && <WorldsView />}
      {view === 'discover' && <DiscoveryView onOpenWorld={openWorld} />}
      {view === 'dashboard' && (
        <Suspense fallback={<div className="dashboard-workspace-loading" role="status">正在打开仪表盘…</div>}>
          <DashboardView parameters={parameters} onUpdateParameters={update} />
        </Suspense>
      )}
      {view === 'people' && <PeopleView onOpenFriend={openFriend} />}
      {view === 'history' && <HistoryView />}
      {view === 'data' && <DataView />}
      {view === 'relationships' && <AnalysisLanding />}
      {view === 'settings' && <SettingsView />}
      <FriendDialog
        friendId={friendDetailId}
        initialFriend={friendPreview?.id === friendDetailId ? friendPreview : null}
        onClose={() => {
          setFriendPreview(null);
          if (friendDetailId) closeDetail('person', friendDetailId, { personTab: null });
        }}
        onOpenWorld={openWorld}
      />
      <WorldDialog
        worldId={worldDetailId}
        onClose={() => {
          if (worldDetailId) closeDetail('world', worldDetailId);
        }}
      />
      <VrchatReconnectDialog
        open={reconnectOpen}
        pending={reconnectMutation.isPending || reconnectTwoFactorMutation.isPending}
        error={reconnectTwoFactor ? reconnectTwoFactorMutation.error : reconnectMutation.error}
        requiresTwoFactor={reconnectTwoFactor}
        onClose={() => {
          if (reconnectMutation.isPending || reconnectTwoFactorMutation.isPending) return;
          setReconnectOpen(false);
          setReconnectTwoFactor(false);
        }}
        onEdit={() => {
          reconnectMutation.reset();
          reconnectTwoFactorMutation.reset();
        }}
        onBack={() => {
          reconnectMutation.reset();
          reconnectTwoFactorMutation.reset();
          setReconnectTwoFactor(false);
        }}
        onLogin={async (credentials) => {
          const result = await reconnectMutation.mutateAsync(credentials);
          if (result.requires_2fa) {
            setReconnectTwoFactor(true);
            return;
          }
          setReconnectOpen(false);
          setReconnectTwoFactor(false);
          await refresh();
        }}
        onVerify={async (code) => {
          await reconnectTwoFactorMutation.mutateAsync(code);
          setReconnectOpen(false);
          setReconnectTwoFactor(false);
          await refresh();
        }}
      />
    </AppShell>
  );
}

export function App() {
  const queryClient = useQueryClient();
  const [requiresTwoFactor, setRequiresTwoFactor] = useState(false);
  const me = useQuery({ queryKey: ['me'], queryFn: getMe, retry: false, staleTime: 5 * 60_000 });
  const loginMutation = useMutation({ mutationFn: loginVrchat });
  const twoFactorMutation = useMutation({ mutationFn: verifyVrchat2fa });

  useEffect(() => {
    const requireAuthentication = () => {
      setRequiresTwoFactor(false);
      queryClient.removeQueries({
        predicate: (query) => query.queryKey[0] !== 'me',
      });
      void queryClient.invalidateQueries({ queryKey: ['me'], exact: true });
    };
    window.addEventListener(AUTH_REQUIRED_EVENT, requireAuthentication);
    return () => window.removeEventListener(AUTH_REQUIRED_EVENT, requireAuthentication);
  }, [queryClient]);

  if (me.isPending) return <LoadingScreen />;

  if (me.isError) {
    if (me.error instanceof ApiError && me.error.status === 401) {
      return (
        <LoginScreen
          pending={loginMutation.isPending || twoFactorMutation.isPending}
          error={requiresTwoFactor ? twoFactorMutation.error : loginMutation.error}
          requiresTwoFactor={requiresTwoFactor}
          onEdit={() => {
            loginMutation.reset();
            twoFactorMutation.reset();
          }}
          onBack={() => {
            loginMutation.reset();
            twoFactorMutation.reset();
            setRequiresTwoFactor(false);
          }}
          onLogin={async (credentials) => {
            try {
              const result = await loginMutation.mutateAsync(credentials);
              if (result.requires_2fa) {
                setRequiresTwoFactor(true);
                return;
              }
              await queryClient.invalidateQueries({ queryKey: ['me'] });
            } catch {
              // The mutation exposes a single inline error through LoginScreen.
            }
          }}
          onVerify={async (code) => {
            try {
              await twoFactorMutation.mutateAsync(code);
              await queryClient.invalidateQueries({ queryKey: ['me'] });
            } catch {
              // The mutation exposes a single inline error through LoginScreen.
            }
          }}
        />
      );
    }
    return (
      <OfflineScreen
        message={me.error instanceof ApiError ? me.error.message : '暂时无法连接服务'}
        onRetry={() => void me.refetch()}
      />
    );
  }

  return <Dashboard identity={me.data.user} />;
}
