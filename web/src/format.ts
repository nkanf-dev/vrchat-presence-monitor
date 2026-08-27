import type { Friend, PresenceEvent } from './api';

const numberFormatter = new Intl.NumberFormat('zh-CN');
const dateFormatter = new Intl.DateTimeFormat('zh-CN', {
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
});

export const statusLabels: Record<string, string> = {
  active: '游戏中',
  'join me': '可加入',
  'ask me': '先询问',
  busy: '忙碌',
  mobile: '移动端在线',
  website: '网页在线',
  offline: '离线',
};

export const formatNumber = (value: number) => numberFormatter.format(value);

export const formatDateTime = (value?: string | null) => {
  if (!value) return '尚未同步';
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? '时间未知' : dateFormatter.format(date);
};

export const statusLabel = (status: string) => statusLabels[status.toLowerCase()] ?? '状态未知';

export const statusTone = (status: string) => {
  const value = status.toLowerCase();
  if (value === 'offline') return 'offline';
  if (value === 'busy' || value === 'ask me') return 'warm';
  if (value === 'join me' || value === 'mobile' || value === 'website') return 'cool';
  return 'online';
};

export const platformLabel = (platform: string) => {
  const value = platform.toLowerCase();
  if (value === 'standalonewindows') return '电脑端';
  if (value === 'android') return 'Android / 一体机';
  if (value === 'ios') return 'iPhone / iPad';
  if (value === 'web') return '网页';
  return platform ? '其他设备' : '设备未知';
};

export const locationLabel = (location: string, status: string) => {
  const raw = location.trim();
  const value = location.trim().toLowerCase();
  if (status.toLowerCase() === 'offline') return '离线';
  if (!value || value === 'offline') return '位置不可见';
  if (value === 'private') return '私人位置';
  if (value === 'traveling') return '正在切换世界';
  if (value === 'online') return '在线（位置隐藏）';
  return raw;
};

export const friendName = (friend: Friend) =>
  friend.display_name || friend.username || friend.id || '未知玩家';

export const eventName = (event: PresenceEvent) =>
  event.display_name || event.username || event.friend_id || '未知玩家';

export const isOnline = (friend: Friend) => friend.status.toLowerCase() !== 'offline';

export const avatarSource = (friend: Friend) => friend.avatar_image_url || friend.avatar_url || '';

export const initials = (name: string) => Array.from(name.trim()).slice(0, 1).join('').toUpperCase() || '?';

export const parseBioLinks = (value: Friend['bio_links']) => {
  let links: unknown = value;
  if (typeof value === 'string') {
    try {
      links = JSON.parse(value);
    } catch {
      return [];
    }
  }
  if (!Array.isArray(links)) return [];
  return links
    .filter((item): item is string => typeof item === 'string')
    .filter((item) => /^https?:\/\//i.test(item))
    .slice(0, 8);
};
