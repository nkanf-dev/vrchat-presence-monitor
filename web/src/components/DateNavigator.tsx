import { CalendarDays, ChevronLeft, ChevronRight, LocateFixed } from 'lucide-react';

import { offsetDateKey, todayKey } from '../analytics';

export function DateNavigator({
  value,
  onChange,
  label,
}: {
  value: string;
  onChange: (value: string) => void;
  label: string;
}) {
  const today = todayKey();
  const latest = value >= today;
  return (
    <div className="date-navigator" aria-label={label}>
      <button
        type="button"
        className="icon-button"
        onClick={() => onChange(offsetDateKey(value, -1))}
        aria-label="前一天"
      >
        <ChevronLeft size={18} aria-hidden="true" />
      </button>
      <label className="date-field">
        <CalendarDays size={17} aria-hidden="true" />
        <span className="sr-only">{label}</span>
        <input type="date" value={value} max={today} onChange={(event) => onChange(event.target.value)} />
      </label>
      <button
        type="button"
        className="icon-button"
        onClick={() => onChange(offsetDateKey(value, 1))}
        disabled={latest}
        aria-label="后一天"
      >
        <ChevronRight size={18} aria-hidden="true" />
      </button>
      <button type="button" className="button button-secondary button-compact" onClick={() => onChange(today)}>
        <LocateFixed size={16} aria-hidden="true" />
        最新
      </button>
    </div>
  );
}

