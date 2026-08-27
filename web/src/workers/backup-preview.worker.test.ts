import { gzipSync } from 'node:zlib';
import { afterEach, describe, expect, it, vi } from 'vitest';

function compressedBrowserFile(payload: unknown) {
  const bytes = new Uint8Array(gzipSync(JSON.stringify(payload)));
  const file = new File([bytes], 'large-backup.json.gz', { type: 'application/gzip' });
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

describe('backup preview worker entry point', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  it('streams a large gzip through the real worker handler and returns a small upload', async () => {
    const postMessage = vi.fn();
    const scope: {
      postMessage: typeof postMessage;
      onmessage?: (event: MessageEvent) => Promise<void>;
    } = { postMessage };
    vi.stubGlobal('self', scope);
    await import('./backup-preview.worker');

    const file = compressedBrowserFile({
      format: 'vrchat-monitor-backup',
      version: 2,
      friends: [{ id: 'usr_1', display_name: 'Alice' }],
      status_events: [],
      raw_fetches: [
        { client_fetch_id: 'fetch_large', body_b64: 'z'.repeat(8 * 1024 * 1024) },
      ],
    });
    await scope.onmessage?.(new MessageEvent('message', {
      data: {
        file,
        maximum: 16 * 1024 * 1024,
        maximumSourceExpanded: 32 * 1024 * 1024,
        maximumServerExpanded: 16 * 1024 * 1024,
      },
    }));

    expect(postMessage).toHaveBeenCalledTimes(1);
    const result = postMessage.mock.calls[0]?.[0];
    expect(result).toMatchObject({
      ok: true,
      preview: { friends: 1, events: 0, rawFetches: 1 },
    });
    expect(result.upload.size).toBeLessThan(1024);
  });
});
