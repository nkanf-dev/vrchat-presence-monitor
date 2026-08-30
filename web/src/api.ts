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
  live: z.boolean().default(false),
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

const analyticsStatsSchema = z.object({
  days: z.number().int().positive(),
  online_now: z.number().int().nonnegative().default(0),
  friend_count: z.number().int().nonnegative().default(0),
  status_counts: z.record(z.string(), z.number().int().nonnegative()).default({}),
  daily_changes: z.array(z.object({
    day: z.string(),
    changes: z.number().int().nonnegative(),
  })).default([]),
  online_hours: z.array(z.object({
    id: z.string(),
    name: z.string(),
    seconds: z.number().nonnegative(),
    hours: z.number().nonnegative(),
  })).default([]),
  online_hours_all: z.array(z.object({
    id: z.string(),
    name: z.string(),
    seconds: z.number().nonnegative(),
    hours: z.number().nonnegative(),
  })).default([]),
});

const presenceSpanSchema = z.object({
  start_minute: z.number().min(0).max(1440),
  end_minute: z.number().min(0).max(1440),
  status: z.string().default('active'),
});

const coverageGapSchema = z.object({
  start: z.string(),
  end: z.string(),
  minutes: z.number().nonnegative(),
});

const coverageSchema = z.object({
  expected_minutes: z.number().nonnegative(),
  observed_minutes: z.number().nonnegative(),
  ratio: z.number().min(0).max(1),
  first_observed: z.string().nullable(),
  last_observed: z.string().nullable(),
  gaps: z.array(coverageGapSchema),
});

const activityCellSchema = z.object({
  ratio: z.number().min(0).max(1).nullable(),
  online_minutes: z.number().nonnegative(),
  observed_minutes: z.number().nonnegative(),
  eligible_minutes: z.number().nonnegative(),
  covered_days: z.number().int().nonnegative(),
  range_days: z.number().int().positive(),
});

const presenceAnalyticsSchema = z.object({
  day: z.string(),
  days: z.number().int().positive(),
  future_clamped: z.boolean().default(false),
  heatmap_from: z.string(),
  heatmap_to: z.string(),
  heatmap_days: z.number().int().nonnegative().optional(),
  heatmap_observed_minutes: z.array(z.number().nonnegative()).length(24),
  heatmap_complete_days: z.number().int().nonnegative(),
  timezone: z.string().default(''),
  coverage: coverageSchema.optional(),
  heatmap_coverage: coverageSchema.optional(),
  gaps: z.array(coverageGapSchema).default([]),
  timeline: z.array(z.object({
    id: z.string(),
    name: z.string(),
    username: z.string().default(''),
    is_self: z.boolean().default(false),
    spans: z.array(presenceSpanSchema).default([]),
    online_minutes: z.number().nonnegative().default(0),
  })).default([]),
  heatmap: z.array(z.object({
    id: z.string(),
    name: z.string(),
    is_self: z.boolean().default(false),
    tracking_started_at: z.string().nullable().default(null),
    cells: z.array(activityCellSchema).length(24).default([]),
    values: z.array(z.number().min(0).max(1)).default([]),
  })).default([]),
});

const worldSpanSchema = presenceSpanSchema.extend({
  location: z.string().default(''),
  world_id: z.string().default(''),
  platform: z.string().default(''),
  location_kind: z.enum(['world', 'private', 'traveling', 'hidden', 'offline']).default('hidden'),
});

const worldAnalyticsSchema = z.object({
  day: z.string(),
  future_clamped: z.boolean().default(false),
  timezone: z.string().default(''),
  self_id: z.string().default(''),
  friends: z.array(z.object({
    id: z.string(),
    name: z.string(),
    username: z.string().default(''),
    is_self: z.boolean().default(false),
    avatar_url: z.string().default(''),
    online_minutes: z.number().nonnegative().default(0),
    spans: z.array(worldSpanSchema).default([]),
  })).default([]),
  world_ids: z.array(z.string()).default([]),
  coverage: coverageSchema.optional(),
  gaps: z.array(coverageGapSchema).default([]),
});

const standaloneCoverageSchema = coverageSchema.extend({
  from: z.string(),
  to: z.string(),
  range_days: z.number().int().positive(),
  timezone: z.string(),
});

const optionalMetric = z.union([z.number(), z.string(), z.null()]).optional();

const worldInfoSchema = z.object({
  id: z.string(),
  name: z.string(),
  description: z.string().default(''),
  thumbnail_url: z.string().default(''),
  image_url: z.string().default(''),
  author_id: z.string().default(''),
  author_name: z.string().default(''),
  capacity: optionalMetric,
  recommended_capacity: optionalMetric,
  occupants: optionalMetric,
  visits: optionalMetric,
  favorites: optionalMetric,
  popularity: optionalMetric,
  heat: optionalMetric,
  release_status: z.string().default(''),
  organization: z.string().default(''),
  tags: z.array(z.string()).default([]),
  publication_date: z.string().default(''),
  created_at: z.string().default(''),
  updated_at: z.string().default(''),
  resolution_status: z.enum(['ready', 'pending', 'unavailable', 'queued', 'retry']).optional(),
}).passthrough();

const tagSchema = z.object({
  id: z.string(),
  name: z.string(),
  color: z.string(),
  created_at: z.string(),
  updated_at: z.string(),
  friend_count: z.number().int().nonnegative().optional(),
});

const annotationSchema = z.object({
  friend_id: z.string(),
  note: z.string(),
  pinned: z.boolean(),
  revision: z.string().nullable(),
  updated_at: z.string().nullable(),
  tags: z.array(tagSchema),
});

const identityEventSchema = z.object({
  event_id: z.string(),
  field: z.enum(['username', 'display_name']),
  old_value: z.string(),
  new_value: z.string(),
  occurred_at: z.string(),
  source: z.string(),
});

const friendInsightSchema = z.object({
  friend: friendSchema,
  from: z.string(),
  to: z.string(),
  timezone: z.string(),
  first_recorded_at: z.string().nullable(),
  latest_observed_online: z.string().nullable(),
  online_minutes: z.number().nonnegative(),
  online_overlap_minutes: z.number().nonnegative(),
  co_presence_minutes: z.number().nonnegative(),
  most_visited_worlds: z.array(z.object({
    world_id: z.string(),
    name: z.string(),
    minutes: z.number().nonnegative(),
    visits: z.number().int().nonnegative(),
    last_observed: z.string(),
  })),
  hourly_activity: z.array(activityCellSchema.extend({
    hour: z.number().int().min(0).max(23),
  })).length(24),
  identity_events: z.array(identityEventSchema),
  coverage: coverageSchema,
  gaps: z.array(coverageGapSchema),
});

const searchTagSchema = tagSchema.pick({ id: true, name: true, color: true });

const searchSchema = z.object({
  query: z.string(),
  groups: z.object({
    people: z.array(z.object({
      id: z.string(),
      username: z.string(),
      name: z.string(),
      status: z.string(),
      location: z.string(),
      avatar_url: z.string(),
      is_self: z.boolean(),
      pinned: z.boolean(),
      tags: z.array(searchTagSchema),
      matches: z.array(z.enum(['id', 'current_name', 'historical_name', 'note', 'tag'])),
      href: z.string().startsWith('#'),
    })),
    worlds: z.array(z.object({
      id: z.string(),
      name: z.string(),
      author_name: z.string(),
      thumbnail_url: z.string(),
      last_observed: z.string(),
      resolved: z.boolean(),
      href: z.string().startsWith('#'),
    })),
    history: z.array(z.object({
      id: z.string(),
      friend_id: z.string(),
      name: z.string(),
      username: z.string(),
      occurred_at: z.string(),
      old_status: z.string(),
      new_status: z.string(),
      location: z.string(),
      platform: z.string(),
      href: z.string().startsWith('#'),
    })),
    destinations: z.array(z.object({
      id: z.string(),
      name: z.string(),
      description: z.string(),
      href: z.string().startsWith('#'),
    })),
  }),
});

const preferenceSchema = z.object({
  timezone: z.string(),
  updated_at: z.string(),
});

const worldLibraryItemSchema = worldInfoSchema.extend({
  last_observed: z.string(),
  event_count: z.number().int().nonnegative(),
  stale: z.boolean().default(false),
});

const worldLibrarySchema = z.object({
  items: z.array(worldLibraryItemSchema),
  next_cursor: z.string().nullable(),
  total: z.number().int().nonnegative(),
});

const discoveryWorldStatsSchema = z.object({
  minutes: z.number().nonnegative(),
  unique_people: z.number().int().nonnegative(),
  visit_count: z.number().int().nonnegative(),
  return_visits: z.number().int().nonnegative(),
  last_observed: z.string().nullable(),
});

const discoveryWorldSchema = worldInfoSchema.extend({
  world_id: z.string(),
  rank: z.number().int().positive(),
  minutes: z.number().nonnegative(),
  unique_people: z.number().int().nonnegative(),
  visit_count: z.number().int().nonnegative(),
  return_visits: z.number().int().nonnegative(),
  last_observed: z.string().nullable(),
  stale: z.boolean().default(false),
});

const risingWorldSchema = worldInfoSchema.extend({
  world_id: z.string(),
  rank: z.number().int().positive(),
  current: discoveryWorldStatsSchema,
  previous: discoveryWorldStatsSchema,
  delta: z.object({
    minutes: z.number(),
    unique_people: z.number().int(),
    visit_count: z.number().int(),
  }),
  stale: z.boolean().default(false),
});

const discoveryCoverageSchema = z.object({
  range_minutes: z.number().nonnegative(),
  covered_minutes: z.number().nonnegative(),
  ratio: z.number().min(0).max(1),
  first_recorded: z.string().nullable(),
  last_recorded: z.string().nullable(),
  gaps: z.array(z.object({
    from: z.string(),
    to: z.string(),
    minutes: z.number().nonnegative(),
  })),
});

const discoverySchema = z.object({
  hot: z.array(discoveryWorldSchema),
  rising: z.array(risingWorldSchema),
  unavailable_minutes: z.number().nonnegative(),
  previous_unavailable_minutes: z.number().nonnegative(),
  range: z.object({
    days: z.union([z.literal(7), z.literal(30)]),
    from: z.string(),
    to: z.string(),
    previous_from: z.string(),
    previous_to: z.string(),
  }),
  coverage: discoveryCoverageSchema,
  selected_people: z.number().int().nonnegative(),
  ranking: z.object({
    hot: z.array(z.string()),
    rising: z.array(z.string()),
  }),
});

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
export type AnalyticsStats = z.infer<typeof analyticsStatsSchema>;
export type PresenceAnalytics = z.infer<typeof presenceAnalyticsSchema>;
export type PresenceSpan = z.infer<typeof presenceSpanSchema>;
export type WorldAnalytics = z.infer<typeof worldAnalyticsSchema>;
export type WorldSpan = z.infer<typeof worldSpanSchema>;
export type WorldInfo = z.infer<typeof worldInfoSchema>;
export type Coverage = z.infer<typeof standaloneCoverageSchema>;
export type Tag = z.infer<typeof tagSchema>;
export type Annotation = z.infer<typeof annotationSchema>;
export type FriendInsight = z.infer<typeof friendInsightSchema>;
export type SearchResults = z.infer<typeof searchSchema>;
export type Preference = z.infer<typeof preferenceSchema>;
export type WorldLibraryItem = z.infer<typeof worldLibraryItemSchema>;
export type WorldLibrary = z.infer<typeof worldLibrarySchema>;
export type DiscoveryWorld = z.infer<typeof discoveryWorldSchema>;
export type RisingWorld = z.infer<typeof risingWorldSchema>;
export type Discovery = z.infer<typeof discoverySchema>;

export class ApiError extends Error {
  readonly status: number;
  readonly code: 'http' | 'network' | 'invalid-data';
  readonly details: unknown;

  constructor(
    message: string,
    status = 0,
    code: ApiError['code'] = 'http',
    details: unknown = null,
  ) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.details = details;
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
    throw new ApiError(message, response.status, 'http', payload);
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

/** Legacy API compatibility for existing viewer-session migrations. */
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

export const disconnectVrchat = () =>
  request('/v1/vrchat/disconnect', z.object({ ok: z.literal(true) }).passthrough(), {
    method: 'POST',
    body: JSON.stringify({}),
  });

export const getOverview = () => request('/v1/overview', overviewSchema);

export const getAnalyticsStats = (days = 30) => {
  const parameters = new URLSearchParams({ days: String(days) });
  return request(`/v1/analytics/stats?${parameters}`, analyticsStatsSchema);
};

export const getPresenceAnalytics = (options: {
  day: string;
  heatmapFrom: string;
  heatmapTo: string;
}) => {
  const parameters = new URLSearchParams({
    day: options.day,
    heatmap_from: options.heatmapFrom,
    heatmap_to: options.heatmapTo,
  });
  return request(`/v1/analytics/presence?${parameters}`, presenceAnalyticsSchema);
};

export const getWorldAnalytics = (day: string) => {
  const parameters = new URLSearchParams({ day });
  return request(`/v1/analytics/worlds?${parameters}`, worldAnalyticsSchema);
};

export const getCoverage = (rangeFrom: string, rangeTo: string) => {
  const parameters = new URLSearchParams({ from: rangeFrom, to: rangeTo });
  return request(`/v1/coverage?${parameters}`, standaloneCoverageSchema);
};

export const getWorld = (worldId: string) =>
  request(`/v1/worlds/${encodeURIComponent(worldId)}`, worldInfoSchema);

export const worldImageUrl = (source: string) =>
  source ? `/v1/world-image?${new URLSearchParams({ url: source })}` : '';

export const syncNow = () =>
  request('/v1/sync', z.object({ ok: z.boolean().default(true) }).passthrough(), {
    method: 'POST',
    body: JSON.stringify({}),
  });

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

export const getSearch = (query: string, signal?: AbortSignal) => {
  const parameters = new URLSearchParams({ q: query, limit: '8' });
  return request(
    `/v1/search?${parameters}`,
    searchSchema,
    signal ? { signal } : {},
  );
};

export const getFriendInsight = (
  friendId: string,
  rangeFrom: string,
  rangeTo: string,
) => {
  const parameters = new URLSearchParams({ from: rangeFrom, to: rangeTo });
  return request(
    `/v1/friends/${encodeURIComponent(friendId)}/insights?${parameters}`,
    friendInsightSchema,
  );
};

export const getFriendAnnotation = (friendId: string) =>
  request(
    `/v1/friends/${encodeURIComponent(friendId)}/annotation`,
    annotationSchema,
  );

export const updateFriendAnnotation = (
  friendId: string,
  value: Pick<Annotation, 'note' | 'pinned' | 'revision'>,
) => request(
  `/v1/friends/${encodeURIComponent(friendId)}/annotation`,
  annotationSchema,
  { method: 'PUT', body: JSON.stringify(value) },
);

export const getTags = () => request('/v1/tags', z.array(tagSchema));

export const createTag = (value: Pick<Tag, 'name' | 'color'>) =>
  request('/v1/tags', tagSchema, {
    method: 'POST',
    body: JSON.stringify(value),
  });

export const updateTag = (tagId: string, value: Pick<Tag, 'name' | 'color'>) =>
  request(`/v1/tags/${encodeURIComponent(tagId)}`, tagSchema, {
    method: 'PUT',
    body: JSON.stringify(value),
  });

export const deleteTag = (tagId: string) =>
  request(
    `/v1/tags/${encodeURIComponent(tagId)}`,
    z.object({ ok: z.literal(true) }),
    { method: 'DELETE', body: JSON.stringify({}) },
  );

export const assignFriendTag = (friendId: string, tagId: string) =>
  request(
    `/v1/friends/${encodeURIComponent(friendId)}/tags/${encodeURIComponent(tagId)}`,
    z.object({ ok: z.literal(true), friend_id: z.string(), tag_id: z.string() }),
    { method: 'PUT', body: JSON.stringify({}) },
  );

export const unassignFriendTag = (friendId: string, tagId: string) =>
  request(
    `/v1/friends/${encodeURIComponent(friendId)}/tags/${encodeURIComponent(tagId)}`,
    z.object({ ok: z.literal(true), friend_id: z.string(), tag_id: z.string() }),
    { method: 'DELETE', body: JSON.stringify({}) },
  );

export const getPreferences = () => request('/v1/preferences', preferenceSchema);

export const updatePreferences = (timezone: string) =>
  request('/v1/preferences', preferenceSchema, {
    method: 'PUT',
    body: JSON.stringify({ timezone }),
  });

export const getWorldLibrary = (options: {
  query?: string;
  author?: string;
  friendId?: string;
  tagId?: string;
  cursor?: string;
  limit?: number;
} = {}) => {
  const parameters = new URLSearchParams({ limit: String(options.limit ?? 36) });
  if (options.query) parameters.set('q', options.query);
  if (options.author) parameters.set('author', options.author);
  if (options.friendId) parameters.set('friend_id', options.friendId);
  if (options.tagId) parameters.set('tag_id', options.tagId);
  if (options.cursor) parameters.set('cursor', options.cursor);
  return request(`/v1/world-library?${parameters}`, worldLibrarySchema);
};

export const getDiscovery = (options: {
  days?: 7 | 30;
  friendId?: string;
  tagId?: string;
  includeSelf?: boolean;
} = {}) => {
  const parameters = new URLSearchParams({
    days: String(options.days ?? 7),
    include_self: String(options.includeSelf ?? true),
  });
  if (options.friendId) parameters.set('friend_id', options.friendId);
  if (options.tagId) parameters.set('tag_id', options.tagId);
  return request(`/v1/discovery/worlds?${parameters}`, discoverySchema);
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
