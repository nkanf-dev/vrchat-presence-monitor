import { useQuery } from '@tanstack/react-query';
import { CalendarRange, RefreshCw, RotateCcw } from 'lucide-react';

import { isDateKey, offsetDateKey, todayKey } from '../analytics';
import { ApiError, getPresenceAnalytics } from '../api';
import { DateNavigator } from '../components/DateNavigator';
import { DailyTimelineChart, PresenceHeatmap } from '../components/PresenceCharts';
import { useHashParameters } from '../navigation';

function AnalyticsError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="inline-error analytics-error" role="alert">
      <strong>分析数据暂时没有加载出来</strong>
      <span>{message}</span>
      <button type="button" className="button button-secondary" onClick={onRetry}>
        <RefreshCw size={17} aria-hidden="true" />
        重新加载
      </button>
    </div>
  );
}

export function DailyView() {
  const { parameters, update } = useHashParameters();
  const today = todayKey();
  const requestedDay = parameters.get('day');
  const requestedFrom = parameters.get('from');
  const requestedTo = parameters.get('to');
  const day = isDateKey(requestedDay) ? requestedDay : today;
  const fallbackFrom = offsetDateKey(today, -29);
  const heatmapFrom = isDateKey(requestedFrom) ? requestedFrom : fallbackFrom;
  const heatmapTo = isDateKey(requestedTo) ? requestedTo : today;
  const result = useQuery({
    queryKey: ['analytics', 'presence', day, heatmapFrom, heatmapTo],
    queryFn: () => getPresenceAnalytics({ day, heatmapFrom, heatmapTo }),
    staleTime: day < today && heatmapTo < today ? Infinity : 30_000,
    refetchInterval: day === today || heatmapTo === today ? 60_000 : false,
  });

  const setDay = (value: string) => update({ day: value || today });
  const resetHeatmap = () => update({ from: fallbackFrom, to: today });

  return (
    <>
      <header className="page-heading analytics-heading">
        <div>
          <p className="kicker">Daily presence</p>
          <h1 tabIndex={-1}>每日在线</h1>
          <p>查看每位玩家当天的真实在线区间，以及按独立日期范围计算的每小时在线比例。</p>
        </div>
        <DateNavigator value={day} onChange={setDay} label="选择每日在线日期" />
      </header>

      <section className="panel analytics-panel" id="daily-timeline" aria-labelledby="daily-timeline-title">
        <header className="panel-heading analytics-panel-heading">
          <div>
            <p className="kicker">Selected day</p>
            <h2 id="daily-timeline-title">{result.data?.day ?? day} 在线时间带</h2>
          </div>
          <span className="analysis-note">
            {result.data ? `${result.data.timezone} · ${result.data.timeline.length} 位追踪对象` : '正在读取时间轴'}
          </span>
        </header>
        {result.isPending ? (
          <div className="chart-skeleton" role="status">正在生成每日时间轴…</div>
        ) : result.isError ? (
          <AnalyticsError
            message={result.error instanceof ApiError ? result.error.message : '请稍后重试'}
            onRetry={() => void result.refetch()}
          />
        ) : result.data.timeline.length ? (
          <DailyTimelineChart rows={result.data.timeline} />
        ) : (
          <div className="empty-state roomy">
            <CalendarRange size={28} aria-hidden="true" />
            <strong>当天还没有在线记录</strong>
            <p>可以切换日期查看已保存的历史区间。</p>
          </div>
        )}
      </section>

      <section className="panel analytics-panel" id="friend-hour-heatmap" aria-labelledby="heatmap-title">
        <header className="panel-heading analytics-panel-heading heatmap-heading">
          <div>
            <p className="kicker">Average by friend / hour</p>
            <h2 id="heatmap-title">每好友每小时在线热力图</h2>
          </div>
          <div className="range-controls" aria-label="热力图日期范围">
            <label>
              <span>从</span>
              <input
                type="date"
                value={heatmapFrom}
                max={heatmapTo}
                onChange={(event) => update({ from: event.target.value, to: event.target.value > heatmapTo ? event.target.value : heatmapTo })}
              />
            </label>
            <span aria-hidden="true">—</span>
            <label>
              <span>至</span>
              <input
                type="date"
                value={heatmapTo}
                min={heatmapFrom}
                max={today}
                onChange={(event) => update({ to: event.target.value, from: event.target.value < heatmapFrom ? event.target.value : heatmapFrom })}
              />
            </label>
            <button type="button" className="button button-secondary button-compact" onClick={resetHeatmap}>
              <RotateCcw size={16} aria-hidden="true" />
              近 30 天
            </button>
          </div>
        </header>
        <div className="heatmap-context">
          <span>颜色越亮，代表该玩家在这一小时更常在线。</span>
          <strong>
            {result.data
              ? `${result.data.heatmap_from} 至 ${result.data.heatmap_to} · 含今日实时数据 · ${result.data.heatmap_complete_days} 个完整日`
              : `${heatmapFrom} 至 ${heatmapTo}`}
          </strong>
        </div>
        {result.isPending ? (
          <div className="chart-skeleton" role="status">正在计算热力图…</div>
        ) : result.isError ? (
          <AnalyticsError
            message={result.error instanceof ApiError ? result.error.message : '请稍后重试'}
            onRetry={() => void result.refetch()}
          />
        ) : result.data.heatmap.length ? (
          <PresenceHeatmap rows={result.data.heatmap} observedMinutes={result.data.heatmap_observed_minutes} />
        ) : (
          <div className="empty-state roomy">
            <CalendarRange size={28} aria-hidden="true" />
            <strong>这个范围还没有观测数据</strong>
            <p>未来尚未到达的小时不会被当作离线记录。</p>
          </div>
        )}
      </section>
    </>
  );
}
