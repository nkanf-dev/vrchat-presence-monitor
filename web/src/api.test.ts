import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  AUTH_REQUIRED_EVENT,
  downloadBackup,
  getMe,
  getOverview,
  login,
  loginVrchat,
  verifyVrchat2fa,
} from './api';

const jsonResponse = (status: number, body: unknown, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', ...headers },
  });

describe('hosted API client', () => {
  afterEach(() => {
    localStorage.clear();
    vi.unstubAllGlobals();
  });

  it('starts a VRChat login with the exact account contract', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        ok: true,
        requires_2fa: true,
        methods: ['totp', 'emailOtp'],
      }),
    );
    vi.stubGlobal('fetch', fetchMock);

    const result = await loginVrchat({ username: 'alice@example.com', password: 'correct horse' });

    expect(result).toEqual({ ok: true, requires_2fa: true, methods: ['totp', 'emailOtp'] });
    expect(fetchMock).toHaveBeenCalledWith(
      '/v1/vrchat/login',
      expect.objectContaining({
        method: 'POST',
        credentials: 'same-origin',
        body: JSON.stringify({ username: 'alice@example.com', password: 'correct horse' }),
      }),
    );
  });

  it('finishes VRChat two-factor login with the exact code contract', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        ok: true,
        requires_2fa: false,
        user: { tenant_id: 'ten_1', name: 'Alice' },
        expires_at: '2026-09-26T00:00:00+00:00',
      }),
    );
    vi.stubGlobal('fetch', fetchMock);

    const result = await verifyVrchat2fa('123456');

    expect(result.user.name).toBe('Alice');
    expect(fetchMock).toHaveBeenCalledWith(
      '/v1/vrchat/2fa',
      expect.objectContaining({
        method: 'POST',
        credentials: 'same-origin',
        body: JSON.stringify({ code: '123456' }),
      }),
    );
  });

  it('keeps the access-code login API compatible', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        ok: true,
        user: { tenant_id: 'ten_1', name: 'Alice' },
        expires_at: '2026-09-26T00:00:00+00:00',
      }),
    );
    vi.stubGlobal('fetch', fetchMock);

    const result = await login('ABCD-EFGH-JKLM-NPQR-STUV');

    expect(result.user.name).toBe('Alice');
    expect(JSON.stringify(result)).not.toContain('session_token');
    expect(fetchMock).toHaveBeenCalledWith(
      '/v1/login',
      expect.objectContaining({
        method: 'POST',
        credentials: 'same-origin',
        body: JSON.stringify({ access_code: 'ABCD-EFGH-JKLM-NPQR-STUV' }),
      }),
    );
  });

  it('adopts a legacy LocalStorage token once and removes it after migration', async () => {
    localStorage.setItem('presence-monitor.session', 'legacy-secret');
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(401, { error: 'unauthorized' }))
      .mockResolvedValueOnce(
        jsonResponse(200, {
          authenticated: true,
          migrated: true,
          user: { tenant_id: 'ten_1', name: 'Alice' },
        }),
      );
    vi.stubGlobal('fetch', fetchMock);

    const result = await getMe();

    expect(result.user.name).toBe('Alice');
    expect(localStorage.getItem('presence-monitor.session')).toBeNull();
    expect(fetchMock.mock.calls[1]?.[1]).toEqual(
      expect.objectContaining({ headers: expect.objectContaining({ Authorization: 'Bearer legacy-secret' }) }),
    );
  });

  it('turns network failures into one user-safe error', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));

    await expect(getMe()).rejects.toEqual(
      expect.objectContaining({ code: 'network', message: '暂时无法连接服务' }),
    );
  });

  it('downloads the server-selected backup format only after a successful response', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response('{"format":"vrchat-monitor-hosted-backup"}', {
        status: 200,
        headers: {
          'Content-Type': 'application/gzip',
          'Content-Disposition': 'attachment; filename="tenant-backup.json.gz"',
        },
      }),
    );
    vi.stubGlobal('fetch', fetchMock);

    const result = await downloadBackup();

    expect(result.filename).toBe('tenant-backup.json.gz');
    expect(result.blob.size).toBeGreaterThan(0);
    expect(fetchMock).toHaveBeenCalledWith(
      '/v1/export.json',
      expect.objectContaining({ credentials: 'same-origin' }),
    );
  });

  it('reports an export error instead of treating an error page as a download', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(jsonResponse(503, { error: '备份容量暂时不可用' })),
    );

    await expect(downloadBackup()).rejects.toEqual(
      expect.objectContaining({ status: 503, message: '备份容量暂时不可用' }),
    );
  });

  it('announces an expired session for every authenticated endpoint', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(401, { error: 'unauthorized' })));
    const listener = vi.fn();
    window.addEventListener(AUTH_REQUIRED_EVENT, listener);

    await expect(getOverview()).rejects.toEqual(expect.objectContaining({ status: 401 }));

    expect(listener).toHaveBeenCalledTimes(1);
    window.removeEventListener(AUTH_REQUIRED_EVENT, listener);
  });
});
