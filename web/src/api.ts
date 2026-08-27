import { z } from 'zod';

const identitySchema = z.object({
  tenant_id: z.string(),
  name: z.string(),
});

const meSchema = z.object({
  authenticated: z.literal(true),
  user: identitySchema,
  migrated: z.boolean().default(false),
});

const loginSchema = z.object({
  ok: z.literal(true),
  user: identitySchema,
  expires_at: z.string(),
});

const vrchatLoginSuccessSchema = z.object({
  ok: z.literal(true),
  requires_2fa: z.literal(false),
  user: identitySchema,
  expires_at: z.string(),
});

const vrchatTwoFactorRequiredSchema = z.object({
  ok: z.literal(true),
  requires_2fa: z.literal(true),
  methods: z.array(z.string()),
});

const vrchatLoginResultSchema = z.discriminatedUnion('requires_2fa', [
  vrchatLoginSuccessSchema,
  vrchatTwoFactorRequiredSchema,
]);

const overviewSchema = z.object({
  tracked_count: z.number().int().nonnegative(),
  online_count: z.number().int().nonnegative(),
  event_total: z.number().int().nonnegative(),
  change_count_7d: z.number().int().nonnegative(),
  status_counts: z.record(z.string(), z.number().int().nonnegative()),
  last_sync: z.string().nullable(),
  collector_error: z.string(),
  collector_state: z.enum(['fresh', 'stale', 'error', 'never']),
  sync_age_seconds: z.number().int().nonnegative().nullable(),
  stale_after_seconds: z.number().int().positive(),
});

export const friendSchema = z
  .object({
    id: z.string(),
    username: z.string().default(''),
    display_name: z.string().default(''),
    is_self: z.union([z.number(), z.boolean()]).default(0),
    status: z.string().default('offline'),
    status_description: z.string().default(''),
    location: z.string().default(''),
    platform: z.string().default(''),
    avatar_url: z.string().default(''),
    avatar_image_url: z.string().default(''),
    bio: z.string().default(''),
    bio_links: z.union([z.string(), z.array(z.string())]).default('[]'),
    last_seen: z.string().nullable().optional(),
    last_changed: z.string().nullable().optional(),
    updated_at: z.string(),
  })
  .passthrough();

export const eventSchema = z
  .object({
    client_event_id: z.string(),
    friend_id: z.string(),
    occurred_at: z.string(),
    old_status: z.string(),
    new_status: z.string(),
    location: z.string().default(''),
    platform: z.string().default(''),
    source: z.string().default(''),
    display_name: z.string().nullable().optional(),
    username: z.string().nullable().optional(),
    avatar_image_url: z.string().nullable().optional(),
  })
  .passthrough();

const friendPageSchema = z.object({
  items: z.array(friendSchema),
  total: z.number().int().nonnegative(),
  limit: z.number().int().positive(),
  offset: z.number().int().nonnegative(),
});

const eventPageSchema = z.object({
  items: z.array(eventSchema),
  total: z.number().int().nonnegative(),
  limit: z.number().int().positive(),
  offset: z.number().int().nonnegative(),
});

const importResultSchema = z.object({
  ok: z.literal(true),
  imported: z.object({
    friends: z.number().int().nonnegative().default(0),
    events: z.number().int().nonnegative().default(0),
    changed: z.number().int().nonnegative().default(0),
  }),
});

const capabilitiesSchema = z.object({
  max_import_bytes: z.number().int().positive(),
  max_import_expanded_bytes: z.number().int().positive(),
  max_source_expanded_bytes: z.number().int().positive(),
}).refine(
  (value) => (
    value.max_import_expanded_bytes >= value.max_import_bytes
    && value.max_source_expanded_bytes >= value.max_import_expanded_bytes
  ),
  { message: 'expanded import limits are inconsistent' },
);

export type Identity = z.infer<typeof identitySchema>;
export type Me = z.infer<typeof meSchema>;
export type VrchatLoginSuccess = z.infer<typeof vrchatLoginSuccessSchema>;
export type VrchatLoginResult = z.infer<typeof vrchatLoginResultSchema>;
export type Overview = z.infer<typeof overviewSchema>;
export type Friend = z.infer<typeof friendSchema>;
export type PresenceEvent = z.infer<typeof eventSchema>;
export type FriendPage = z.infer<typeof friendPageSchema>;
export type EventPage = z.infer<typeof eventPageSchema>;
export type ImportResult = z.infer<typeof importResultSchema>;
export type Capabilities = z.infer<typeof capabilitiesSchema>;

export class ApiError extends Error {
  readonly status: number;
  readonly code: 'http' | 'network' | 'invalid-data';

  constructor(message: string, status = 0, code: ApiError['code'] = 'http') {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
  }
}

export const AUTH_REQUIRED_EVENT = 'presence-monitor:auth-required';

const AUTHENTICATION_PATHS = new Set([
  '/v1/me',
  '/v1/login',
  '/v1/vrchat/login',
  '/v1/vrchat/2fa',
]);

const notifyAuthenticationRequired = (path: string, status: number) => {
  if (status === 401 && !AUTHENTICATION_PATHS.has(path) && typeof window !== 'undefined') {
    window.dispatchEvent(new Event(AUTH_REQUIRED_EVENT));
  }
};

const LEGACY_KEYS = ['presence-monitor.session', 'vrchat-monitor.viewer-token'] as const;

const legacyToken = () => {
  try {
    for (const key of LEGACY_KEYS) {
      const value = localStorage.getItem(key);
      if (value) return value;
    }
  } catch {
    return '';
  }
  return '';
};

const clearLegacyTokens = () => {
  try {
    for (const key of LEGACY_KEYS) localStorage.removeItem(key);
  } catch {
    // A blocked LocalStorage does not invalidate the HttpOnly cookie session.
  }
};

async function parseResponse(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return {};
  }
}

async function rawRequest(path: string, options: RequestInit = {}): Promise<unknown> {
  let response: Response;
  try {
    response = await fetch(path, {
      ...options,
      credentials: 'same-origin',
      headers: {
        Accept: 'application/json',
        ...(options.body ? { 'Content-Type': 'application/json' } : {}),
        ...options.headers,
      },
    });
  } catch {
    throw new ApiError('暂时无法连接服务', 0, 'network');
  }
  const payload = await parseResponse(response);
  if (!response.ok) {
    notifyAuthenticationRequired(path, response.status);
    const message =
      typeof payload === 'object' && payload && 'error' in payload && typeof payload.error === 'string'
        ? payload.error
        : `请求失败（${response.status}）`;
    throw new ApiError(message, response.status);
  }
  return payload;
}

async function request<T>(path: string, schema: z.ZodType<T>, options: RequestInit = {}): Promise<T> {
  const payload = await rawRequest(path, options);
  const parsed = schema.safeParse(payload);
  if (!parsed.success) throw new ApiError('服务器返回了无法识别的数据', 0, 'invalid-data');
  return parsed.data;
}

const backupFilename = (response: Response) => {
  const disposition = response.headers.get('Content-Disposition') ?? '';
  const utf8 = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  const quoted = disposition.match(/filename="([^"]+)"/i)?.[1];
  const plain = disposition.match(/filename=([^;]+)/i)?.[1]?.trim();
  let candidate = utf8 ?? quoted ?? plain ?? '';
  if (utf8) {
    try {
      candidate = decodeURIComponent(utf8);
    } catch {
      candidate = '';
    }
  }
  candidate = candidate.split(/[\\/]/).at(-1) ?? '';
  candidate = [...candidate]
    .filter((character) => {
      const code = character.charCodeAt(0);
      return code >= 0x20 && code !== 0x7f;
    })
    .join('');
  if (/\.json(?:\.gz)?$/i.test(candidate)) return candidate;
  return response.headers.get('Content-Type')?.includes('application/gzip')
    ? 'presence-monitor-backup.json.gz'
    : 'presence-monitor-backup.json';
};

export async function downloadBackup(): Promise<{ blob: Blob; filename: string }> {
  let response: Response;
  try {
    response = await fetch('/v1/export.json', {
      credentials: 'same-origin',
      headers: { Accept: 'application/json, application/gzip' },
    });
  } catch {
    throw new ApiError('暂时无法连接服务', 0, 'network');
  }
  if (!response.ok) {
    notifyAuthenticationRequired('/v1/export.json', response.status);
    const payload = await parseResponse(response);
    const message =
      typeof payload === 'object' && payload && 'error' in payload && typeof payload.error === 'string'
        ? payload.error
        : `备份导出失败（${response.status}）`;
    throw new ApiError(message, response.status);
  }
  let blob: Blob;
  try {
    blob = await response.blob();
  } catch {
    throw new ApiError('备份下载中断，请重试', 0, 'network');
  }
  if (blob.size === 0) throw new ApiError('服务器返回了空备份', 0, 'invalid-data');
  return { blob, filename: backupFilename(response) };
}

export async function getMe(): Promise<Me> {
  try {
    return await request('/v1/me', meSchema);
  } catch (error) {
    const token = legacyToken();
    if (!(error instanceof ApiError) || error.status !== 401 || !token) throw error;
    const adopted = await request('/v1/me', meSchema, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (adopted.migrated) clearLegacyTokens();
    return adopted;
  }
}

export const login = (accessCode: string) =>
  request('/v1/login', loginSchema, {
    method: 'POST',
    body: JSON.stringify({ access_code: accessCode }),
  });

export const loginVrchat = (credentials: { username: string; password: string }) =>
  request('/v1/vrchat/login', vrchatLoginResultSchema, {
    method: 'POST',
    body: JSON.stringify(credentials),
  });

export const verifyVrchat2fa = (code: string) =>
  request('/v1/vrchat/2fa', vrchatLoginSuccessSchema, {
    method: 'POST',
    body: JSON.stringify({ code }),
  });

export const logout = () =>
  request('/v1/logout', z.object({ ok: z.literal(true) }), {
    method: 'POST',
    body: JSON.stringify({}),
  });

export const getOverview = () => request('/v1/overview', overviewSchema);

export const getCapabilities = () => request('/v1/capabilities', capabilitiesSchema);

export const getFriends = (options: {
  query?: string;
  status?: string;
  limit?: number;
  offset?: number;
} = {}) => {
  const parameters = new URLSearchParams();
  if (options.query) parameters.set('q', options.query);
  if (options.status) parameters.set('status', options.status);
  parameters.set('limit', String(options.limit ?? 50));
  parameters.set('offset', String(options.offset ?? 0));
  return request(`/v1/friends?${parameters}`, friendPageSchema);
};

export const getEvents = (options: { query?: string; limit?: number; offset?: number } = {}) => {
  const parameters = new URLSearchParams();
  if (options.query) parameters.set('q', options.query);
  parameters.set('limit', String(options.limit ?? 50));
  parameters.set('offset', String(options.offset ?? 0));
  return request(`/v1/events?${parameters}`, eventPageSchema);
};

export const importBackupFile = (file: File) =>
  request('/v1/import.json', importResultSchema, {
    method: 'POST',
    body: file,
    headers: {
      'Content-Type': /\.gz$/i.test(file.name) || file.type === 'application/gzip'
        ? 'application/gzip'
        : 'application/json',
    },
  });
