import { AlertTriangle, CheckCircle2, Clock3, RefreshCw } from 'lucide-react';

import type { Overview } from '../api';
import { formatDateTime } from '../format';

export function StatusBanner({
  overview,
  refreshFailed,
  onRetry,
  onReconnect,
}: {
  overview: Overview | undefined;
  refreshFailed: boolean;
  onRetry: () => void;
  onReconnect?: () => void;
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
    const reconnect = vrchatReconnectRequired(overview);
    return (
      <section className="status-banner status-warning" aria-live="polite">
        <AlertTriangle size={20} aria-hidden="true" />
        <div>
          <strong>{reconnect ? 'VRChat 需要重新连接' : '云端采集暂时中断'}</strong>
          <span>{reconnect ? '你的面板与历史数据仍可正常查看，重新连接后会继续采集。' : '已有记录仍可查看；连接恢复后会自动继续写入。'}</span>
        </div>
        {reconnect && onReconnect && (
          <button className="button button-secondary button-compact" onClick={onReconnect}>
            <RefreshCw size={16} aria-hidden="true" /> 重新连接 VRChat
          </button>
        )}
      </section>
    );
  }
  if (overview?.collector_state === 'stale') {
    return (
      <section className="status-banner status-stale" aria-live="polite">
        <AlertTriangle size={20} aria-hidden="true" />
        <div>
          <strong>采集数据已经过期</strong>
          <span>最近一次采集：{formatDateTime(overview.last_sync)}。云端连接恢复后会自动继续。</span>
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
          <span>云端正在准备第一份好友状态与活动历史。</span>
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

export const vrchatReconnectRequired = (overview: Overview | undefined) => {
  const message = overview?.collector_error?.toLowerCase() ?? '';
  return /(login|auth|unauthor|credential|cookie|session|401|登录|认证|验证|会话).*(失效|过期|需要|invalid|expired|required|unauthor)|(?:需要|请).*(?:登录|认证|验证)/i.test(message);
};
