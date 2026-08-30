export type BackupScope = 'full' | 'normalized';

export type BackupPreview = {
  format: string;
  exportedAt: string;
  friends: number;
  events: number;
  rawFetches: number;
  version?: number;
  scope?: BackupScope;
  friendAnnotations?: number;
  tags?: number;
  friendTags?: number;
  friendIdentityEvents?: number;
  friendTrackingEvents?: number;
  collectionSamples?: number;
  eventAnomalies?: number;
  tenantPreferences?: number;
};

export type BackupPreviewResult =
  | { ok: true; preview: BackupPreview }
  | { ok: false };

const formats = new Set(['vrchat-monitor-backup', 'vrchat-monitor-hosted-backup']);

const v3Collections = {
  friends: 'friends',
  status_events: 'events',
  friend_annotations: 'friendAnnotations',
  tags: 'tags',
  friend_tags: 'friendTags',
  friend_identity_events: 'friendIdentityEvents',
  friend_tracking_events: 'friendTrackingEvents',
  collection_samples: 'collectionSamples',
  event_anomalies: 'eventAnomalies',
  tenant_preferences: 'tenantPreferences',
  raw_fetches: 'rawFetches',
} as const;

export function summarizeBackup(value: unknown): BackupPreviewResult {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return { ok: false };
  const payload = value as Record<string, unknown>;
  const isV3 = payload.format === 'vrchat-monitor-hosted-backup' && payload.version === 3;
  if (isV3) {
    if (payload.scope !== 'full' && payload.scope !== 'normalized') return { ok: false };
    if (Object.keys(v3Collections).some((field) => !Array.isArray(payload[field]))) {
      return { ok: false };
    }
    if (payload.scope === 'normalized' && (payload.raw_fetches as unknown[]).length > 0) {
      return { ok: false };
    }
    if (
      typeof payload.exported_at !== 'string'
      || Number.isNaN(Date.parse(payload.exported_at))
    ) {
      return { ok: false };
    }
    const exportedAt = payload.exported_at;
    const counts = Object.fromEntries(
      Object.entries(v3Collections).map(([field, previewField]) => [
        previewField,
        (payload[field] as unknown[]).length,
      ]),
    ) as Pick<
      BackupPreview,
      | 'friends'
      | 'events'
      | 'friendAnnotations'
      | 'tags'
      | 'friendTags'
      | 'friendIdentityEvents'
      | 'friendTrackingEvents'
      | 'collectionSamples'
      | 'eventAnomalies'
      | 'tenantPreferences'
      | 'rawFetches'
    >;
    return {
      ok: true,
      preview: {
        format: 'vrchat-monitor-hosted-backup',
        version: 3,
        scope: payload.scope,
        exportedAt,
        ...counts,
      },
    };
  }
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
