export type LocationKind = 'world' | 'private' | 'hidden' | 'traveling' | 'offline' | 'gap';

export const ratioLabel = (ratio: number | null) =>
  ratio === null ? '—' : `${Math.round(ratio * 100)}%`;

export const locationLabel = (kind: LocationKind, worldName = '') => ({
  world: worldName || '世界',
  private: '私人位置',
  hidden: '位置隐藏',
  traveling: '切换世界中',
  offline: '离线',
  gap: '记录中断',
}[kind]);

export const coverageLabel = (observedMinutes: number, expectedMinutes: number) => {
  if (expectedMinutes <= 0 || observedMinutes <= 0) return '这个时段还没有记录';
  const ratio = Math.min(1, observedMinutes / expectedMinutes);
  if (ratio >= 0.9) return '记录完整';
  if (ratio >= 0.5) return '记录较多';
  return '记录较少';
};

export const activityDetail = (value: {
  ratio: number | null;
  online_minutes: number;
  observed_minutes: number;
  eligible_minutes: number;
}) => {
  if (value.observed_minutes <= 0) return '这个时段还没有记录';
  if (value.ratio === null) return `已记录 ${Math.round(value.observed_minutes)} 分钟，暂不足以计算`;
  return `在线 ${Math.round(value.online_minutes)} / ${Math.round(value.observed_minutes)} 分钟`;
};

export const stableWorldColor = (worldId: string) => {
  let hash = 2166136261;
  for (const character of worldId) {
    hash ^= character.codePointAt(0) ?? 0;
    hash = Math.imul(hash, 16777619);
  }
  const hue = Math.abs(hash) % 360;
  const saturation = 58 + (Math.abs(hash >>> 8) % 18);
  const lightness = 48 + (Math.abs(hash >>> 16) % 12);
  return `hsl(${hue} ${saturation}% ${lightness}%)`;
};
