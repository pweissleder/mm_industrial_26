// "Your session at a glance": a compact recap computed by the backend from recorded events only.
// Never shows invented numbers: anything without data renders as an em dash.
import type { MemoryRecap } from '../../lib/memory';

const EMPTY = '—';

export function formatWalked(recap: MemoryRecap | null): string {
  if (!recap || (recap.navigations_started === 0 && recap.walked_m === 0)) return EMPTY;
  return `${recap.walked_m.toFixed(1)} m`;
}

export function formatMinutes(recap: MemoryRecap | null): string {
  if (!recap || recap.navigations_started === 0) return EMPTY;
  const seconds = Math.round(recap.simulated_minutes * 60);
  if (seconds < 60) return `${seconds} s`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return s ? `${m} min ${s} s` : `${m} min`;
}

const badgeText = (recap: MemoryRecap) =>
  recap.kind === 'demo' ? 'Demo · synthetic' : recap.kind === 'test' ? 'Test · not your history' : null;

interface Props {
  recap: MemoryRecap | null;
  loading?: boolean;
  error?: string | null;
  onNewSession?: () => void;
  onDelete?: () => void;
  onExport?: () => void;
  onClose?: () => void;
}

function Stat({ label, value, detail }: { label: string; value: string; detail?: string | null }) {
  return (
    <div className="recap-card__stat">
      <dt>{label}</dt>
      <dd>
        <span className="recap-card__value">{value}</span>
        {detail ? <span className="recap-card__detail">{detail}</span> : null}
      </dd>
    </div>
  );
}

export default function RecapCard({ recap, loading, error, onNewSession, onDelete, onExport, onClose }: Props) {
  const badge = recap ? badgeText(recap) : null;
  const places = recap?.places_reached ?? [];
  const floors = recap?.floors_visited ?? [];
  const nav =
    recap && recap.navigations_started > 0
      ? `${recap.navigations_completed} of ${recap.navigations_started} navigations completed` +
        (recap.reroutes ? ` · ${recap.reroutes} reroute${recap.reroutes === 1 ? '' : 's'}` : '')
      : null;
  return (
    <section className={`recap-card${recap?.synthetic ? ' recap-card--synthetic' : ''}`} aria-label="Session recap">
      <header className="recap-card__head">
        <div>
          <p className="recap-card__eyebrow">{recap?.label ?? 'Session recap'}</p>
          <h2 className="recap-card__title">Your session at a glance</h2>
        </div>
        {badge ? (
          <span className={`recap-card__badge recap-card__badge--${recap?.kind}`} title={recap?.label}>
            {badge}
          </span>
        ) : null}
        {onClose ? (
          <button type="button" className="recap-card__close" onClick={onClose} aria-label="Close recap">
            ×
          </button>
        ) : null}
      </header>

      {error ? (
        <p className="recap-card__note recap-card__note--bad" role="alert">
          {error}
        </p>
      ) : loading ? (
        <p className="recap-card__note" role="status">
          Loading your recap…
        </p>
      ) : null}

      <dl className="recap-card__grid">
        <Stat
          label="Places reached"
          value={places.length ? String(recap?.places_reached_count ?? places.length) : EMPTY}
          detail={places.length ? places.join(', ') : null}
        />
        <Stat label="Walked" value={formatWalked(recap)} detail={recap && recap.walked_m > 0 ? 'on foot, rides excluded' : null} />
        <Stat label="Floors" value={floors.length ? String(floors.length) : EMPTY} detail={floors.length ? floors.join(', ') : null} />
        <Stat label="Simulated time" value={formatMinutes(recap)} detail={recap?.navigations_started ? 'pauses excluded' : null} />
        <div className="recap-card__stat recap-card__stat--wide">
          <dt>Favourite destination</dt>
          <dd>
            <span className="recap-card__value">{recap?.favourite_destination ?? EMPTY}</span>
          </dd>
        </div>
      </dl>

      {nav ? <p className="recap-card__foot">{nav}</p> : null}

      <div className="recap-card__actions">
        <button type="button" className="recap-card__btn recap-card__btn--primary" onClick={onNewSession} disabled={!onNewSession}>
          New session
        </button>
        <button type="button" className="recap-card__btn" onClick={onExport} disabled={!onExport || !recap}>
          Export
        </button>
        <button type="button" className="recap-card__btn recap-card__btn--bad" onClick={onDelete} disabled={!onDelete || !recap}>
          Delete history
        </button>
      </div>
    </section>
  );
}
