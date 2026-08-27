import { useQuery } from '@tanstack/react-query';
import { ExternalLink, Image, Map, RefreshCw, X } from 'lucide-react';
import { useEffect, useRef } from 'react';

import { isSpecialWorld, specialWorldInfo } from '../analytics';
import { ApiError, getWorld, worldImageUrl } from '../api';

export function WorldDialog({ worldId, onClose }: { worldId: string | null; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const special = worldId ? specialWorldInfo(worldId) : undefined;
  const result = useQuery({
    queryKey: ['world', worldId],
    queryFn: () => getWorld(worldId ?? ''),
    enabled: Boolean(worldId && !isSpecialWorld(worldId)),
    staleTime: 60 * 60_000,
  });
  const info = special ?? result.data;

  useEffect(() => {
    const element = dialog.current;
    if (!element) return;
    if (worldId && !element.open) element.showModal();
    if (!worldId && element.open) element.close();
  }, [worldId]);

  const metrics = info
    ? [
        ['容量', info.capacity],
        ['推荐容量', info.recommended_capacity],
        ['当前人数', info.occupants],
        ['访问', info.visits],
        ['收藏', info.favorites],
        ['热度', info.heat],
        ['流行度', info.popularity],
      ].filter((item) => item[1] !== null && item[1] !== undefined && item[1] !== '')
    : [];
  const image = info?.image_url || info?.thumbnail_url || '';

  return (
    <dialog
      className="dialog world-dialog"
      ref={dialog}
      aria-labelledby="world-dialog-title"
      onClose={onClose}
      onCancel={onClose}
      onClick={(event) => {
        if (event.target === dialog.current) dialog.current?.close();
      }}
    >
      <div className="dialog-scroll">
        <button className="icon-button dialog-close" onClick={() => dialog.current?.close()} aria-label="关闭世界详情">
          <X size={20} aria-hidden="true" />
        </button>
        {!info && result.isPending ? (
          <div className="dialog-loading" role="status">
            <Image size={24} aria-hidden="true" />
            <strong>正在读取世界资料…</strong>
          </div>
        ) : !info && result.isError ? (
          <div className="inline-error" role="alert">
            <Map size={24} aria-hidden="true" />
            <strong>世界详情暂时不可用</strong>
            <span>{result.error instanceof ApiError ? result.error.message : '请稍后重试'}</span>
            <button type="button" className="button button-secondary" onClick={() => void result.refetch()}>
              <RefreshCw size={17} aria-hidden="true" />
              重新读取
            </button>
          </div>
        ) : info ? (
          <>
            <header className="world-dialog-header">
              {image ? (
                <img src={worldImageUrl(image)} alt="" />
              ) : (
                <span className="world-dialog-placeholder" aria-hidden="true"><Map size={30} /></span>
              )}
              <div>
                <p className="kicker">VRChat world</p>
                <h2 id="world-dialog-title">{info.name}</h2>
                <p>{info.author_name || info.author_id || 'VRChat'}</p>
              </div>
            </header>
            <div className="world-facts">
              {info.release_status && <span>{info.release_status}</span>}
              {info.organization && <span>{info.organization}</span>}
              {metrics.map(([label, value]) => <span key={label}>{label}：{String(value)}</span>)}
            </div>
            {info.tags.length > 0 && <div className="world-tags">{info.tags.map((tag) => <span key={tag}>{tag}</span>)}</div>}
            <section className="world-description">
              <h3>世界简介</h3>
              <p>{info.description || '这个世界没有公开简介。'}</p>
            </section>
            <dl className="world-technical-details">
              <div><dt>World ID</dt><dd>{info.id}</dd></div>
              {info.publication_date && <div><dt>发布于</dt><dd>{new Date(info.publication_date).toLocaleString('zh-CN')}</dd></div>}
              {info.updated_at && <div><dt>更新于</dt><dd>{new Date(info.updated_at).toLocaleString('zh-CN')}</dd></div>}
            </dl>
            {!isSpecialWorld(info.id) && (
              <a className="button button-secondary world-external-link" href={`https://vrchat.com/home/world/${encodeURIComponent(info.id)}`} target="_blank" rel="noreferrer noopener">
                <ExternalLink size={17} aria-hidden="true" />
                在 VRChat 查看
              </a>
            )}
          </>
        ) : null}
      </div>
    </dialog>
  );
}
