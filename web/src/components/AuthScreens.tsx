import { ArrowRight, RefreshCw, X } from 'lucide-react';
import { FormEvent, useEffect, useRef, useState } from 'react';

import { ApiError } from '../api';
import { Brand } from './Brand';

export function LoadingScreen() {
  return (
    <main className="auth-screen" aria-busy="true">
      <section className="auth-card loading-card" role="status" aria-live="polite">
        <Brand />
        <span className="loader" aria-hidden="true" />
        <h1>正在打开你的面板</h1>
        <p>验证这台设备的登录状态…</p>
      </section>
    </main>
  );
}

export function OfflineScreen({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <main className="auth-screen">
      <section className="auth-card" aria-labelledby="offline-title">
        <Brand />
        <p className="kicker">连接中断</p>
        <h1 id="offline-title">暂时连不上服务</h1>
        <p>{message}。服务恢复后可以重新连接；如果此前已登录，仍有效的会话会继续使用。</p>
        <button className="button button-primary button-wide" onClick={onRetry} autoFocus>
          <RefreshCw size={18} aria-hidden="true" />
          重新连接
        </button>
      </section>
    </main>
  );
}

const loginErrorMessage = (error: Error | null, requiresTwoFactor: boolean) => {
  if (!error) return '';
  if (!(error instanceof ApiError)) return '登录失败，请重试。';
  if (error.status === 429) return '尝试次数较多，请稍后再试。';
  if (error.status === 401) {
    return requiresTwoFactor ? '验证码不正确，请重试。' : '账号或密码不正确。';
  }
  if (error.status >= 500) return '服务暂时不可用，请稍后重试。';
  if (error.status >= 400) {
    return requiresTwoFactor ? '验证码无法使用，请重新输入。' : '请检查账号和密码后重试。';
  }
  return error.message;
};

type LoginFlowProps = {
  pending: boolean;
  error: Error | null;
  requiresTwoFactor: boolean;
  onLogin: (credentials: { username: string; password: string }) => Promise<void>;
  onVerify: (code: string) => Promise<void>;
  onEdit: () => void;
};

function VrchatLoginPanel({
  pending,
  error,
  requiresTwoFactor,
  onLogin,
  onVerify,
  onEdit,
  reconnect = false,
}: LoginFlowProps & { reconnect?: boolean }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [code, setCode] = useState('');

  useEffect(() => {
    if (!requiresTwoFactor) return;
    setPassword('');
    setCode('');
  }, [requiresTwoFactor]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    try {
      if (requiresTwoFactor) {
        const value = code.trim();
        if (value) await onVerify(value);
        return;
      }
      const account = username.trim();
      if (account && password) await onLogin({ username: account, password });
    } catch {
      // The mutation error is rendered inline by the shared login flow.
    }
  };

  const errorMessage = loginErrorMessage(error, requiresTwoFactor);

  const canSubmit = requiresTwoFactor ? Boolean(code.trim()) : Boolean(username.trim() && password);

  return (
    <>
        <div className="auth-intro">
          <h1 id={reconnect ? 'reconnect-title' : 'login-title'}>
            {requiresTwoFactor ? '输入验证码' : reconnect ? '重新连接 VRChat' : '登录 VRChat'}
          </h1>
          <p>
            {reconnect
              ? '你的面板与历史数据保持不变，重新验证后云端会继续采集。'
              : '使用你的 VRChat 账号登录。'}
          </p>
        </div>
        <form className="login-form" onSubmit={submit}>
          {requiresTwoFactor ? (
            <>
              <label htmlFor="verification-code">验证码</label>
              <input
                id="verification-code"
                name="verification-code"
                className="text-field verification-field"
                value={code}
                onChange={(event) => {
                  setCode(event.target.value);
                  onEdit();
                }}
                placeholder="请输入验证码"
                autoComplete="one-time-code"
                inputMode="numeric"
                autoCapitalize="none"
                spellCheck={false}
                aria-describedby="login-error"
                disabled={pending}
                autoFocus
                required
              />
            </>
          ) : (
            <>
              <label htmlFor="vrchat-username">VRChat 账号</label>
              <input
                id="vrchat-username"
                name="username"
                className="text-field"
                value={username}
                onChange={(event) => {
                  setUsername(event.target.value);
                  onEdit();
                }}
                autoComplete="username"
                autoCapitalize="none"
                spellCheck={false}
                aria-describedby="login-error"
                disabled={pending}
                autoFocus
                required
              />
              <label htmlFor="vrchat-password">密码</label>
              <input
                id="vrchat-password"
                name="password"
                className="text-field"
                type="password"
                value={password}
                onChange={(event) => {
                  setPassword(event.target.value);
                  onEdit();
                }}
                autoComplete="current-password"
                aria-describedby="login-error"
                disabled={pending}
                required
              />
            </>
          )}
          <p className="form-error" id="login-error" role="alert" aria-live="polite">
            {errorMessage}
          </p>
          <button className="button button-primary button-wide" type="submit" disabled={pending || !canSubmit}>
            {pending ? '正在登录…' : requiresTwoFactor ? '验证并继续' : reconnect ? '重新连接' : '登录'}
            {!pending && <ArrowRight size={18} aria-hidden="true" />}
          </button>
        </form>
    </>
  );
}

export function LoginScreen(props: LoginFlowProps) {
  return (
    <main className="auth-screen">
      <section className="auth-card" aria-labelledby="login-title">
        <Brand />
        <VrchatLoginPanel {...props} />
      </section>
    </main>
  );
}

export function VrchatReconnectDialog({
  open,
  onClose,
  ...flow
}: LoginFlowProps & { open: boolean; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const element = dialog.current;
    if (!element) return;
    if (open && !element.open) element.showModal();
    if (!open && element.open) element.close();
  }, [open]);
  return (
    <dialog
      className="dialog reconnect-dialog"
      ref={dialog}
      aria-labelledby="reconnect-title"
      onCancel={onClose}
      onClose={onClose}
    >
      <div className="dialog-scroll">
        <button type="button" className="icon-button dialog-close" onClick={() => dialog.current?.close()} aria-label="关闭重新连接窗口">
          <X size={20} aria-hidden="true" />
        </button>
        <VrchatLoginPanel {...flow} reconnect />
      </div>
    </dialog>
  );
}
