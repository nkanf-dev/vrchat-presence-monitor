import type { MouseEvent } from 'react';

import { routeHref, type AnalysisSection } from '../navigation';

const items = [
  { id: 'daily', label: '每日在线' },
  { id: 'worlds', label: '世界时间轴' },
  { id: 'discover', label: '世界发现' },
] as const satisfies ReadonlyArray<{ id: AnalysisSection; label: string }>;

const shouldHandleNavigation = (event: MouseEvent<HTMLAnchorElement>) =>
  event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey;

export function AnalysisNav({
  section,
  parameters,
  onNavigate,
}: {
  section: AnalysisSection;
  parameters: URLSearchParams;
  onNavigate: (section: AnalysisSection) => void;
}) {
  return (
    <nav className="section-nav" aria-label="分析页面">
      {items.map((item) => {
        const active = item.id === section;
        return (
          <a
            key={item.id}
            href={routeHref(parameters, { area: 'analysis', section: item.id })}
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
