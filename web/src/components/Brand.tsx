export function Brand({ compact = false }: { compact?: boolean }) {
  return (
    <span className={`brand ${compact ? 'brand-compact' : ''}`} aria-label="Presence Monitor">
      <span className="brand-mark" aria-hidden="true">
        <span />
      </span>
      {!compact && (
        <span className="brand-copy">
          Presence <strong>Monitor</strong>
        </span>
      )}
    </span>
  );
}
