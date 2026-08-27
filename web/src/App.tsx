import { useIsFetching, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { RefreshCw } from 'lucide-react';
import { useEffect, useState } from 'react';

import {
  ApiError,
  AUTH_REQUIRED_EVENT,
  type Friend,
  getEvents,
  getFriends,
  getMe,
  getOverview,
  login,
  logout,
} from './api';
import { AppShell } from './components/AppShell';
import { FriendDialog } from './components/FriendDialog';
import { LoadingScreen, LoginScreen, OfflineScreen } from './components/AuthScreens';
import { StatusBanner } from './components/StatusBanner';
import { useHashView } from './navigation';
import { DataView } from './views/DataView';
import { HistoryView } from './views/HistoryView';
import { OverviewView } from './views/OverviewView';
import { PeopleView } from './views/PeopleView';

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

function Dashboard({ identity }: { identity: { tenant_id: string; name: string } }) {
  const queryClient = useQueryClient();
  const { view, navigate } = useHashView();
  const [selectedFriend, setSelectedFriend] = useState<Friend | null>(null);
  const [loggingOut, setLoggingOut] = useState(false);
  const overviewQuery = useQuery({
    queryKey: ['overview'],
    queryFn: getOverview,
    refetchInterval: 60_000,
  });
  const peopleQuery = useQuery({
    queryKey: ['friends', 'overview'],
    queryFn: () => getFriends({ limit: 8, offset: 0 }),
    refetchInterval: 60_000,
    enabled: view === 'overview',
  });
  const eventsQuery = useQuery({
    queryKey: ['events', 'overview'],
    queryFn: () => getEvents({ limit: 8, offset: 0 }),
    refetchInterval: 60_000,
    enabled: view === 'overview',
  });
  const activeFetches = useIsFetching();

  const errors = [overviewQuery.error, peopleQuery.error, eventsQuery.error];
  const unauthorized = errors.some((error) => error instanceof ApiError && error.status === 401);
  useEffect(() => {
    if (unauthorized) void queryClient.invalidateQueries({ queryKey: ['me'] });
  }, [queryClient, unauthorized]);

  useEffect(() => {
    if (view === 'overview' && overviewQuery.isPending) return;
    const frame = window.requestAnimationFrame(() => {
      const title = document.querySelector<HTMLElement>('#main-content h1');
      title?.focus({ preventScroll: true });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [overviewQuery.isPending, view]);

  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['overview'] }),
      queryClient.invalidateQueries({ queryKey: ['friends'] }),
      queryClient.invalidateQueries({ queryKey: ['events'] }),
      queryClient.invalidateQueries({ queryKey: ['capabilities'] }),
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
    } catch {
      setLoggingOut(false);
      await refresh();
    }
  };

  const refreshing = activeFetches > 0;
  // Preview failures stay inside their own panels. Only the overview request
  // controls the global connection state shown by the shell and banner.
  const refreshFailed = overviewQuery.isError;
  const viewLabel = { overview: '状态总览', people: '玩家列表', history: '状态历史', data: '数据与备份' }[view];

  return (
    <AppShell
      identity={identity}
      overview={overviewQuery.data}
      refreshFailed={refreshFailed}
      view={view}
      refreshing={refreshing}
      onNavigate={navigate}
      onRefresh={() => void refresh()}
      onLogout={() => void signOut()}
    >
      <span className="sr-only" role="status" aria-live="polite">已进入{viewLabel}</span>
      {!overviewQuery.isPending &&
        (overviewQuery.data || (overviewQuery.isError && view !== 'overview')) && (
        <StatusBanner overview={overviewQuery.data} refreshFailed={refreshFailed} onRetry={() => void refresh()} />
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
          onOpenFriend={setSelectedFriend}
          onNavigatePeople={() => navigate('people')}
          onNavigateHistory={() => navigate('history')}
        />
      ) : null}
      {view === 'people' && <PeopleView onOpenFriend={setSelectedFriend} />}
      {view === 'history' && <HistoryView />}
      {view === 'data' && <DataView />}
      <FriendDialog friend={selectedFriend} onClose={() => setSelectedFriend(null)} />
    </AppShell>
  );
}

export function App() {
  const queryClient = useQueryClient();
  const me = useQuery({ queryKey: ['me'], queryFn: getMe, retry: false, staleTime: 5 * 60_000 });
  const loginMutation = useMutation({ mutationFn: login });

  useEffect(() => {
    const requireAuthentication = () => {
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
          pending={loginMutation.isPending}
          error={loginMutation.error}
          onLogin={async (code) => {
            try {
              await loginMutation.mutateAsync(code);
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
