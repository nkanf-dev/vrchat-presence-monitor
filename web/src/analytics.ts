import type { WorldAnalytics, WorldInfo, WorldSpan } from './api';

export const PRIVATE_WORLD_ID = '__private__';
export const TRAVELING_WORLD_ID = '__traveling__';
export const OFFLINE_WORLD_ID = '__offline__';
export const HIDDEN_WORLD_ID = '__location_hidden__';

const WORLD_LOCATION_PATTERN = /wrld_[0-9a-f-]{36}/i;

export const worldIdFromLocation = (location: string) =>
  location.match(WORLD_LOCATION_PATTERN)?.[0] ?? '';

const SPECIAL_WORLDS: Record<string, WorldInfo> = {
  [PRIVATE_WORLD_ID]: {
    id: PRIVATE_WORLD_ID,
    name: '私人世界',
    description: '该时段处于私人实例，世界详情不可见。',
    thumbnail_url: '',
    image_url: '',
    author_id: '',
    author_name: '',
    release_status: 'private',
    organization: '',
    tags: [],
    publication_date: '',
    created_at: '',
    updated_at: '',
  },
  [TRAVELING_WORLD_ID]: {
    id: TRAVELING_WORLD_ID,
    name: '切换世界中',
    description: 'VRChat 正在切换实例，暂时没有具体世界信息。',
    thumbnail_url: '',
    image_url: '',
    author_id: '',
    author_name: '',
    release_status: 'traveling',
    organization: '',
    tags: [],
    publication_date: '',
    created_at: '',
    updated_at: '',
  },
  [OFFLINE_WORLD_ID]: {
    id: OFFLINE_WORLD_ID,
    name: '离线',
    description: '这条历史记录的在线状态与位置字段不一致，按离线位置保留。',
    thumbnail_url: '',
    image_url: '',
    author_id: '',
    author_name: '',
    release_status: 'offline',
    organization: '',
    tags: [],
    publication_date: '',
    created_at: '',
    updated_at: '',
  },
  [HIDDEN_WORLD_ID]: {
    id: HIDDEN_WORLD_ID,
    name: '在线（位置隐藏）',
    description: '玩家在线，但 VRChat 没有提供可见的世界 ID。',
    thumbnail_url: '',
    image_url: '',
    author_id: '',
    author_name: '',
    release_status: 'hidden',
    organization: '',
    tags: [],
    publication_date: '',
    created_at: '',
    updated_at: '',
  },
};

export const isSpecialWorld = (worldId: string) => worldId in SPECIAL_WORLDS;

export const specialWorldInfo = (worldId: string) => SPECIAL_WORLDS[worldId];

const localDateKeyFromDate = (value: Date) => {
  const parts = new Intl.DateTimeFormat('en-CA', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(value);
  const part = (type: Intl.DateTimeFormatPartTypes) => parts.find((item) => item.type === type)?.value ?? '';
  return `${part('year')}-${part('month')}-${part('day')}`;
};

export const todayKey = () => localDateKeyFromDate(new Date());

export const offsetDateKey = (value: string, offset: number) => {
  const date = new Date(`${value || todayKey()}T12:00:00`);
  if (Number.isNaN(date.getTime())) return todayKey();
  date.setDate(date.getDate() + offset);
  return localDateKeyFromDate(date);
};

export const isDateKey = (value: string | null): value is string =>
  Boolean(value && /^\d{4}-\d{2}-\d{2}$/.test(value));

export const formatClock = (minute: number) => {
  const safe = Math.max(0, Math.min(1440, Math.round(minute)));
  const hour = Math.floor(safe / 60) % 24;
  return `${String(hour).padStart(2, '0')}:${String(safe % 60).padStart(2, '0')}`;
};

export const formatMinutes = (minutes: number) => {
  const rounded = Math.max(0, Math.round(minutes));
  if (rounded < 60) return `${rounded} 分钟`;
  const hours = Math.floor(rounded / 60);
  const rest = rounded % 60;
  return rest ? `${hours} 小时 ${rest} 分钟` : `${hours} 小时`;
};

export const formatSecondsCompact = (seconds: number) => {
  const minutes = Math.max(0, Math.round(seconds / 60));
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${hours}h ${rest}m` : `${hours}h`;
};

export const presenceColor = (status: string) => {
  const value = status.trim().toLowerCase();
  if (value === 'join me') return '#67d8f4';
  if (value === 'ask me') return '#f2a461';
  if (value === 'busy') return '#ff7588';
  if (value === 'offline') return '#11151b';
  return '#b6f36b';
};

const classifyWorld = (span: WorldSpan) => {
  if (span.world_id) return span.world_id;
  const location = span.location.trim().toLowerCase();
  if (location === 'private' || location.startsWith('private:')) return PRIVATE_WORLD_ID;
  if (location === 'traveling' || location === 'travel') return TRAVELING_WORLD_ID;
  if (location === 'offline' || span.status.trim().toLowerCase() === 'offline') return OFFLINE_WORLD_ID;
  return HIDDEN_WORLD_ID;
};

export const normalizeWorldAnalytics = (payload: WorldAnalytics): WorldAnalytics => {
  const discovered = new Set(payload.world_ids);
  const friends = payload.friends.map((friend) => ({
    ...friend,
    spans: friend.spans.map((span) => {
      const worldId = classifyWorld(span);
      discovered.add(worldId);
      return { ...span, world_id: worldId };
    }),
  }));
  return { ...payload, friends, world_ids: [...discovered] };
};

const hashWorldId = (worldId: string) => {
  let value = 2166136261;
  for (const character of worldId) {
    value ^= character.codePointAt(0) ?? 0;
    value = Math.imul(value, 16777619);
  }
  return value >>> 0;
};

export const createWorldColors = (worldIds: string[]) => {
  const result = new Map<string, string>([
    [PRIVATE_WORLD_ID, '#687381'],
    [TRAVELING_WORLD_ID, '#67d8f4'],
    [OFFLINE_WORLD_ID, '#05070a'],
    [HIDDEN_WORLD_ID, '#455264'],
  ]);
  [...new Set(worldIds)].filter((id) => !isSpecialWorld(id)).sort().forEach((id) => {
    const hash = hashWorldId(id);
    const hue = hash % 360;
    const saturation = 62 + ((hash >>> 9) % 19);
    const lightness = 56 + ((hash >>> 17) % 13);
    result.set(id, `hsl(${hue} ${saturation}% ${lightness}%)`);
  });
  return result;
};

export const worldName = (worldId: string, info?: WorldInfo) =>
  info?.name || specialWorldInfo(worldId)?.name || worldId;
