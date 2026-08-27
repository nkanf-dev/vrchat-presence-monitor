import { AlertTriangle, CheckCircle2, Clock3, RefreshCw } from 'lucide-react';

import type { Overview } from '../api';
import { formatDateTime } from '../format';

export function StatusBanner({
  overview,
  refreshFailed,
  onRetry,
}: {
  overview: Overview | undefined;
  refreshFailed: boolean;
  onRetry: () => void;
}) {
  if (refreshFailed) {
    return (
      <section className="status-banner status-stale" aria-live="polite">
        <AlertTriangle size={20} aria-hidden="true" />
        <div>
          <strong>{overview ? '刷新失败，正在显示上次状态摘要' : '状态摘要暂时无法加载'}</strong>
          <span>
            {overview?.last_sync ? `最后成功同步：${formatDateTime(overview.last_sync)}` : '当前页面仍可使用，恢复后会自动刷新。'}
          </span>
        </div>
        <button className="button button-secondary button-compact" onClick={onRetry}>
          <RefreshCw size={16} aria-hidden="true" /> 重试
        </button>
      </section>
    );
  }
  if (overview?.collector_state === 'error') {
    return (
      <section className="status-banner status-warning" aria-live="polite">
        <AlertTriangle size={20} aria-hidden="true" />
        <div>
          <strong>采集暂时中断</strong>
          <span>已有记录仍可查看；采集器恢复后会继续写入，不会用错误消息刷屏。</span>
        </div>
      </section>
    );
  }
  if (overview?.collector_state === 'stale') {
    return (
      <section className="status-banner status-stale" aria-live="polite">
        <AlertTriangle size={20} aria-hidden="true" />
        <div>
          <strong>采集数据已经过期</strong>
          <span>最近一次采集：{formatDateTime(overview.last_sync)}。请检查 bridge 是否仍在运行。</span>
        </div>
        <button className="button button-secondary button-compact" onClick={onRetry}>
          <RefreshCw size={16} aria-hidden="true" /> 重试
        </button>
      </section>
    );
  }
  if (!overview?.last_sync) {
    return (
      <section className="status-banner status-pending" aria-live="polite">
        <Clock3 size={20} aria-hidden="true" />
        <div>
          <strong>等待第一份数据</strong>
          <span>连接本地采集 bridge 后，这里会出现好友状态和活动历史。</span>
        </div>
      </section>
    );
  }
  return (
    <section className="status-banner status-connected" aria-live="polite">
      <CheckCircle2 size={20} aria-hidden="true" />
      <div>
        <strong>数据已更新</strong>
        <span>最近一次采集：{formatDateTime(overview.last_sync)}</span>
      </div>
    </section>
  );
}
