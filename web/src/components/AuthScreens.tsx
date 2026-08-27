import { ArrowRight, RefreshCw } from 'lucide-react';
import { FormEvent, useEffect, useState } from 'react';

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

export function LoginScreen({
  pending,
  error,
  requiresTwoFactor,
  onLogin,
  onVerify,
  onEdit,
}: {
  pending: boolean;
  error: Error | null;
  requiresTwoFactor: boolean;
  onLogin: (credentials: { username: string; password: string }) => Promise<void>;
  onVerify: (code: string) => Promise<void>;
  onEdit: () => void;
}) {
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
    if (requiresTwoFactor) {
      const value = code.trim();
      if (value) await onVerify(value);
      return;
    }
    const account = username.trim();
    if (account && password) await onLogin({ username: account, password });
  };

  const errorMessage = loginErrorMessage(error, requiresTwoFactor);

  const canSubmit = requiresTwoFactor ? Boolean(code.trim()) : Boolean(username.trim() && password);

  return (
    <main className="auth-screen">
      <section className="auth-card" aria-labelledby="login-title">
        <Brand />
        <div className="auth-intro">
          <h1 id="login-title">{requiresTwoFactor ? '输入验证码' : '登录 VRChat'}</h1>
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
            {pending ? '正在登录…' : '登录'}
            {!pending && <ArrowRight size={18} aria-hidden="true" />}
          </button>
        </form>
      </section>
    </main>
  );
}
