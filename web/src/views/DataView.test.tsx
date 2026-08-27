import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { DataView } from './DataView';

class WorkerStub {
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: ((event: ErrorEvent) => void) | null = null;
  onmessageerror: ((event: MessageEvent) => void) | null = null;
  terminate = vi.fn();

  postMessage() {
    queueMicrotask(() => {
      this.onmessage?.(
        new MessageEvent('message', {
          data: {
            ok: true,
            preview: {
              format: 'vrchat-monitor-hosted-backup',
              exportedAt: '2026-08-27T12:00:00Z',
              friends: 2,
              events: 3,
              rawFetches: 0,
            },
          },
        }),
      );
    });
  }
}

describe('DataView import flow', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('previews in a worker and prevents closing while an import is pending', async () => {
    vi.stubGlobal('Worker', WorkerStub);
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(() => undefined)));
    const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
    const user = userEvent.setup();
    render(
      <QueryClientProvider client={client}>
        <DataView />
      </QueryClientProvider>,
    );

    const input = document.querySelector<HTMLInputElement>('#backup-file');
    expect(input).not.toBeNull();
    await user.upload(
      input!,
      new File(['{}'], 'presence-backup.json', { type: 'application/json' }),
    );

    const dialog = await screen.findByRole('dialog', { name: '确认导入这份备份？' });
    expect(screen.getByText('2')).toBeVisible();
    expect(screen.getByText('3')).toBeVisible();

    await user.click(screen.getByRole('button', { name: '确认合并' }));
    expect(dialog).toHaveAttribute('aria-busy', 'true');
    expect(screen.getByRole('button', { name: '关闭导入预览' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '取消' })).toBeDisabled();
  });
});
