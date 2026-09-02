import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, Clipboard, ExternalLink, RefreshCw, Share2, Trash2, X } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';

import {
  ApiError,
  type DashboardShareAppearance,
  getDashboardShare,
  getDashboardShareAudit,
  publishDashboardShare,
  revokeDashboardShare,
} from '../api';
import { formatDateTime } from '../format';

export function DashboardShareDialog({ onClose, dashboardDirty }: { onClose: () => void; dashboardDirty: boolean }) {
  const client = useQueryClient();
  const share = useQuery({ queryKey: ['dashboard-share'], queryFn: getDashboardShare });
  const audit = useQuery({
    queryKey: ['dashboard-share-audit'],
    queryFn: () => getDashboardShareAudit(30, 0),
    enabled: share.data?.enabled === true,
  });
  const [password, setPassword] = useState('');
  const [copied, setCopied] = useState(false);
  const [appearance, setAppearance] = useState<DashboardShareAppearance>({
    preset: 'midnight', heading: '', description: '', page_title: '', avatar_url: '', custom_css: '',
  });
  const [appearanceLoaded, setAppearanceLoaded] = useState(false);
  useEffect(() => {
    if (!share.data || appearanceLoaded) return;
    setAppearance(share.data.appearance);
    setAppearanceLoaded(true);
  }, [appearanceLoaded, share.data]);
  const updateAppearance = <Key extends keyof DashboardShareAppearance>(key: Key, value: DashboardShareAppearance[Key]) => {
    setAppearance((current) => ({ ...current, [key]: value }));
  };
  const publish = useMutation({
    mutationFn: () => publishDashboardShare(password, appearance),
    onSuccess: async () => {
      setPassword('');
      await Promise.all([
        client.invalidateQueries({ queryKey: ['dashboard-share'] }),
        client.invalidateQueries({ queryKey: ['dashboard-share-audit'] }),
      ]);
    },
  });
  const revoke = useMutation({
    mutationFn: revokeDashboardShare,
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: ['dashboard-share'] });
    },
  });
  const shareUrl = useMemo(() => share.data?.id ? `${window.location.origin}/s/${share.data.id}` : '', [share.data?.id]);
  const copy = async () => {
    if (!shareUrl) return;
    await navigator.clipboard.writeText(shareUrl);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1800);
  };
  const error = publish.error ?? revoke.error;

  return (
    <div className="dashboard-dialog-backdrop" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget) onClose();
    }}>
      <section className="dashboard-dialog dashboard-share-dialog" role="dialog" aria-modal="true" aria-labelledby="dashboard-share-title">
        <header>
          <div><h2 id="dashboard-share-title">分享仪表盘</h2><span>生成一个独立页面，数据会持续更新。</span></div>
          <button type="button" className="icon-button" onClick={onClose} aria-label="关闭"><X size={19} aria-hidden="true" /></button>
        </header>
        {share.isPending ? <div className="dashboard-panel-state">正在读取分享状态…</div> : <div className="dashboard-share-body">
          {dashboardDirty && <div className="dashboard-share-notice">当前有未保存更改。分享会发布最近一次保存的布局。</div>}
          {share.data?.enabled && <div className="dashboard-share-current">
            <div><strong>{share.data.title}</strong><span>{share.data.protected ? '已设置密码' : '无需密码'} · {share.data.access_total ?? 0} 次访问</span></div>
            <div className="dashboard-share-link"><input readOnly value={shareUrl} aria-label="分享链接" /><button type="button" className="button button-secondary" onClick={() => void copy()}>{copied ? <Check size={16} /> : <Clipboard size={16} />}{copied ? '已复制' : '复制'}</button></div>
            <a className="button button-secondary" href={shareUrl} target="_blank" rel="noreferrer"><ExternalLink size={16} />打开分享页</a>
          </div>}
          <fieldset className="dashboard-share-appearance">
            <legend>页面外观</legend>
            <label><span>风格</span><select value={appearance.preset} onChange={(event) => updateAppearance('preset', event.target.value as DashboardShareAppearance['preset'])}>
              <option value="midnight">深夜</option><option value="aurora">极光</option><option value="paper">纸白</option><option value="sunset">日落</option>
            </select></label>
            <label><span>页面标题</span><input value={appearance.heading} maxLength={120} placeholder="默认使用仪表盘名称" onChange={(event) => updateAppearance('heading', event.target.value)} /></label>
            <label className="dashboard-share-wide"><span>说明文字</span><textarea value={appearance.description} maxLength={500} rows={3} placeholder="给访问者的一段说明（可选）" onChange={(event) => updateAppearance('description', event.target.value)} /></label>
            <label><span>浏览器标题</span><input value={appearance.page_title} maxLength={120} placeholder="默认使用页面标题" onChange={(event) => updateAppearance('page_title', event.target.value)} /></label>
            <label><span>页面头像 / 图标</span><input type="url" value={appearance.avatar_url} maxLength={2048} placeholder="https://…" onChange={(event) => updateAppearance('avatar_url', event.target.value)} /></label>
            <label className="dashboard-share-wide"><span>自定义 CSS</span><textarea className="dashboard-share-css" value={appearance.custom_css} maxLength={12_000} rows={5} spellCheck={false} placeholder="例如：.shared-dashboard-shell { --accent: #8fe8ff; }" onChange={(event) => updateAppearance('custom_css', event.target.value)} /><small>作用于分享页；不可使用外部资源引用。</small></label>
          </fieldset>
          <form className="dashboard-share-form" onSubmit={(event) => { event.preventDefault(); publish.mutate(); }}>
            <label><span>{share.data?.enabled ? '更新访问密码' : '访问密码'}（可选）</span><input type="password" value={password} maxLength={256} autoComplete="new-password" placeholder="留空则无需密码" onChange={(event) => setPassword(event.target.value)} /></label>
            <button type="submit" className="button button-primary" disabled={publish.isPending || dashboardDirty}>
              {publish.isPending ? <RefreshCw className="spinning" size={16} /> : <Share2 size={16} />}
              {share.data?.enabled ? '更新分享内容' : '创建分享'}
            </button>
            {share.data?.enabled && <button type="button" className="button button-danger" disabled={revoke.isPending} onClick={() => revoke.mutate()}><Trash2 size={16} />停止分享</button>}
          </form>
          {error && <div className="dashboard-save-error" role="alert">{error instanceof ApiError ? error.message : '操作失败，请重试'}</div>}
          {share.data?.enabled && <section className="dashboard-share-audit">
            <div className="dashboard-share-audit-heading"><div><strong>访问记录</strong><span>{audit.data?.total ?? 0} 条</span></div><button type="button" className="icon-button" onClick={() => void audit.refetch()} aria-label="刷新访问记录"><RefreshCw size={16} /></button></div>
            {audit.data?.items.length ? <div className="dashboard-share-audit-list">{audit.data.items.map((item) => <div key={item.id}>
              <span className={item.outcome === 'success' ? 'share-audit-ok' : 'share-audit-failed'}>{item.event_type === 'view' ? '访问' : '解锁'} · {item.outcome === 'success' ? '成功' : item.outcome === 'invalid_password' ? '密码错误' : item.outcome}</span>
              <small>{item.device_class} · {item.visitor_hash} · {formatDateTime(item.occurred_at)}</small>
            </div>)}</div> : <div className="dashboard-share-empty">还没有访问记录</div>}
          </section>}
        </div>}
      </section>
    </div>
  );
}
