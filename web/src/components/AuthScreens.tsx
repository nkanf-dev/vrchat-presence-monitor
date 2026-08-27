import { ArrowRight, Eye, EyeOff, RefreshCw, ShieldCheck } from 'lucide-react';
import { FormEvent, useState } from 'react';

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

const formatCode = (value: string) => {
  const compact = value.toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 20);
  return compact.match(/.{1,4}/g)?.join('-') ?? '';
};

export function LoginScreen({
  pending,
  error,
  onLogin,
}: {
  pending: boolean;
  error: Error | null;
  onLogin: (code: string) => Promise<void>;
}) {
  const [code, setCode] = useState('');
  const [visible, setVisible] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!code) return;
    await onLogin(code);
  };

  const errorMessage = error
    ? error instanceof ApiError && error.status === 429
      ? '尝试次数较多，请稍后再试。'
      : error instanceof ApiError && error.status === 401
        ? '访问码不正确或已被撤销。'
        : error.message
    : '';

  return (
    <main className="auth-screen">
      <section className="auth-card" aria-labelledby="login-title">
        <Brand />
        <div className="auth-intro">
          <p className="kicker">安全访问</p>
          <h1 id="login-title">打开你的监控面板</h1>
          <p>输入服务管理员发给你的访问码。如果此前已登录，这台设备会继续使用仍然有效的会话。</p>
        </div>
        <form className="login-form" onSubmit={submit}>
          <label htmlFor="access-code">访问码</label>
          <div className="field-with-action">
            <input
              id="access-code"
              name="access-code"
              className="text-field code-field"
              type={visible ? 'text' : 'password'}
              value={code}
              onChange={(event) => setCode(formatCode(event.target.value))}
              placeholder="XXXX-XXXX-XXXX-XXXX-XXXX"
              autoComplete="off"
              autoCapitalize="characters"
              spellCheck={false}
              aria-describedby="access-help login-error"
              disabled={pending}
              autoFocus
              required
            />
            <button
              className="field-action"
              type="button"
              onClick={() => setVisible((current) => !current)}
              aria-label={visible ? '隐藏访问码' : '显示访问码'}
            >
              {visible ? <EyeOff size={18} aria-hidden="true" /> : <Eye size={18} aria-hidden="true" />}
            </button>
          </div>
          <p className="field-help" id="access-help">
            访问码只用于这个监控站点，不是 VRChat 密码。
          </p>
          <p className="form-error" id="login-error" role="alert" aria-live="polite">
            {errorMessage}
          </p>
          <button className="button button-primary button-wide" type="submit" disabled={pending || !code}>
            {pending ? '正在登录…' : '登录'}
            {!pending && <ArrowRight size={18} aria-hidden="true" />}
          </button>
        </form>
        <div className="trust-note">
          <ShieldCheck size={18} aria-hidden="true" />
          <span>浏览器登录由安全 Cookie 保存，页面脚本无法读取。</span>
        </div>
      </section>
    </main>
  );
}
