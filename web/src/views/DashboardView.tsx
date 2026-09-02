import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  BarChart3,
  ChevronDown,
  ChevronUp,
  Check,
  Copy,
  Donut,
  GripVertical,
  LayoutDashboard,
  LineChart,
  Pencil,
  Plus,
  RefreshCw,
  Save,
  Share2,
  SlidersHorizontal,
  Trash2,
  UsersRound,
  X,
} from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import ReactGridLayout, { type Layout, useContainerWidth, verticalCompactor } from 'react-grid-layout';
import 'react-grid-layout/css/styles.css';
import 'react-resizable/css/styles.css';

import {
  ApiError,
  type Dashboard,
  type DashboardDocument,
  type DashboardPanel as DashboardPanelModel,
  type DashboardPanelKind,
  dashboardDocumentSchema,
  getDashboard,
  getFriends,
  updateDashboard,
} from '../api';
import { DashboardPanel } from '../components/DashboardPanel';
import { DashboardShareDialog } from '../components/DashboardShareDialog';
import { formatDateTime, statusLabel } from '../format';

type HashPatch = Record<string, string | number | null>;
type CatalogItem = {
  kind: DashboardPanelKind;
  title: string;
  description: string;
  size: Pick<DashboardPanelModel, 'w' | 'h'>;
  icon: typeof BarChart3;
};

const catalog: CatalogItem[] = [
  { kind: 'online-now', title: '当前在线', description: '此刻在线的追踪对象数量', size: { w: 3, h: 4 }, icon: UsersRound },
  { kind: 'tracked-count', title: '追踪人数', description: '好友与自己的账号总数', size: { w: 3, h: 4 }, icon: LayoutDashboard },
  { kind: 'status-breakdown', title: '当前状态', description: '当前各状态的人数分布', size: { w: 6, h: 7 }, icon: Donut },
  { kind: 'online-ranking', title: '在线时长排行', description: '所选范围内的在线时长排名', size: { w: 6, h: 8 }, icon: BarChart3 },
  { kind: 'daily-changes', title: '每日状态变化', description: '每天记录到的状态变化趋势', size: { w: 6, h: 6 }, icon: LineChart },
  { kind: 'friend-heatmap', title: '好友时段热力', description: '每位好友在一天各时段的在线比例', size: { w: 12, h: 9 }, icon: SlidersHorizontal },
  { kind: 'world-ranking', title: '热门世界', description: '好友游玩时间最多的世界', size: { w: 8, h: 8 }, icon: BarChart3 },
  { kind: 'platform-breakdown', title: '平台分布', description: '当前设备平台的人数分布', size: { w: 6, h: 7 }, icon: Donut },
  { kind: 'collection-coverage', title: '数据覆盖率', description: '所选时段内实际采集覆盖情况', size: { w: 4, h: 5 }, icon: LineChart },
];

const catalogByKind = new Map(catalog.map((item) => [item.kind, item]));
const rangeOptions = [1, 7, 30, 90] as const;
const refreshOptions = [0, 30, 60, 300] as const;
const DASHBOARD_DRAFT_KEY = 'presence-monitor:dashboard-draft:v1';

const cloneDocument = (document: DashboardDocument): DashboardDocument =>
  structuredClone(document);

const validRange = (value: string | null): DashboardDocument['range_days'] | null => {
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed >= 1 && parsed <= 730 ? parsed : null;
};

const samplePanels = (): DashboardPanelModel[] => [
  { id: 'online-now', kind: 'online-now', title: '当前在线', x: 0, y: 0, w: 3, h: 4, range_days: 0, limit: 10, include_self: true, friend_ids: [], statuses: [], platforms: [], world_ids: [], world_tag: '', world_sort: 'people' },
  { id: 'tracked-count', kind: 'tracked-count', title: '追踪人数', x: 3, y: 0, w: 3, h: 4, range_days: 0, limit: 10, include_self: true, friend_ids: [], statuses: [], platforms: [], world_ids: [], world_tag: '', world_sort: 'people' },
  { id: 'status-breakdown', kind: 'status-breakdown', title: '当前状态', x: 6, y: 0, w: 6, h: 7, range_days: 0, limit: 10, include_self: true, friend_ids: [], statuses: [], platforms: [], world_ids: [], world_tag: '', world_sort: 'people' },
  { id: 'online-ranking', kind: 'online-ranking', title: '在线时长排行', x: 0, y: 4, w: 6, h: 8, range_days: 0, limit: 10, include_self: true, friend_ids: [], statuses: [], platforms: [], world_ids: [], world_tag: '', world_sort: 'people' },
  { id: 'daily-changes', kind: 'daily-changes', title: '每日状态变化', x: 6, y: 7, w: 6, h: 5, range_days: 0, limit: 10, include_self: true, friend_ids: [], statuses: [], platforms: [], world_ids: [], world_tag: '', world_sort: 'people' },
  { id: 'friend-heatmap', kind: 'friend-heatmap', title: '好友时段热力', x: 0, y: 12, w: 12, h: 9, range_days: 0, limit: 12, include_self: true, friend_ids: [], statuses: [], platforms: [], world_ids: [], world_tag: '', world_sort: 'people' },
];

const validRefresh = (value: string | null): DashboardDocument['refresh_seconds'] | null => {
  if (value === null) return null;
  const parsed = Number(value);
  return parsed === 0 || parsed === 30 || parsed === 60 || parsed === 300 ? parsed : null;
};

const createPanelId = () => `panel_${crypto.randomUUID().replaceAll('-', '').slice(0, 16)}`;

const conflictDashboard = (error: unknown): Dashboard | null => {
  if (!(error instanceof ApiError) || error.status !== 409 || !error.details || typeof error.details !== 'object') return null;
  const server = 'server' in error.details ? error.details.server : null;
  if (!server || typeof server !== 'object' || !('document' in server)) return null;
  return server as Dashboard;
};

function WorkspaceDialog({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  return (
    <div className="dashboard-dialog-backdrop" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget) onClose();
    }}>
      <section className="dashboard-dialog" role="dialog" aria-modal="true" aria-labelledby="dashboard-dialog-title">
        <header>
          <h2 id="dashboard-dialog-title">{title}</h2>
          <button type="button" className="icon-button" onClick={onClose} aria-label="关闭">
            <X size={19} aria-hidden="true" />
          </button>
        </header>
        {children}
      </section>
    </div>
  );
}

export function DashboardView({
  parameters,
  onUpdateParameters,
}: {
  parameters: URLSearchParams;
  onUpdateParameters: (values: HashPatch, replace?: boolean) => void;
}) {
  const queryClient = useQueryClient();
  const { width, containerRef, mounted } = useContainerWidth();
  const mobile = mounted && width < 720;
  const config = useQuery({
    queryKey: ['dashboard-config'],
    queryFn: getDashboard,
    staleTime: 30_000,
  });
  const [document, setDocument] = useState<DashboardDocument | null>(null);
  const [revision, setRevision] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [editing, setEditing] = useState(false);
  const [customRangeOpen, setCustomRangeOpen] = useState(false);
  const [libraryOpen, setLibraryOpen] = useState(false);
  const [shareOpen, setShareOpen] = useState(false);
  const [editingPanelId, setEditingPanelId] = useState<string | null>(null);
  const [filterSearch, setFilterSearch] = useState('');
  const [conflict, setConflict] = useState<Dashboard | null>(null);
  const [recoveredDraft, setRecoveredDraft] = useState(false);
  const loadedRevision = useRef<string | null | undefined>(undefined);
  const filterFriends = useQuery({
    queryKey: ['friends', 'dashboard-filter'],
    queryFn: () => getFriends({ limit: 200, offset: 0 }),
    enabled: editingPanelId !== null,
    staleTime: 60_000,
  });

  useEffect(() => {
    if (!config.data || loadedRevision.current === config.data.revision || dirty) return;
    let next = cloneDocument(config.data.document);
    try {
      const stored = JSON.parse(window.sessionStorage.getItem(DASHBOARD_DRAFT_KEY) ?? 'null') as unknown;
      if (stored && typeof stored === 'object' && 'revision' in stored && 'document' in stored) {
        const candidate = stored as { revision: unknown; document: unknown };
        const parsed = dashboardDocumentSchema.safeParse(candidate.document);
        if (candidate.revision === config.data.revision && parsed.success) {
          next = cloneDocument(parsed.data);
          setDirty(true);
          setRecoveredDraft(true);
        }
      }
    } catch {
      window.sessionStorage.removeItem(DASHBOARD_DRAFT_KEY);
    }
    next.range_days = validRange(parameters.get('dashRange')) ?? next.range_days;
    next.refresh_seconds = validRefresh(parameters.get('dashRefresh')) ?? next.refresh_seconds;
    if (!next.panels.length) {
      next.panels = samplePanels();
      setDirty(true);
    }
    setDocument(next);
    setRevision(config.data.revision);
    loadedRevision.current = config.data.revision;
  }, [config.data, dirty, parameters]);

  useEffect(() => {
    if (!document) return;
    try {
      if (dirty) window.sessionStorage.setItem(DASHBOARD_DRAFT_KEY, JSON.stringify({ revision, document }));
      else window.sessionStorage.removeItem(DASHBOARD_DRAFT_KEY);
    } catch {
      // The dashboard still works when browser storage is unavailable.
    }
  }, [dirty, document, revision]);

  useEffect(() => {
    if (!dirty) return;
    const guard = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener('beforeunload', guard);
    return () => window.removeEventListener('beforeunload', guard);
  }, [dirty]);

  useEffect(() => {
    if (!document?.refresh_seconds) return;
    const timer = window.setInterval(() => {
      void queryClient.invalidateQueries({ queryKey: ['dashboard-data'] });
    }, document.refresh_seconds * 1000);
    return () => window.clearInterval(timer);
  }, [document?.refresh_seconds, queryClient]);

  const save = useMutation({
    mutationFn: (value: Pick<Dashboard, 'revision' | 'document'>) => updateDashboard(value),
    onSuccess: (saved) => {
      setDocument(cloneDocument(saved.document));
      setRevision(saved.revision);
      loadedRevision.current = saved.revision;
      setDirty(false);
      setRecoveredDraft(false);
      setConflict(null);
      queryClient.setQueryData(['dashboard-config'], saved);
    },
    onError: (error) => setConflict(conflictDashboard(error)),
  });

  const changeDocument = (change: (current: DashboardDocument) => DashboardDocument) => {
    setDocument((current) => current ? change(cloneDocument(current)) : current);
    setDirty(true);
  };

  const updateRange = (range: DashboardDocument['range_days']) => {
    changeDocument((current) => ({ ...current, range_days: range }));
    onUpdateParameters({ dashRange: range }, true);
  };

  const updateRefresh = (seconds: DashboardDocument['refresh_seconds']) => {
    changeDocument((current) => ({ ...current, refresh_seconds: seconds }));
    onUpdateParameters({ dashRefresh: seconds }, true);
  };

  const orderedPanels = useMemo(
    () => [...(document?.panels ?? [])].sort((a, b) => a.y - b.y || a.x - b.x),
    [document?.panels],
  );

  const layout = useMemo<Layout>(() => {
    if (!mobile) return orderedPanels.map((panel) => ({
      i: panel.id,
      x: panel.x,
      y: panel.y,
      w: panel.w,
      h: panel.h,
      minW: panel.kind === 'online-now' || panel.kind === 'tracked-count' ? 2 : 4,
      minH: 3,
      maxW: 12,
      maxH: 20,
      static: !editing,
    }));
    let y = 0;
    return orderedPanels.map((panel) => {
      const item = { i: panel.id, x: 0, y, w: 1, h: panel.h, minW: 1, minH: 3, static: true };
      y += panel.h;
      return item;
    });
  }, [editing, mobile, orderedPanels]);

  const applyLayout = (next: Layout) => {
    if (!editing || mobile) return;
    const byId = new Map(next.map((item) => [item.i, item]));
    const changed = document?.panels.some((panel) => {
      const item = byId.get(panel.id);
      return item && (panel.x !== item.x || panel.y !== item.y || panel.w !== item.w || panel.h !== item.h);
    });
    if (!changed) return;
    changeDocument((current) => ({
      ...current,
      panels: current.panels.map((panel) => {
        const item = byId.get(panel.id);
        return item ? { ...panel, x: item.x, y: item.y, w: item.w, h: item.h } : panel;
      }),
    }));
  };

  const addPanel = (item: CatalogItem) => {
    changeDocument((current) => {
      const bottom = current.panels.reduce((value, panel) => Math.max(value, panel.y + panel.h), 0);
      return {
        ...current,
        panels: [...current.panels, {
          id: createPanelId(),
          kind: item.kind,
          title: item.title,
          x: 0,
          y: bottom,
          w: item.size.w,
          h: item.size.h,
          range_days: item.kind === 'world-ranking' ? 30 : 0,
          limit: 10,
          include_self: true,
          friend_ids: [],
          statuses: [],
          platforms: [],
          world_ids: [],
          world_tag: '',
          world_sort: 'people',
        }],
      };
    });
    setLibraryOpen(false);
    setEditing(true);
  };

  const updatePanel = (panelId: string, patch: Partial<DashboardPanelModel>) => {
    changeDocument((current) => ({
      ...current,
      panels: current.panels.map((panel) => panel.id === panelId ? { ...panel, ...patch } : panel),
    }));
  };

  const duplicatePanel = (source: DashboardPanelModel) => {
    changeDocument((current) => ({
      ...current,
      panels: [...current.panels, {
        ...source,
        id: createPanelId(),
        title: `${source.title} 副本`.slice(0, 80),
        x: Math.min(12 - source.w, source.x + 1),
        y: source.y + 1,
      }],
    }));
  };

  const removePanel = (panelId: string) => {
    changeDocument((current) => ({
      ...current,
      panels: current.panels.filter((panel) => panel.id !== panelId),
    }));
    setEditingPanelId(null);
  };

  const movePanel = (panelId: string, delta: -1 | 1) => {
    changeDocument((current) => {
      const sorted = [...current.panels].sort((a, b) => a.y - b.y || a.x - b.x);
      const index = sorted.findIndex((panel) => panel.id === panelId);
      const target = index + delta;
      if (index < 0 || target < 0 || target >= sorted.length) return current;
      const sourcePanel = sorted[index];
      const targetPanel = sorted[target];
      if (!sourcePanel || !targetPanel) return current;
      return {
        ...current,
        panels: current.panels.map((panel) => {
          if (panel.id === sourcePanel.id) return { ...panel, x: targetPanel.x, y: targetPanel.y };
          if (panel.id === targetPanel.id) return { ...panel, x: sourcePanel.x, y: sourcePanel.y };
          return panel;
        }),
      };
    });
  };

  const editingPanel = document?.panels.find((panel) => panel.id === editingPanelId) ?? null;

  if (config.isError && !document) {
    return (
      <div className="inline-error" role="alert">
        <strong>仪表盘暂时没有加载出来</strong>
        <span>{config.error instanceof ApiError ? config.error.message : '请稍后重试'}</span>
        <button type="button" className="button button-secondary" onClick={() => void config.refetch()}>重试</button>
      </div>
    );
  }
  if (config.isPending || !document) {
    return <div className="dashboard-workspace-loading" role="status">正在打开仪表盘…</div>;
  }

  return (
    <>
      <header className="page-heading dashboard-page-heading">
        <div>
          <p className="kicker">Custom dashboard</p>
          {editing ? (
            <input
              className="dashboard-title-input"
              value={document.title}
              maxLength={80}
              aria-label="仪表盘名称"
              onChange={(event) => changeDocument((current) => ({ ...current, title: event.target.value || '我的仪表盘' }))}
            />
          ) : <h1 tabIndex={-1}>{document.title}</h1>}
          <p>把关心的在线、时段与世界数据组合成自己的观察面板。</p>
        </div>
        <div className="dashboard-save-state" aria-live="polite">
          {dirty ? '有未保存更改' : revision ? `已保存 · ${formatDateTime(config.data?.updated_at)}` : '使用默认布局'}
        </div>
      </header>

      <section className="dashboard-toolbar panel" aria-label="仪表盘工具栏">
        <div className="dashboard-range" role="group" aria-label="全局时间范围">
          {rangeOptions.map((range) => (
            <button key={range} type="button" className={!customRangeOpen && document.range_days === range ? 'active' : ''} onClick={() => { setCustomRangeOpen(false); updateRange(range); }}>
              {range === 1 ? '1 天' : `${range} 天`}
            </button>
          ))}
          <button type="button" className={customRangeOpen || !rangeOptions.includes(document.range_days as (typeof rangeOptions)[number]) ? 'active' : ''} onClick={() => setCustomRangeOpen(true)}>自定义</button>
        </div>
        {customRangeOpen && <label className="dashboard-custom-range"><input type="number" min={1} max={730} value={document.range_days} aria-label="自定义天数" onChange={(event) => updateRange(Math.max(1, Math.min(730, Number(event.target.value) || 1)))} /><span>天</span></label>}
        <label className="dashboard-refresh-select">
          <RefreshCw size={15} aria-hidden="true" />
          <span className="sr-only">自动刷新</span>
          <select value={document.refresh_seconds} onChange={(event) => updateRefresh(Number(event.target.value) as DashboardDocument['refresh_seconds'])}>
            {refreshOptions.map((seconds) => <option key={seconds} value={seconds}>{seconds === 0 ? '关闭自动刷新' : seconds < 60 ? `${seconds} 秒刷新` : `${seconds / 60} 分钟刷新`}</option>)}
          </select>
        </label>
        <button type="button" className="button button-secondary" onClick={() => void queryClient.invalidateQueries({ queryKey: ['dashboard-data'] })}>
          <RefreshCw size={16} aria-hidden="true" />刷新
        </button>
        <button type="button" className={editing ? 'button button-secondary is-active' : 'button button-secondary'} onClick={() => setEditing((value) => !value)}>
          {editing ? <Check size={16} aria-hidden="true" /> : <Pencil size={16} aria-hidden="true" />}
          {editing ? '完成编辑' : '编辑布局'}
        </button>
        <button type="button" className="button button-secondary" onClick={() => setLibraryOpen(true)} disabled={document.panels.length >= 20}>
          <Plus size={16} aria-hidden="true" />添加图表
        </button>
        <button type="button" className="button button-secondary" onClick={() => setShareOpen(true)}>
          <Share2 size={16} aria-hidden="true" />分享
        </button>
        <button type="button" className="button button-primary" disabled={!dirty || save.isPending} onClick={() => save.mutate({ revision, document })}>
          <Save size={16} aria-hidden="true" />{save.isPending ? '保存中…' : '保存'}
        </button>
      </section>

      {mobile && editing && <div className="dashboard-mobile-note" role="note">手机上可配置图表；拖拽与缩放请在较宽屏幕使用。</div>}
      {recoveredDraft && (
        <div className="dashboard-recovered-draft" role="status">
          <span>已恢复上次未保存的编辑</span>
          <button type="button" className="button button-secondary button-compact" onClick={() => {
            if (!config.data) return;
            setDocument(cloneDocument(config.data.document));
            setRevision(config.data.revision);
            setDirty(false);
            setRecoveredDraft(false);
          }}>放弃草稿</button>
        </div>
      )}
      {save.isError && !conflict && <div className="dashboard-save-error" role="alert">保存失败：{save.error instanceof ApiError ? save.error.message : '请稍后重试'}</div>}
      {conflict && (
        <div className="dashboard-conflict" role="alert">
          <div><strong>另一台设备更新了仪表盘</strong><span>当前草稿仍在这里，请选择保留哪一版。</span></div>
          <button type="button" className="button button-secondary" onClick={() => {
            setDocument(cloneDocument(conflict.document));
            setRevision(conflict.revision);
            loadedRevision.current = conflict.revision;
            setDirty(false);
            setConflict(null);
          }}>载入云端版本</button>
          <button type="button" className="button button-primary" onClick={() => save.mutate({ revision: conflict.revision, document })}>保存当前布局</button>
        </div>
      )}

      <div className={editing ? 'dashboard-grid-host is-editing' : 'dashboard-grid-host'} ref={containerRef}>
        {mounted && mobile ? (
          <div className="dashboard-mobile-stack">
            {orderedPanels.map((panel, index) => (
              <article className="dashboard-grid-panel panel" key={panel.id}>
                <header className="dashboard-panel-heading">
                  <div><h2>{panel.title || catalogByKind.get(panel.kind)?.title}</h2><span>{panel.range_days ? `${panel.range_days} 天` : `跟随全局 · ${document.range_days} 天`}</span></div>
                  {editing && <div className="dashboard-panel-actions">
                    <button type="button" onClick={() => movePanel(panel.id, -1)} disabled={index === 0} aria-label={`上移 ${panel.title}`}><ChevronUp size={16} aria-hidden="true" /></button>
                    <button type="button" onClick={() => movePanel(panel.id, 1)} disabled={index === orderedPanels.length - 1} aria-label={`下移 ${panel.title}`}><ChevronDown size={16} aria-hidden="true" /></button>
                    <button type="button" onClick={() => setEditingPanelId(panel.id)} aria-label={`配置 ${panel.title}`}><SlidersHorizontal size={16} aria-hidden="true" /></button>
                    <button type="button" onClick={() => duplicatePanel(panel)} disabled={document.panels.length >= 20} aria-label={`复制 ${panel.title}`}><Copy size={16} aria-hidden="true" /></button>
                    <button type="button" onClick={() => removePanel(panel.id)} aria-label={`删除 ${panel.title}`}><Trash2 size={16} aria-hidden="true" /></button>
                  </div>}
                </header>
                <div className="dashboard-panel-body"><DashboardPanel panel={panel} globalRangeDays={document.range_days} /></div>
              </article>
            ))}
          </div>
        ) : mounted ? (
          <ReactGridLayout
            width={width}
            layout={layout}
            gridConfig={{ cols: mobile ? 1 : 12, rowHeight: mobile ? 34 : 36, margin: mobile ? [0, 12] : [12, 12], containerPadding: [0, 0] }}
            dragConfig={{ enabled: editing && !mobile, bounded: true, handle: '.dashboard-panel-drag' }}
            resizeConfig={{ enabled: editing && !mobile, handles: ['se'] }}
            compactor={verticalCompactor}
            onDragStop={(next) => applyLayout(next)}
            onResizeStop={(next) => applyLayout(next)}
          >
            {orderedPanels.map((panel) => (
              <article className="dashboard-grid-panel panel" key={panel.id}>
                <header className="dashboard-panel-heading">
                  <div>
                    <h2>{panel.title || catalogByKind.get(panel.kind)?.title}</h2>
                    <span>{panel.range_days ? `${panel.range_days} 天` : `跟随全局 · ${document.range_days} 天`}</span>
                  </div>
                  {editing && (
                    <div className="dashboard-panel-actions">
                      {!mobile && <button type="button" className="dashboard-panel-drag" aria-label={`拖动 ${panel.title}`}><GripVertical size={17} aria-hidden="true" /></button>}
                      <button type="button" onClick={() => setEditingPanelId(panel.id)} aria-label={`配置 ${panel.title}`}><SlidersHorizontal size={16} aria-hidden="true" /></button>
                      <button type="button" onClick={() => duplicatePanel(panel)} disabled={document.panels.length >= 20} aria-label={`复制 ${panel.title}`}><Copy size={16} aria-hidden="true" /></button>
                      <button type="button" onClick={() => removePanel(panel.id)} aria-label={`删除 ${panel.title}`}><Trash2 size={16} aria-hidden="true" /></button>
                    </div>
                  )}
                </header>
                <div className="dashboard-panel-body">
                  <DashboardPanel panel={panel} globalRangeDays={document.range_days} />
                </div>
              </article>
            ))}
          </ReactGridLayout>
        ) : null}
      </div>

      {!document.panels.length && (
        <section className="panel dashboard-empty">
          <LayoutDashboard size={34} aria-hidden="true" />
          <strong>从第一张图表开始</strong>
          <p>添加一个指标、排行或热力图，建立自己的观察面板。</p>
          <button type="button" className="button button-primary" onClick={() => setLibraryOpen(true)}><Plus size={16} aria-hidden="true" />添加图表</button>
        </section>
      )}

      {libraryOpen && (
        <WorkspaceDialog title="添加图表" onClose={() => setLibraryOpen(false)}>
          <div className="dashboard-chart-library">
            {catalog.map((item) => {
              const Icon = item.icon;
              return (
                <button type="button" key={item.kind} onClick={() => addPanel(item)}>
                  <span><Icon size={20} aria-hidden="true" /></span>
                  <strong>{item.title}</strong>
                  <small>{item.description}</small>
                </button>
              );
            })}
          </div>
        </WorkspaceDialog>
      )}

      {editingPanel && (
        <WorkspaceDialog title="配置图表" onClose={() => setEditingPanelId(null)}>
          <form className="dashboard-panel-form" onSubmit={(event) => { event.preventDefault(); setEditingPanelId(null); }}>
            <label><span>标题</span><input value={editingPanel.title} maxLength={80} onChange={(event) => updatePanel(editingPanel.id, { title: event.target.value })} /></label>
            {!['online-now', 'tracked-count', 'status-breakdown'].includes(editingPanel.kind) && (
              <label><span>时间范围</span><select value={editingPanel.range_days} onChange={(event) => updatePanel(editingPanel.id, { range_days: Number(event.target.value) as DashboardPanelModel['range_days'] })}>
                <option value={0}>跟随全局</option>
                <option value={1}>1 天</option><option value={7}>7 天</option><option value={30}>30 天</option>
                {editingPanel.kind !== 'world-ranking' && <option value={90}>90 天</option>}
              </select></label>
            )}
            {['online-ranking', 'friend-heatmap', 'world-ranking'].includes(editingPanel.kind) && (
              <label><span>显示数量</span><input type="number" min={3} max={30} value={editingPanel.limit} onChange={(event) => updatePanel(editingPanel.id, { limit: Math.max(3, Math.min(30, Number(event.target.value))) })} /></label>
            )}
            {['online-now', 'tracked-count', 'status-breakdown', 'platform-breakdown', 'friend-heatmap', 'world-ranking'].includes(editingPanel.kind) && (
              <label className="dashboard-checkbox"><input type="checkbox" checked={editingPanel.include_self} onChange={(event) => updatePanel(editingPanel.id, { include_self: event.target.checked })} /><span>包含自己的账号</span></label>
            )}
            {editingPanel.kind !== 'daily-changes' && (
              <fieldset className="dashboard-filter-group">
                <legend>玩家</legend>
                <input
                  value={filterSearch}
                  placeholder="搜索玩家"
                  aria-label="搜索可筛选玩家"
                  onChange={(event) => setFilterSearch(event.target.value)}
                />
                <div className="dashboard-filter-options">
                  {(filterFriends.data?.items ?? [])
                    .filter((friend) => !filterSearch || `${friend.display_name} ${friend.username}`.toLowerCase().includes(filterSearch.toLowerCase()))
                    .map((friend) => {
                      const checked = editingPanel.friend_ids.includes(friend.id);
                      return <label key={friend.id} className="dashboard-checkbox">
                        <input
                          type="checkbox"
                          checked={checked}
                          disabled={!checked && editingPanel.kind !== 'world-ranking' && editingPanel.friend_ids.length >= 50}
                          onChange={(event) => updatePanel(editingPanel.id, {
                            friend_ids: event.target.checked
                              ? editingPanel.kind === 'world-ranking'
                                ? [friend.id]
                                : [...editingPanel.friend_ids, friend.id].slice(0, 50)
                              : editingPanel.friend_ids.filter((id) => id !== friend.id),
                          })}
                        />
                        <span>{friend.display_name || friend.username}{friend.is_self ? '（自己）' : ''}</span>
                      </label>;
                    })}
                </div>
                <small>{editingPanel.friend_ids.length ? `已选择 ${editingPanel.friend_ids.length} 位；不选择时使用全部玩家` : '当前使用全部玩家'}{editingPanel.kind === 'world-ranking' ? '；世界排行一次筛选一位玩家' : ''}</small>
              </fieldset>
            )}
            {['online-now', 'tracked-count', 'status-breakdown', 'platform-breakdown'].includes(editingPanel.kind) && (
              <fieldset className="dashboard-filter-group dashboard-filter-inline">
                <legend>状态</legend>
                {['active', 'join me', 'ask me', 'busy', 'offline'].map((status) => <label key={status} className="dashboard-checkbox">
                  <input type="checkbox" checked={editingPanel.statuses.includes(status)} onChange={(event) => updatePanel(editingPanel.id, {
                    statuses: event.target.checked ? [...editingPanel.statuses, status] : editingPanel.statuses.filter((value) => value !== status),
                  })} /><span>{statusLabel(status)}</span>
                </label>)}
              </fieldset>
            )}
            {['online-now', 'tracked-count', 'status-breakdown', 'platform-breakdown'].includes(editingPanel.kind) && (
              <fieldset className="dashboard-filter-group dashboard-filter-inline">
                <legend>平台</legend>
                {[...new Set((filterFriends.data?.items ?? []).map((friend) => friend.platform).filter(Boolean))].sort().map((platform) => <label key={platform} className="dashboard-checkbox">
                  <input type="checkbox" checked={editingPanel.platforms.includes(platform)} onChange={(event) => updatePanel(editingPanel.id, {
                    platforms: event.target.checked ? [...editingPanel.platforms, platform] : editingPanel.platforms.filter((value) => value !== platform),
                  })} /><span>{platform}</span>
                </label>)}
              </fieldset>
            )}
            {editingPanel.kind === 'world-ranking' && <>
              <label><span>排序方式</span><select value={editingPanel.world_sort} onChange={(event) => updatePanel(editingPanel.id, { world_sort: event.target.value as DashboardPanelModel['world_sort'] })}>
                <option value="people">游玩人数</option>
                <option value="minutes">游玩时长</option>
                <option value="visits">到访次数</option>
                <option value="recent">最近到访</option>
              </select></label>
              <label><span>世界标签</span><input value={editingPanel.world_tag} placeholder="例如 game" onChange={(event) => updatePanel(editingPanel.id, { world_tag: event.target.value })} /></label>
              <label><span>限定世界 ID（逗号分隔）</span><input value={editingPanel.world_ids.join(', ')} placeholder="wrld_..." onChange={(event) => updatePanel(editingPanel.id, {
                world_ids: event.target.value.split(',').map((value) => value.trim()).filter((value) => value.startsWith('wrld_')).slice(0, 50),
              })} /></label>
              {editingPanel.friend_ids.length > 1 && <small className="dashboard-form-note">世界排行一次聚合一位玩家；当前将使用全部玩家。保留一位即可查看其个人世界排行。</small>}
            </>}
            <div className="dashboard-dialog-actions">
              <button type="button" className="button button-danger" onClick={() => removePanel(editingPanel.id)}><Trash2 size={16} aria-hidden="true" />删除图表</button>
              <button type="submit" className="button button-primary"><Check size={16} aria-hidden="true" />完成</button>
            </div>
          </form>
        </WorkspaceDialog>
      )}
      {shareOpen && <DashboardShareDialog onClose={() => setShareOpen(false)} dashboardDirty={dirty} />}
    </>
  );
}
