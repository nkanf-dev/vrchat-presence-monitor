export type BackupPreview = {
  format: string;
  exportedAt: string;
  friends: number;
  events: number;
  rawFetches: number;
};

export type BackupPreviewResult =
  | { ok: true; preview: BackupPreview }
  | { ok: false };

const formats = new Set(['vrchat-monitor-backup', 'vrchat-monitor-hosted-backup']);

export function summarizeBackup(value: unknown): BackupPreviewResult {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return { ok: false };
  const payload = value as Record<string, unknown>;
  const supportedVersion =
    (payload.format === 'vrchat-monitor-hosted-backup' &&
      (payload.version === 1 || payload.version === 2)) ||
    (payload.format === 'vrchat-monitor-backup' && (payload.version === 1 || payload.version === 2));
  if (typeof payload.format !== 'string' || !formats.has(payload.format) || !supportedVersion) {
    return { ok: false };
  }
  if (!Array.isArray(payload.friends) || !Array.isArray(payload.status_events)) return { ok: false };

  const exportedAt =
    typeof payload.exported_at === 'string' && !Number.isNaN(Date.parse(payload.exported_at))
      ? payload.exported_at
      : '';
  return {
    ok: true,
    preview: {
      format: payload.format,
      exportedAt,
      friends: payload.friends.length,
      events: payload.status_events.length,
      rawFetches: Array.isArray(payload.raw_fetches) ? payload.raw_fetches.length : 0,
    },
  };
}
