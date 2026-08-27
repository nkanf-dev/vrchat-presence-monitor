import { useQueries, useQuery } from '@tanstack/react-query';
import { Filter, Map as MapIcon, RefreshCw, UserRound } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';

import {
  createWorldColors,
  isDateKey,
  isSpecialWorld,
  normalizeWorldAnalytics,
  specialWorldInfo,
  todayKey,
  worldName,
} from '../analytics';
import { ApiError, getWorld, getWorldAnalytics, worldImageUrl } from '../api';
import type { WorldInfo } from '../api';
import { DateNavigator } from '../components/DateNavigator';
import { EveryoneWorldChart, PersonWorldChart } from '../components/WorldCharts';
import { WorldDialog } from '../components/WorldDialog';
import { useHashParameters } from '../navigation';

export function WorldsView() {
  const { parameters, update } = useHashParameters();
  const today = todayKey();
  const requestedDay = parameters.get('day');
  const day = isDateKey(requestedDay) ? requestedDay : today;
  const requestedPerson = parameters.get('person') ?? '';
  const requestedWorld = parameters.get('world') ?? '';
  const [openWorldId, setOpenWorldId] = useState<string | null>(null);
  const result = useQuery({
    queryKey: ['analytics', 'worlds', day],
    queryFn: async () => normalizeWorldAnalytics(await getWorldAnalytics(day)),
    staleTime: day < today ? Infinity : 30_000,
    refetchInterval: day === today ? 60_000 : false,
  });
  const worldIds = useMemo(() => result.data?.world_ids ?? [], [result.data?.world_ids]);
  const publicWorldIds = useMemo(() => worldIds.filter((id) => !isSpecialWorld(id)), [worldIds]);
  const worldQueries = useQueries({
    queries: publicWorldIds.map((worldId) => ({
      queryKey: ['world', worldId],
      queryFn: () => getWorld(worldId),
      staleTime: 60 * 60_000,
      retry: 1,
    })),
  });
  const worldInfo = useMemo(() => {
    const catalog = new Map<string, WorldInfo>();
    worldIds.forEach((worldId) => {
      const special = specialWorldInfo(worldId);
      if (special) catalog.set(worldId, special);
    });
    worldQueries.forEach((query, index) => {
      const worldId = publicWorldIds[index];
      if (query.data && worldId) catalog.set(worldId, query.data);
    });
    return catalog;
  }, [publicWorldIds, worldIds, worldQueries]);
  const colors = useMemo(() => createWorldColors(worldIds), [worldIds]);
  const people = result.data?.friends ?? [];
  const fallbackPerson = result.data?.self_id || people.find((person) => person.online_minutes > 0)?.id || people[0]?.id || '';
  const selectedPersonId = people.some((person) => person.id === requestedPerson) ? requestedPerson : fallbackPerson;
  const selectedPerson = people.find((person) => person.id === selectedPersonId);
  const worldFilter = worldIds.includes(requestedWorld) ? requestedWorld : '';

  useEffect(() => {
    if (!result.data) return;
    const patch: Record<string, string | null> = {};
    if (selectedPersonId && selectedPersonId !== requestedPerson) patch.person = selectedPersonId;
    if (requestedWorld && !worldFilter) patch.world = null;
    if (Object.keys(patch).length) update(patch, true);
  }, [requestedPerson, requestedWorld, result.data, selectedPersonId, update, worldFilter]);

  return (
    <>
      <header className="page-heading analytics-heading">
        <div>
          <p className="kicker">Presence × worlds</p>
          <h1 tabIndex={-1}>在线 × 世界</h1>
          <p>把在线区间和世界记录叠在同一条时间轴上；上方聚焦单人，下方横向对比所有玩家。</p>
        </div>
        <DateNavigator value={day} onChange={(value) => update({ day: value || today })} label="选择世界分析日期" />
      </header>

      {result.isPending ? (
        <section className="panel chart-skeleton world-page-loading" role="status">正在生成世界时间带…</section>
      ) : result.isError ? (
        <section className="panel inline-error analytics-error" role="alert">
          <MapIcon size={26} aria-hidden="true" />
          <strong>世界时间带暂时没有加载出来</strong>
          <span>{result.error instanceof ApiError ? result.error.message : '请稍后重试'}</span>
          <button type="button" className="button button-secondary" onClick={() => void result.refetch()}>
            <RefreshCw size={17} aria-hidden="true" />
            重新加载
          </button>
        </section>
      ) : (
        <>
          <section className="panel analytics-panel" id="person-world-timeline" aria-labelledby="person-world-title">
            <header className="panel-heading analytics-panel-heading">
              <div>
                <p className="kicker">Selected person</p>
                <h2 id="person-world-title">{result.data.day} 单人世界时间轴</h2>
              </div>
              <label className="select-control person-select">
                <UserRound size={17} aria-hidden="true" />
                <span className="sr-only">选择玩家</span>
                <select value={selectedPersonId} onChange={(event) => update({ person: event.target.value })}>
                  {people.map((person) => (
                    <option key={person.id} value={person.id}>
                      {person.name}{person.is_self ? '（自己）' : ''} · {Math.round(person.online_minutes)} 分钟
                    </option>
                  ))}
                </select>
              </label>
            </header>
            {selectedPerson ? (
              <PersonWorldChart person={selectedPerson} info={worldInfo} colors={colors} onOpenWorld={setOpenWorldId} />
            ) : (
              <div className="empty-state roomy">
                <MapIcon size={28} aria-hidden="true" />
                <strong>当天还没有玩家记录</strong>
                <p>切换到有在线记录的日期再查看。</p>
              </div>
            )}
            <WorldLegend worldIds={[...new Set(selectedPerson?.spans.map((span) => span.world_id) ?? [])]} info={worldInfo} colors={colors} onOpen={setOpenWorldId} />
          </section>

          <section className="panel analytics-panel" id="everyone-world-timeline" aria-labelledby="everyone-world-title">
            <header className="panel-heading analytics-panel-heading world-overview-heading">
              <div>
                <p className="kicker">All tracked people</p>
                <h2 id="everyone-world-title">所有玩家与自己的世界时间带</h2>
              </div>
              <label className="select-control world-filter">
                <Filter size={17} aria-hidden="true" />
                <span className="sr-only">筛选世界</span>
                <select value={worldFilter} onChange={(event) => update({ world: event.target.value || null })}>
                  <option value="">全部世界</option>
                  {worldIds.map((worldId) => <option key={worldId} value={worldId}>{worldName(worldId, worldInfo.get(worldId))}</option>)}
                </select>
              </label>
            </header>
            <div className="heatmap-context">
              <span>{result.data.timezone} · {people.length} 位追踪对象</span>
              <strong>{worldFilter ? `仅显示 ${worldName(worldFilter, worldInfo.get(worldFilter))}` : '点击条带或图例查看世界详情'}</strong>
            </div>
            <EveryoneWorldChart people={people} info={worldInfo} colors={colors} worldFilter={worldFilter} onOpenWorld={setOpenWorldId} />
            <WorldLegend worldIds={worldIds} info={worldInfo} colors={colors} onOpen={setOpenWorldId} />
          </section>
        </>
      )}

      <WorldDialog worldId={openWorldId} onClose={() => setOpenWorldId(null)} />
    </>
  );
}

function WorldLegend({
  worldIds,
  info,
  colors,
  onOpen,
}: {
  worldIds: string[];
  info: Map<string, WorldInfo>;
  colors: Map<string, string>;
  onOpen: (worldId: string) => void;
}) {
  if (!worldIds.length) return <p className="world-legend-empty">所选范围没有世界记录。</p>;
  return (
    <div className="world-legend" aria-label="世界颜色图例">
      {[...new Set(worldIds)].map((worldId) => {
        const details = info.get(worldId);
        const image = details?.thumbnail_url || details?.image_url || '';
        return (
          <button key={worldId} type="button" className="world-legend-card" onClick={() => onOpen(worldId)}>
            {image ? <img src={worldImageUrl(image)} alt="" /> : <span className="world-legend-placeholder"><MapIcon size={20} /></span>}
            <i style={{ background: colors.get(worldId) ?? '#687381' }} aria-hidden="true" />
            <span>
              <strong>{worldName(worldId, details)}</strong>
              <small>{details?.author_name ? `作者：${details.author_name}` : worldId}</small>
            </span>
          </button>
        );
      })}
    </div>
  );
}
