import { ExternalLink, MapPin, Monitor, UserRound, X } from 'lucide-react';
import { useEffect, useRef } from 'react';

import type { Friend } from '../api';
import {
  friendName,
  locationLabel,
  parseBioLinks,
  platformLabel,
  statusLabel,
  statusTone,
} from '../format';
import { Avatar } from './Avatar';

export function FriendDialog({ friend, onClose }: { friend: Friend | null; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const element = dialog.current;
    if (!element) return;
    if (friend && !element.open) element.showModal();
    if (!friend && element.open) element.close();
  }, [friend]);

  if (!friend) return null;
  const name = friendName(friend);
  const links = parseBioLinks(friend.bio_links);

  return (
    <dialog
      className="dialog profile-dialog"
      ref={dialog}
      aria-labelledby="profile-dialog-title"
      onClose={onClose}
      onCancel={onClose}
    >
      <div className="dialog-scroll">
        <button className="icon-button dialog-close" onClick={() => dialog.current?.close()} aria-label="关闭资料">
          <X size={20} aria-hidden="true" />
        </button>
        <header className="profile-header">
          <Avatar friend={friend} size="large" />
          <div>
            <span className={`status-badge tone-${statusTone(friend.status)}`}>{statusLabel(friend.status)}</span>
            <h2 id="profile-dialog-title">{name}</h2>
            {friend.username && friend.username !== name && <p className="profile-handle">@{friend.username}</p>}
            {Boolean(friend.is_self) && <span className="self-label">你的账号</span>}
          </div>
        </header>

        <dl className="profile-facts">
          <div>
            <dt>
              <MapPin size={16} aria-hidden="true" /> 位置
            </dt>
            <dd>{locationLabel(friend.location, friend.status)}</dd>
          </div>
          <div>
            <dt>
              <Monitor size={16} aria-hidden="true" /> 设备
            </dt>
            <dd>{platformLabel(friend.platform)}</dd>
          </div>
          <div>
            <dt>
              <UserRound size={16} aria-hidden="true" /> 状态文字
            </dt>
            <dd>{friend.status_description || '没有公开状态文字'}</dd>
          </div>
        </dl>

        <section className="profile-section" aria-labelledby="bio-title">
          <h3 id="bio-title">简介</h3>
          <p className={friend.bio ? 'bio-copy' : 'muted'}>{friend.bio || '这个玩家没有公开简介。'}</p>
        </section>

        {links.length > 0 && (
          <section className="profile-section" aria-labelledby="links-title">
            <h3 id="links-title">公开链接</h3>
            <ul className="link-list">
              {links.map((link) => (
                <li key={link}>
                  <a href={link} target="_blank" rel="noreferrer noopener">
                    <span>{new URL(link).hostname}</span>
                    <ExternalLink size={15} aria-hidden="true" />
                  </a>
                </li>
              ))}
            </ul>
          </section>
        )}

        <details className="technical-details">
          <summary>技术详情</summary>
          <dl>
            <div>
              <dt>用户 ID</dt>
              <dd>{friend.id}</dd>
            </div>
            <div>
              <dt>原始位置</dt>
              <dd>{friend.location || '—'}</dd>
            </div>
            <div>
              <dt>原始平台</dt>
              <dd>{friend.platform || '—'}</dd>
            </div>
          </dl>
        </details>
      </div>
    </dialog>
  );
}
