import { describe, expect, it } from 'vitest';

import { summarizeBackup } from './backup';

describe('backup preview', () => {
  it('summarizes compatible hosted and local backups without retaining their rows', () => {
    const result = summarizeBackup({
      format: 'vrchat-monitor-backup',
      version: 2,
      exported_at: '2026-08-27T12:00:00Z',
      friends: [{ id: 'usr_1' }, { id: 'usr_2' }],
      status_events: [{ id: 1 }],
      raw_fetches: [{ id: 1 }, { id: 2 }, { id: 3 }],
    });

    expect(result).toEqual({
      ok: true,
      preview: {
        format: 'vrchat-monitor-backup',
        exportedAt: '2026-08-27T12:00:00Z',
        friends: 2,
        events: 1,
        rawFetches: 3,
      },
    });
  });

  it('rejects a labelled backup when its normalized collections are missing', () => {
    expect(
      summarizeBackup({
        format: 'vrchat-monitor-hosted-backup',
        version: 1,
      }),
    ).toEqual({ ok: false });
  });

  it('does not render an invalid exported date', () => {
    const result = summarizeBackup({
      format: 'vrchat-monitor-hosted-backup',
      version: 1,
      exported_at: 'not-a-date',
      friends: [],
      status_events: [],
    });

    expect(result.ok && result.preview.exportedAt).toBe('');
  });

  it('accepts hosted v2 and rejects an unknown hosted backup version', () => {
    expect(
      summarizeBackup({
        format: 'vrchat-monitor-hosted-backup',
        version: 2,
        friends: [],
        status_events: [],
      }).ok,
    ).toBe(true);
    expect(
      summarizeBackup({
        format: 'vrchat-monitor-hosted-backup',
        version: 3,
        friends: [],
        status_events: [],
      }),
    ).toEqual({ ok: false });
  });
});
