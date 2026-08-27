import { Cloud, LogOut, Unplug, UserRound, X } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';

import type { Identity, Overview } from '../api';

type AccountAction = 'logout' | 'disconnect';

export function AccountMenu({
  identity,
  overview,
  busy,
  onLogout,
  onDisconnect,
}: {
  identity: Identity;
  overview: Overview | undefined;
  busy: boolean;
  onLogout: () => Promise<void> | void;
  onDisconnect: () => Promise<void> | void;
}) {
  const root = useRef<HTMLDivElement>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const [open, setOpen] = useState(false);
  const [action, setAction] = useState<AccountAction | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    const close = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    document.addEventListener('pointerdown', close);
    document.addEventListener('keydown', escape);
    return () => {
      document.removeEventListener('pointerdown', close);
      document.removeEventListener('keydown', escape);
    };
  }, []);

  useEffect(() => {
    const element = dialog.current;
    if (!element) return;
    if (action && !element.open) element.showModal();
    if (!action && element.open) element.close();
  }, [action]);

  const confirm = async () => {
    if (!action || busy) return;
    setError('');
    try {
      if (action === 'logout') await onLogout();
      else await onDisconnect();
      dialog.current?.close();
      setAction(null);
      setOpen(false);
    } catch {
      setError(action === 'logout' ? '暂时无法退出此设备，请重试。' : '暂时无法停止采集，请重试。');
    }
  };

  const connected = overview?.collector_state === 'fresh';

  return (
    <div className="account-menu-root" ref={root}>
      <button
        type="button"
        className="avatar-menu"
        onClick={() => setOpen((value) => !value)}
        aria-label="打开账户菜单"
        aria-haspopup="menu"
        aria-expanded={open}
      >
        {Array.from(identity.name)[0] ?? 'P'}
      </button>
      {open && (
        <div className="account-menu" role="menu">
          <header>
            <span className="account-menu-avatar"><UserRound size={20} aria-hidden="true" /></span>
            <div>
              <strong>{identity.name}</strong>
              <span>{connected ? '云端持续采集中' : '你的 Presence Monitor 空间'}</span>
            </div>
          </header>
          <p><Cloud size={16} aria-hidden="true" />关闭或刷新页面不会中断持续采集。</p>
          <div className="account-menu-actions">
            <button type="button" role="menuitem" onClick={() => { setError(''); setAction('logout'); }}>
              <LogOut size={17} aria-hidden="true" />
              <span><strong>退出此设备</strong><small>其他设备与云端采集不受影响</small></span>
            </button>
            <button type="button" role="menuitem" className="danger" onClick={() => { setError(''); setAction('disconnect'); }}>
              <Unplug size={17} aria-hidden="true" />
              <span><strong>断开 VRChat 并停止采集</strong><small>历史数据仍然保留</small></span>
            </button>
          </div>
        </div>
      )}

      <dialog
        className="dialog confirmation-dialog"
        ref={dialog}
        aria-labelledby="account-confirm-title"
        onCancel={() => setAction(null)}
        onClose={() => { setAction(null); setError(''); }}
      >
        {action && (
          <div className="dialog-scroll">
            <button className="icon-button dialog-close" onClick={() => dialog.current?.close()} aria-label="取消">
              <X size={20} aria-hidden="true" />
            </button>
            <span className={action === 'disconnect' ? 'confirmation-icon danger' : 'confirmation-icon'} aria-hidden="true">
              {action === 'disconnect' ? <Unplug size={24} /> : <LogOut size={24} />}
            </span>
            <h2 id="account-confirm-title">{action === 'logout' ? '退出此设备？' : '停止云端采集？'}</h2>
            <p>
              {action === 'logout'
                ? '只会退出当前浏览器。VRChat 连接和云端采集继续运行，其他已登录设备不受影响。'
                : '服务器会撤销当前 VRChat 连接并停止后续采集；已保存的玩家与历史数据不会删除。'}
            </p>
            {error && <p className="form-error" role="alert">{error}</p>}
            <div className="confirmation-actions">
              <button type="button" className="button button-secondary" onClick={() => dialog.current?.close()} disabled={busy}>取消</button>
              <button
                type="button"
                className={action === 'disconnect' ? 'button button-danger' : 'button button-primary'}
                onClick={() => void confirm()}
                disabled={busy}
              >
                {action === 'disconnect' ? <Unplug size={17} aria-hidden="true" /> : <LogOut size={17} aria-hidden="true" />}
                {busy ? '正在处理…' : action === 'disconnect' ? '断开并停止' : '退出此设备'}
              </button>
            </div>
          </div>
        )}
      </dialog>
    </div>
  );
}
