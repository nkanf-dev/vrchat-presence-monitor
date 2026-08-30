import { describe, expect, it } from 'vitest';
import { gzipSync } from 'node:zlib';

import { normalizeBackupFile } from './backup-normalizer';

function streamedFile(
  payload: unknown,
  options: { name?: string; reportedSize?: number; chunkSize?: number; rawText?: string } = {},
) {
  const bytes = new TextEncoder().encode(options.rawText ?? JSON.stringify(payload));
  const chunkSize = options.chunkSize ?? 97;
  return {
    name: options.name ?? 'backup.json',
    size: options.reportedSize ?? bytes.byteLength,
    slice(start = 0, end = bytes.byteLength) {
      const part = bytes.slice(start, end);
      return {
        arrayBuffer: async () => part.buffer.slice(
          part.byteOffset,
          part.byteOffset + part.byteLength,
        ),
      } as Blob;
    },
    stream() {
      return new ReadableStream<Uint8Array>({
        start(controller) {
          for (let offset = 0; offset < bytes.byteLength; offset += chunkSize) {
            controller.enqueue(bytes.slice(offset, offset + chunkSize));
          }
          controller.close();
        },
      });
    },
  };
}

async function fileText(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error ?? new Error('file read failed'));
    reader.onload = () => resolve(String(reader.result ?? ''));
    reader.readAsText(file);
  });
}

function compressedBrowserFile(payload: unknown, name = 'backup.json.gz') {
  const bytes = new Uint8Array(gzipSync(JSON.stringify(payload)));
  const file = new File([bytes], name, { type: 'application/gzip' });
  Object.defineProperties(file, {
    slice: {
      value(start = 0, end = bytes.byteLength) {
        const part = bytes.slice(start, end);
        return {
          arrayBuffer: async () => part.buffer.slice(
            part.byteOffset,
            part.byteOffset + part.byteLength,
          ),
        };
      },
    },
    stream: {
      value() {
        return new ReadableStream<Uint8Array>({
          start(controller) {
            controller.enqueue(bytes);
            controller.close();
          },
        });
      },
    },
  });
  return file;
}

describe('streaming backup normalization', () => {
  it('previews every v3 collection and uploads the original gzip unchanged', async () => {
    const source = {
      format: 'vrchat-monitor-hosted-backup',
      version: 3,
      scope: 'full',
      exported_at: '2026-08-29T12:00:00+00:00',
      friends: [{ id: 'usr_1', display_name: 'Alice' }],
      status_events: [{ client_event_id: 'event_1', friend_id: 'usr_1' }],
      friend_annotations: [{ friend_id: 'usr_1', revision: 'revision_1' }],
      tags: [{ id: 'tag_1' }, { id: 'tag_2' }],
      friend_tags: [{ friend_id: 'usr_1', tag_id: 'tag_1' }],
      friend_identity_events: [{ event_id: 'identity_1', friend_id: 'usr_1' }],
      friend_tracking_events: [{ event_id: 'tracking_1', friend_id: 'usr_1' }],
      collection_samples: [{ sample_id: 'sample_1' }, { sample_id: 'sample_2' }],
      event_anomalies: [{ anomaly_id: 'anomaly_1' }],
      tenant_preferences: [{ timezone: 'Asia/Shanghai' }],
      raw_fetches: [{ client_fetch_id: 'fetch_1', body_b64: 'z'.repeat(2 * 1024 * 1024) }],
    };
    const compressed = compressedBrowserFile(source, 'complete-v3.json.gz');

    const result = await normalizeBackupFile(compressed, 4 * 1024 * 1024, 8 * 1024 * 1024);

    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.preview).toEqual({
      format: 'vrchat-monitor-hosted-backup',
      version: 3,
      scope: 'full',
      exportedAt: '2026-08-29T12:00:00+00:00',
      friends: 1,
      events: 1,
      friendAnnotations: 1,
      tags: 2,
      friendTags: 1,
      friendIdentityEvents: 1,
      friendTrackingEvents: 1,
      collectionSamples: 2,
      eventAnomalies: 1,
      tenantPreferences: 1,
      rawFetches: 1,
    });
    expect(result.upload).toBe(compressed);
    expect(result.upload.name).toBe(compressed.name);
    expect(result.upload.size).toBe(compressed.size);
  });

  it('rejects a v3 file with a missing ledger array', async () => {
    const source = {
      format: 'vrchat-monitor-hosted-backup',
      version: 3,
      scope: 'normalized',
      friends: [],
      status_events: [],
    };

    await expect(
      normalizeBackupFile(streamedFile(source), 10_000, 10_000),
    ).resolves.toMatchObject({ ok: false, reason: 'invalid' });
  });

  it('drops raw fetches without retaining the full source document', async () => {
    const source = {
      format: 'vrchat-monitor-backup',
      version: 2,
      exported_at: '2026-08-27T12:00:00+00:00',
      friends: [{ id: 'usr_1', display_name: 'Alice' }],
      status_events: [
        {
          client_event_id: 'event_1',
          friend_id: 'usr_1',
          occurred_at: '2026-08-27T12:00:00+00:00',
        },
      ],
      raw_fetches: [
        { client_fetch_id: 'fetch_1', body_b64: 'x'.repeat(256 * 1024) },
        { client_fetch_id: 'fetch_2', body_b64: 'y'.repeat(256 * 1024) },
      ],
    };

    const result = await normalizeBackupFile(
      streamedFile(source),
      1024 * 1024,
      1024 * 1024,
    );

    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.preview).toMatchObject({ friends: 1, events: 1, rawFetches: 2 });
    expect(result.upload.size).toBeLessThan(1024);
    const normalized = JSON.parse(await fileText(result.upload));
    expect(normalized.raw_fetches).toBeUndefined();
    expect(normalized.friends).toHaveLength(1);
    expect(normalized.status_events).toHaveLength(1);
  });

  it('accepts a local source larger than the server request after raw redaction', async () => {
    const source = {
      format: 'vrchat-monitor-backup',
      version: 2,
      friends: [{ id: 'usr_1', display_name: 'Alice' }],
      status_events: [],
      raw_fetches: [{ client_fetch_id: 'fetch_1', body_b64: 'x'.repeat(4096) }],
    };

    const result = await normalizeBackupFile(
      streamedFile(source, { reportedSize: 4096 }),
      1024,
      8192,
      2048,
    );

    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.preview.rawFetches).toBe(1);
    expect(result.upload.size).toBeLessThan(1024);
  });

  it('keeps a restorable gzip when normalized JSON exceeds the request limit', async () => {
    const source = {
      format: 'vrchat-monitor-hosted-backup',
      version: 2,
      exported_at: '2026-08-27T12:00:00+00:00',
      friends: [{ id: 'usr_1', display_name: 'Alice', bio: 'x'.repeat(4096) }],
      status_events: [],
    };
    const compressed = compressedBrowserFile(source);
    expect(compressed.size).toBeLessThan(512);

    const result = await normalizeBackupFile(compressed, 512, 8192, 8192);

    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.upload.name).toBe('backup.json.gz');
    expect(result.upload.type).toBe('application/gzip');
    expect(result.upload.size).toBe(compressed.size);
  });

  it('rejects malformed shapes and every relevant size boundary', async () => {
    const base = {
      format: 'vrchat-monitor-hosted-backup',
      version: 2,
      exported_at: '2026-08-27T12:00:00+00:00',
      friends: [{ id: 'usr_1', bio: 'b'.repeat(2048) }],
      status_events: [],
    };

    await expect(
      normalizeBackupFile(streamedFile(base), 100, 1000),
    ).resolves.toMatchObject({ ok: false, reason: 'input-too-large' });
    await expect(
      normalizeBackupFile(streamedFile(base, { reportedSize: 50 }), 500, 10_000),
    ).resolves.toMatchObject({ ok: false, reason: 'normalized-too-large' });
    await expect(
      normalizeBackupFile(streamedFile(base, { reportedSize: 50 }), 500, 600),
    ).resolves.toMatchObject({ ok: false, reason: 'expanded-too-large' });
    await expect(
      normalizeBackupFile(
        streamedFile({ format: base.format, version: 2, friends: [] }),
        10_000,
        10_000,
      ),
    ).resolves.toMatchObject({ ok: false, reason: 'invalid' });
  });

  it('rejects duplicate top-level keys instead of merging their arrays', async () => {
    const duplicated = [
      '{"format":"vrchat-monitor-backup","version":2,',
      '"friends":[{"id":"usr_first"}],',
      '"\\u0066riends":[{"id":"usr_second"}],',
      '"status_events":[]}',
    ].join('');

    await expect(
      normalizeBackupFile(
        streamedFile(null, { rawText: duplicated, chunkSize: 11 }),
        10_000,
        10_000,
      ),
    ).resolves.toMatchObject({ ok: false, reason: 'invalid' });
  });

  it('rejects duplicate keys inside retained player and event objects', async () => {
    const duplicated = [
      '{"format":"vrchat-monitor-backup","version":2,',
      '"friends":[{"id":"usr_first","\\u0069d":"usr_second"}],',
      '"status_events":[]}',
    ].join('');

    await expect(
      normalizeBackupFile(
        streamedFile(null, { rawText: duplicated, chunkSize: 7 }),
        10_000,
        10_000,
      ),
    ).resolves.toMatchObject({ ok: false, reason: 'invalid' });
  });

  it('redacts a giant raw body before the JSON parser can materialize it', async () => {
    const rawBody = 'z'.repeat(32 * 1024 * 1024);
    const source = {
      format: 'vrchat-monitor-backup',
      version: 2,
      friends: [{ id: 'usr_1', display_name: 'Alice' }],
      status_events: [],
      raw_fetches: [{ client_fetch_id: 'fetch_large', body_b64: rawBody }],
    };
    const result = await normalizeBackupFile(
      streamedFile(source, { chunkSize: 64 * 1024 }),
      40 * 1024 * 1024,
      40 * 1024 * 1024,
    );

    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.preview.rawFetches).toBe(1);
    expect(result.upload.size).toBeLessThan(1024);
  });

  it('streams a large gzip source and uploads only normalized history', async () => {
    const source = {
      format: 'vrchat-monitor-backup',
      version: 2,
      friends: [{ id: 'usr_1', display_name: 'Alice' }],
      status_events: [],
      raw_fetches: [
        { client_fetch_id: 'fetch_large_gzip', body_b64: 'z'.repeat(32 * 1024 * 1024) },
      ],
    };
    const compressed = compressedBrowserFile(source, 'large-backup.json.gz');
    const result = await normalizeBackupFile(
      compressed,
      32 * 1024 * 1024,
      80 * 1024 * 1024,
      64 * 1024 * 1024,
    );

    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.preview.rawFetches).toBe(1);
    expect(result.upload.size).toBeLessThan(1024);
    expect(JSON.parse(await fileText(result.upload))).toMatchObject({
      friends: [{ id: 'usr_1' }],
      status_events: [],
    });
  });
});
