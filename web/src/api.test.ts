import { afterEach, describe, expect, it, vi } from 'vitest';

import { getMe, login } from './api';

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

  it('keeps the browser session out of JavaScript login state', async () => {
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
      expect.objectContaining({ credentials: 'same-origin' }),
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
});
