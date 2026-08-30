import type { MouseEvent } from 'react';

import { routeHref, type MoreSection } from '../navigation';

const items = [
  { id: 'history', label: '状态历史' },
  { id: 'data', label: '数据与备份' },
  { id: 'settings', label: '设置' },
] as const satisfies ReadonlyArray<{ id: MoreSection; label: string }>;

const shouldHandleNavigation = (event: MouseEvent<HTMLAnchorElement>) =>
  event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey;

export function MoreNav({
  section,
  parameters,
  onNavigate,
}: {
  section: MoreSection;
  parameters: URLSearchParams;
  onNavigate: (section: MoreSection) => void;
}) {
  return (
    <nav className="section-nav" aria-label="更多页面">
      {items.map((item) => {
        const active = item.id === section;
        return (
          <a
            key={item.id}
            href={routeHref(parameters, { area: 'more', section: item.id })}
            className={active ? 'section-nav-link active' : 'section-nav-link'}
            aria-current={active ? 'page' : undefined}
            onClick={(event) => {
              if (!shouldHandleNavigation(event)) return;
              event.preventDefault();
              onNavigate(item.id);
            }}
          >
            {item.label}
          </a>
        );
      })}
    </nav>
  );
}
