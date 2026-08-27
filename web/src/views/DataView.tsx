import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Database, Download, FileWarning, ShieldCheck, Upload, X } from 'lucide-react';
import { ChangeEvent, useEffect, useRef, useState } from 'react';

import { ApiError, importBackupFile } from '../api';
import type { BackupPreview, BackupPreviewResult } from '../backup';

const MAX_FILE_SIZE = 64 * 1024 * 1024;

type Preview = {
  name: string;
  size: number;
  file: File;
} & BackupPreview;

const abortError = () => {
  const error = new Error('backup inspection aborted');
  error.name = 'AbortError';
  return error;
};

const inspectBackup = (file: File, signal: AbortSignal) =>
  new Promise<BackupPreviewResult>((resolve, reject) => {
    const worker = new Worker(new URL('../workers/backup-preview.worker.ts', import.meta.url), {
      type: 'module',
    });
    let settled = false;

    const settle = (callback: () => void) => {
      if (settled) return;
      settled = true;
      signal.removeEventListener('abort', abort);
      worker.terminate();
      callback();
    };
    const abort = () => settle(() => reject(abortError()));
    if (signal.aborted) {
      abort();
      return;
    }
    signal.addEventListener('abort', abort, { once: true });
    worker.onmessage = (event: MessageEvent<BackupPreviewResult>) => {
      settle(() => resolve(event.data));
    };
    worker.onerror = (event) => {
      event.preventDefault();
      settle(() => reject(new Error('worker failed')));
    };
    worker.onmessageerror = () => {
      settle(() => reject(new Error('worker response failed')));
    };
    try {
      worker.postMessage({ file, maximum: MAX_FILE_SIZE });
    } catch (error) {
      settle(() => reject(error instanceof Error ? error : new Error('worker request failed')));
    }
  });

export function DataView() {
  const queryClient = useQueryClient();
  const fileInput = useRef<HTMLInputElement>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const inspection = useRef<AbortController | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [inspecting, setInspecting] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    const element = dialog.current;
    if (preview && element && !element.open) element.showModal();
  }, [preview]);

  useEffect(
    () => () => {
      const current = inspection.current;
      inspection.current = null;
      current?.abort();
    },
    [],
  );

  const importMutation = useMutation({
    mutationFn: (file: File) => importBackupFile(file),
    onSuccess: async (result) => {
      dialog.current?.close();
      setMessage(
        `导入完成：更新 ${result.imported.friends} 位玩家，新增 ${result.imported.events} 条历史记录。`,
      );
      setError('');
      setPreview(null);
      if (fileInput.current) fileInput.current.value = '';
      await queryClient.invalidateQueries();
    },
    onError: (reason) => {
      setError(reason instanceof ApiError ? reason.message : '导入失败，请检查备份后重试');
    },
  });

  const closePreview = () => {
    if (importMutation.isPending) return;
    dialog.current?.close();
    setPreview(null);
    setError('');
    if (fileInput.current) fileInput.current.value = '';
  };

  const chooseFile = async (event: ChangeEvent<HTMLInputElement>) => {
    const input = event.currentTarget;
    inspection.current?.abort();
    setError('');
    setMessage('');
    setPreview(null);
    const file = input.files?.[0];
    if (!file) return;
    if (file.size > MAX_FILE_SIZE) {
      setError('备份文件超过 64 MB，未读取也未上传。');
      input.value = '';
      return;
    }
    const controller = new AbortController();
    inspection.current = controller;
    setInspecting(true);
    try {
      const inspected = await inspectBackup(file, controller.signal);
      if (!inspected.ok) {
        setError('这不是兼容的 Presence Monitor v1 备份，未上传任何内容。');
        input.value = '';
        return;
      }
      setPreview({
        name: file.name,
        size: file.size,
        file,
        ...inspected.preview,
      });
    } catch (reason) {
      if (reason instanceof Error && reason.name === 'AbortError') return;
      setError('浏览器未能完成本地检查。请重新选择文件，或换一个现代浏览器后重试。');
      input.value = '';
    } finally {
      if (inspection.current === controller) {
        inspection.current = null;
        setInspecting(false);
      }
    }
  };

  return (
    <>
      <header className="page-heading">
        <div>
          <p className="kicker">Data ownership</p>
          <h1 tabIndex={-1}>数据与备份</h1>
          <p>导出自己的规范化记录，或在确认预览后合并兼容备份。导入不会覆盖更新的数据。</p>
        </div>
      </header>

      <div className="data-card-grid">
        <article className="action-card">
          <span className="action-icon" aria-hidden="true">
            <Download size={22} />
          </span>
          <div>
            <h2>导出备份</h2>
            <p>包含玩家快照与状态历史，不包含访问码、浏览器会话或采集凭据。</p>
          </div>
          <a
            className="button button-primary"
            href="/v1/export.json"
            download
            onClick={() => {
              setMessage('备份下载已开始。请像保护私人聊天记录一样妥善保存。');
              setError('');
            }}
          >
            <Download size={17} aria-hidden="true" />
            下载 JSON
          </a>
        </article>

        <article className="action-card">
          <span className="action-icon" aria-hidden="true">
            <Upload size={22} />
          </span>
          <div>
            <h2>导入备份</h2>
            <p>先在浏览器本地检查格式、大小和数量，确认后才会发送到服务器。</p>
          </div>
          <input
            ref={fileInput}
            className="sr-only"
            type="file"
            accept="application/json,.json"
            onChange={(event) => void chooseFile(event)}
            id="backup-file"
          />
          <button
            className="button button-secondary"
            onClick={() => fileInput.current?.click()}
            disabled={inspecting || importMutation.isPending}
            aria-describedby="backup-import-help"
          >
            <Upload size={17} aria-hidden="true" />
            {inspecting ? '正在本地检查…' : '选择 JSON'}
          </button>
        </article>
      </div>

      <span className="sr-only" id="backup-import-help">
        文件会先在此设备本地检查；确认预览前不会上传。
      </span>

      {inspecting && (
        <p className="operation-message pending" role="status" aria-live="polite">
          正在本地检查备份格式和记录数量…
        </p>
      )}

      {(message || error) && (
        <p
          className={error ? 'operation-message error' : 'operation-message success'}
          role={error ? 'alert' : 'status'}
          aria-live={error ? 'assertive' : 'polite'}
        >
          {error || message}
        </p>
      )}

      <section className="panel security-panel" aria-labelledby="data-boundary-title">
        <header className="panel-heading">
          <div>
            <p className="kicker">What stays where</p>
            <h2 id="data-boundary-title">数据边界</h2>
          </div>
        </header>
        <div className="boundary-grid">
          <div>
            <ShieldCheck size={21} aria-hidden="true" />
            <h3>浏览器登录</h3>
            <p>保存在 HttpOnly Cookie 中，页面代码无法读取。退出只撤销当前设备的会话。</p>
          </div>
          <div>
            <Database size={21} aria-hidden="true" />
            <h3>监控数据</h3>
            <p>由这个自托管实例保存，并按租户隔离。服务器管理员仍然掌握服务器与数据库。</p>
          </div>
          <div>
            <FileWarning size={21} aria-hidden="true" />
            <h3>备份文件</h3>
            <p>可能包含好友、简介和活动位置。下载后由你负责保存与删除，不应提交到公开仓库。</p>
          </div>
        </div>
      </section>

      <dialog
        className="dialog import-dialog"
        ref={dialog}
        aria-labelledby="import-dialog-title"
        aria-busy={importMutation.isPending}
        onCancel={(event) => {
          if (importMutation.isPending) event.preventDefault();
          else closePreview();
        }}
        onClose={() => {
          if (!importMutation.isPending) {
            setPreview(null);
            setError('');
            if (fileInput.current) fileInput.current.value = '';
          }
        }}
      >
        {preview && (
          <div className="dialog-scroll">
            <button
              className="icon-button dialog-close"
              onClick={closePreview}
              aria-label="关闭导入预览"
              disabled={importMutation.isPending}
            >
              <X size={20} aria-hidden="true" />
            </button>
            <p className="kicker">Import preview</p>
            <h2 id="import-dialog-title">确认导入这份备份？</h2>
            <p className="dialog-lead">尚未向服务器写入任何内容。确认后会按时间合并，旧快照不会覆盖新数据。</p>
            <dl className="import-facts">
              <div>
                <dt>文件</dt>
                <dd>{preview.name}</dd>
              </div>
              <div>
                <dt>玩家</dt>
                <dd>{preview.friends.toLocaleString('zh-CN')}</dd>
              </div>
              <div>
                <dt>历史记录</dt>
                <dd>{preview.events.toLocaleString('zh-CN')}</dd>
              </div>
              <div>
                <dt>备份时间</dt>
                <dd>{preview.exportedAt ? new Date(preview.exportedAt).toLocaleString('zh-CN') : '未知'}</dd>
              </div>
            </dl>
            {preview.rawFetches > 0 && (
              <p className="privacy-warning">
                <FileWarning size={18} aria-hidden="true" />
                文件含 {preview.rawFetches.toLocaleString('zh-CN')} 条原始 API 响应；Hosted 只导入兼容的规范化数据。
              </p>
            )}
            {error && <p className="form-error" role="alert">{error}</p>}
            <div className="dialog-actions">
              <button className="button button-secondary" onClick={closePreview} disabled={importMutation.isPending}>
                取消
              </button>
              <button
                className="button button-primary"
                onClick={() => importMutation.mutate(preview.file)}
                disabled={importMutation.isPending}
              >
                {importMutation.isPending ? '正在导入…' : '确认合并'}
              </button>
            </div>
          </div>
        )}
      </dialog>
    </>
  );
}
