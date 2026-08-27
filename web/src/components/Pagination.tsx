import { ChevronsLeft, ChevronsRight, ChevronLeft, ChevronRight } from 'lucide-react';
import { FormEvent, useEffect, useId, useState } from 'react';

export function Pagination({
  page,
  pageCount,
  busy,
  label,
  onPageChange,
}: {
  page: number;
  pageCount: number;
  busy: boolean;
  label: string;
  onPageChange: (page: number) => void;
}) {
  const statusId = useId();
  const [draft, setDraft] = useState(String(page + 1));
  useEffect(() => setDraft(String(page + 1)), [page]);

  const go = (next: number) => {
    const target = Math.max(0, Math.min(pageCount - 1, next));
    setDraft(String(target + 1));
    if (target !== page) onPageChange(target);
  };
  const submit = (event: FormEvent) => {
    event.preventDefault();
    const requested = Number.parseInt(draft, 10);
    if (Number.isFinite(requested)) go(requested - 1);
    else setDraft(String(page + 1));
  };

  return (
    <footer className="pagination" aria-label={`${label}分页`} aria-busy={busy}>
      <span id={statusId} role="status" aria-live="polite">第 {page + 1} / {pageCount} 页</span>
      <form className="pagination-controls" onSubmit={submit}>
        <button className="icon-button" type="button" onClick={() => go(0)} disabled={busy || page === 0} aria-label={`${label}第一页`}>
          <ChevronsLeft size={18} aria-hidden="true" />
        </button>
        <button className="icon-button" type="button" onClick={() => go(page - 1)} disabled={busy || page === 0} aria-label={`${label}上一页`}>
          <ChevronLeft size={18} aria-hidden="true" />
        </button>
        <label className="page-jump">
          <span className="sr-only">跳转到页</span>
          <input
            value={draft}
            onChange={(event) => setDraft(event.target.value.replace(/\D/g, '').slice(0, 7))}
            inputMode="numeric"
            pattern="[0-9]*"
            aria-label="页码"
            aria-describedby={statusId}
            disabled={busy}
          />
        </label>
        <button className="icon-button" type="button" onClick={() => go(page + 1)} disabled={busy || page >= pageCount - 1} aria-label={`${label}下一页`}>
          <ChevronRight size={18} aria-hidden="true" />
        </button>
        <button className="icon-button" type="button" onClick={() => go(pageCount - 1)} disabled={busy || page >= pageCount - 1} aria-label={`${label}最后一页`}>
          <ChevronsRight size={18} aria-hidden="true" />
        </button>
      </form>
    </footer>
  );
}
