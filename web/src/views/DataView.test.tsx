import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { DataView } from './DataView';

class WorkerStub {
  static messages: unknown[] = [];

  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: ((event: ErrorEvent) => void) | null = null;
  onmessageerror: ((event: MessageEvent) => void) | null = null;
  terminate = vi.fn();

  postMessage(message: unknown) {
    WorkerStub.messages.push(message);
    queueMicrotask(() => {
      this.onmessage?.(
        new MessageEvent('message', {
          data: {
            ok: true,
            upload: new File(['{}'], 'normalized.json', { type: 'application/json' }),
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
  afterEach(() => {
    WorkerStub.messages = [];
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('previews in a worker and prevents closing while an import is pending', async () => {
    vi.stubGlobal('Worker', WorkerStub);
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      if (String(input).includes('/v1/capabilities')) {
        return Promise.resolve(new Response(JSON.stringify({
          max_import_bytes: 32 * 1024 * 1024,
          max_import_expanded_bytes: 64 * 1024 * 1024,
          max_source_expanded_bytes: 256 * 1024 * 1024,
        }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      }
      return new Promise<Response>(() => undefined);
    }));
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    const user = userEvent.setup();
    render(
      <QueryClientProvider client={client}>
        <DataView />
      </QueryClientProvider>,
    );

    await screen.findByRole('button', { name: '选择 JSON / JSON.gz' });
    const input = document.querySelector<HTMLInputElement>('#backup-file');
    expect(input).not.toBeNull();
    expect(input).toHaveAttribute('hidden');
    await user.upload(
      input!,
      new File(['{}'], 'presence-backup.json', { type: 'application/json' }),
    );

    const dialog = await screen.findByRole('dialog', { name: '确认导入这份备份？' });
    expect(WorkerStub.messages[0]).toMatchObject({
      maximum: 32 * 1024 * 1024,
      maximumServerExpanded: 64 * 1024 * 1024,
      maximumSourceExpanded: 256 * 1024 * 1024,
    });
    expect(screen.getByText('2')).toBeVisible();
    expect(screen.getByText('3')).toBeVisible();

    await user.click(screen.getByRole('button', { name: '确认合并' }));
    expect(dialog).toHaveAttribute('aria-busy', 'true');
    expect(screen.getByRole('button', { name: '关闭导入预览' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '取消' })).toBeDisabled();
  });

  it('offers an in-page retry when capacity discovery fails', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ error: 'temporary failure' }), {
          status: 503,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({
          max_import_bytes: 32 * 1024 * 1024,
          max_import_expanded_bytes: 64 * 1024 * 1024,
          max_source_expanded_bytes: 256 * 1024 * 1024,
        }), { status: 200, headers: { 'Content-Type': 'application/json' } }),
      );
    vi.stubGlobal('fetch', fetchMock);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const user = userEvent.setup();
    render(
      <QueryClientProvider client={client}>
        <DataView />
      </QueryClientProvider>,
    );

    await user.click(await screen.findByRole('button', { name: '重新读取容量' }));

    expect(await screen.findByRole('button', { name: '选择 JSON / JSON.gz' })).toBeEnabled();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('does not claim an export succeeded when the server rejects it', async () => {
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      if (String(input).includes('/v1/capabilities')) {
        return Promise.resolve(new Response(JSON.stringify({
          max_import_bytes: 32 * 1024 * 1024,
          max_import_expanded_bytes: 64 * 1024 * 1024,
          max_source_expanded_bytes: 256 * 1024 * 1024,
        }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      }
      return Promise.resolve(new Response(JSON.stringify({ error: '备份暂时不可用' }), {
        status: 503,
        headers: { 'Content-Type': 'application/json' },
      }));
    }));
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    const user = userEvent.setup();
    render(
      <QueryClientProvider client={client}>
        <DataView />
      </QueryClientProvider>,
    );

    await user.click(screen.getByRole('button', { name: '下载备份' }));

    expect(await screen.findByText('备份暂时不可用')).toBeVisible();
    expect(screen.queryByText(/备份已下载/)).not.toBeInTheDocument();
  });

  it('announces success only after creating a local download', async () => {
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);
    const createObjectURL = vi.fn(() => 'blob:backup');
    const revokeObjectURL = vi.fn();
    vi.stubGlobal('URL', { createObjectURL, revokeObjectURL });
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      if (String(input).includes('/v1/capabilities')) {
        return Promise.resolve(new Response(JSON.stringify({
          max_import_bytes: 32 * 1024 * 1024,
          max_import_expanded_bytes: 64 * 1024 * 1024,
          max_source_expanded_bytes: 256 * 1024 * 1024,
        }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      }
      return Promise.resolve(new Response('{"format":"vrchat-monitor-hosted-backup"}', {
        status: 200,
        headers: {
          'Content-Type': 'application/json',
          'Content-Disposition': 'attachment; filename="my-backup.json"',
        },
      }));
    }));
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    const user = userEvent.setup();
    const rendered = render(
      <QueryClientProvider client={client}>
        <DataView />
      </QueryClientProvider>,
    );

    await user.click(screen.getByRole('button', { name: '下载备份' }));

    expect(await screen.findByText(/备份已生成并交给浏览器/)).toBeVisible();
    expect(screen.getByRole('link', { name: '再次保存' })).toHaveAttribute(
      'download',
      'my-backup.json',
    );
    expect(click).toHaveBeenCalledTimes(1);
    expect(createObjectURL).toHaveBeenCalledTimes(1);
    expect(revokeObjectURL).not.toHaveBeenCalled();

    rendered.unmount();
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:backup');
  });

  it('restores keyboard focus after cancelling an import preview', async () => {
    vi.stubGlobal('Worker', WorkerStub);
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      if (String(input).includes('/v1/capabilities')) {
        return Promise.resolve(new Response(JSON.stringify({
          max_import_bytes: 32 * 1024 * 1024,
          max_import_expanded_bytes: 64 * 1024 * 1024,
          max_source_expanded_bytes: 256 * 1024 * 1024,
        }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      }
      return Promise.resolve(new Response('{}', { status: 200 }));
    }));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const user = userEvent.setup();
    render(
      <QueryClientProvider client={client}>
        <DataView />
      </QueryClientProvider>,
    );

    const chooseButton = await screen.findByRole('button', { name: '选择 JSON / JSON.gz' });
    const input = document.querySelector<HTMLInputElement>('#backup-file');
    await user.upload(
      input!,
      new File(['{}'], 'presence-backup.json', { type: 'application/json' }),
    );
    await screen.findByRole('dialog', { name: '确认导入这份备份？' });
    await user.click(screen.getByRole('button', { name: '取消' }));

    await waitFor(() => expect(chooseButton).toHaveFocus());
  });
});
