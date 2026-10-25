// Live guidance card (Phase B): Find destination -> Show route -> Start -> Arrive.
// User view: next instruction, distance, destination and few controls, no ids.
// Developer view: the same simulation plus floor, heading, progress, revisions and routing facts.
import type { GuidanceVoiceState } from '../../lib/guidanceVoice';
import { GUIDANCE_SPEED } from '../../lib/guidanceVoice';
import { formatMetres } from '../../lib/nav';
import { SPEEDS, type InstructionKind } from '../../lib/simulation';
import { RouteBadge } from './ChatReply';
import { CloseIcon, ReplayIcon, RouteIcon, SpeakerIcon } from './Icons';
import type { Guidance } from './useGuidance';

const KIND_ICON: Record<InstructionKind, string> = {
  start: '●',
  turn_left: '↰',
  turn_right: '↱',
  door: '⇥',
  exit: '⇱',
  stairs: '⇅',
  lift: '⇕',
  arrive: '⚑',
};

const STATUS_TEXT: Record<string, string> = {
  idle: 'Route ready',
  active: 'Navigating',
  paused: 'Paused',
  recomputing: 'Recalculating…',
  arrived: 'Arrived',
  stopped: 'Stopped',
  blocked: 'No route',
};

interface Props {
  guidance: Guidance;
  developer: boolean;
  destination: string | null;
  floorName: (id: string | null | undefined) => string | null;
  scenarioRevision?: number;
  voice: GuidanceVoiceState | null;
  voiceAvailable: boolean;
  onMute: () => void;
  onRepeat: () => void;
  onVoiceSpeed: (speed: number) => void;
  onUnlockAudio: () => void;
  onClose: () => void;
  /** First-person view: the HUD shows the instruction, the card keeps only status and controls. */
  pov?: boolean;
}

export default function GuidancePanel(props: Props) {
  const { guidance: g, developer, voice } = props;
  const snap = g.snapshot;
  const route = snap.route;
  if (!route || route.status !== 'ok') {
    if (snap.status !== 'blocked') return null;
  }
  const status = snap.status;
  const remaining = Math.max(0, snap.total_m - snap.distance_m);
  const minutes = Math.max(1, Math.round(remaining / 1.3 / 60));
  const ins = g.instruction;
  const p = snap.progress;

  const start = () => {
    props.onUnlockAudio();
    g.start();
  };

  return (
    <section className={`guidance card${developer ? ' guidance--dev' : ''}${props.pov ? ' guidance--pov' : ''}`} aria-label="Navigation">
      <header className="guidance__head">
        <RouteIcon width={16} height={16} />
        <span className="guidance__dest">{props.destination ?? 'Destination'}</span>
        <span className={`guidance__status guidance__status--${status}`} role="status">
          {STATUS_TEXT[status] ?? status}
        </span>
        {!g.navigating && (
          <button className="icon-btn icon-btn--sm" onClick={props.onClose} aria-label="Close navigation">
            <CloseIcon width={14} height={14} />
          </button>
        )}
      </header>

      {snap.message && <p className="guidance__message">{snap.message}</p>}

      {ins && status !== 'blocked' && (
        <div className="guidance__next" aria-live="polite">
          <span className={`guidance__icon guidance__icon--${ins.kind}`} aria-hidden="true">
            {KIND_ICON[ins.kind]}
          </span>
          <div>
            <p className="guidance__text">{ins.text}</p>
            <p className="guidance__meta">
              {status === 'arrived'
                ? 'You are there.'
                : `${g.toNextM !== null && g.toNextM > 0.5 ? `In ${formatMetres(g.toNextM)} · ` : ''}${formatMetres(remaining)} left · about ${minutes} min`}
            </p>
          </div>
        </div>
      )}

      <div className="guidance__progress" aria-hidden="true">
        <span style={{ width: `${Math.round((snap.total_m ? snap.distance_m / snap.total_m : 0) * 100)}%` }} />
      </div>

      <div className="guidance__controls">
        {!g.navigating && status !== 'arrived' && (
          <>
            <button className="btn btn--primary btn--sm" onClick={start} disabled={!g.canStart}>
              {status === 'stopped' ? 'Restart navigation' : 'Start navigation'}
            </button>
            {g.previewing ? (
              <button className="btn btn--ghost btn--sm" onClick={g.skipPreview}>
                Skip preview
              </button>
            ) : (
              <button className="btn btn--ghost btn--sm" onClick={g.preview} disabled={!g.canStart}>
                Fly-through
              </button>
            )}
          </>
        )}
        {status === 'active' && (
          <button className="btn btn--primary btn--sm" onClick={g.pause}>
            Pause
          </button>
        )}
        {status === 'paused' && (
          <button className="btn btn--primary btn--sm" onClick={g.resume}>
            Resume
          </button>
        )}
        {(g.navigating || status === 'arrived' || status === 'blocked') && (
          <button className="btn btn--ghost btn--sm" onClick={g.stop}>
            {status === 'arrived' ? 'Done' : 'Stop'}
          </button>
        )}
        <button className="btn btn--ghost btn--sm" onClick={g.overview}>
          Overview
        </button>
        {g.navigating && (
          <button className="btn btn--ghost btn--sm" onClick={g.recenter}>
            Recenter
          </button>
        )}
      </div>

      <div className="guidance__settings">
        <div className="segmented segmented--small" role="group" aria-label="Walking speed">
          {SPEEDS.map((s) => (
            <button
              key={s}
              className="segmented__item"
              aria-pressed={snap.speed === s}
              onClick={() => g.setSpeed(s)}
            >
              {s}×
            </button>
          ))}
        </div>
        <button
          className="chip chip--soft"
          onClick={props.onMute}
          aria-pressed={!!voice && !voice.muted}
          disabled={!props.voiceAvailable}
          title={props.voiceAvailable ? 'Spoken hints on/off' : 'Voice is not available on this server'}
        >
          <SpeakerIcon width={14} height={14} /> {voice && !voice.muted && props.voiceAvailable ? 'Voice on' : 'Voice off'}
        </button>
        <button
          className="icon-btn icon-btn--sm"
          onClick={props.onRepeat}
          disabled={!props.voiceAvailable || !voice?.last || voice.muted}
          aria-label="Repeat instruction"
          title="Repeat instruction"
        >
          <ReplayIcon width={15} height={15} />
        </button>
        {props.voiceAvailable && voice && !voice.muted && (
          <label className="guidance__voice-speed">
            <span>{voice.speed.toFixed(2)}×</span>
            <input
              type="range"
              min={GUIDANCE_SPEED.min}
              max={GUIDANCE_SPEED.max}
              step={0.05}
              value={voice.speed}
              aria-label="Voice speed"
              onChange={(e) => props.onVoiceSpeed(Number(e.target.value))}
            />
          </label>
        )}
      </div>
      {voice?.error && props.voiceAvailable && !voice.muted && (
        <p className="guidance__hint">Spoken hints paused: {voice.error}</p>
      )}

      {developer && (
        <details key={props.pov ? 'pov' : 'map'} className="guidance__debug-wrap" open={!props.pov}>
        <summary>Simulation details</summary>
        <dl className="details__rows guidance__debug" aria-label="Guidance debug">
          <div className="details__row">
            <dt>Route</dt>
            <dd>{route && <RouteBadge route={route} />}</dd>
          </div>
          <div className="details__row">
            <dt>Floor</dt>
            <dd>{props.floorName(p?.floor_id) ?? '–'}</dd>
          </div>
          <div className="details__row">
            <dt>Position</dt>
            <dd>{p ? `${p.position[0].toFixed(2)}, ${p.position[1].toFixed(2)}` : '–'}</dd>
          </div>
          <div className="details__row">
            <dt>Heading</dt>
            <dd>{p ? `${Math.round(p.heading_deg)}°` : '–'}</dd>
          </div>
          <div className="details__row">
            <dt>Progress</dt>
            <dd>
              {formatMetres(snap.distance_m)} / {formatMetres(snap.total_m)} (
              {Math.round((snap.total_m ? snap.distance_m / snap.total_m : 0) * 100)}%)
            </dd>
          </div>
          <div className="details__row">
            <dt>Waypoint</dt>
            <dd>
              {p ? p.waypoint_index : '–'} / {(route?.waypoints?.length ?? 1) - 1}
            </dd>
          </div>
          <div className="details__row">
            <dt>Revisions</dt>
            <dd>
              route {snap.route_revision} · scenario {route?.revision ?? '–'}
              {props.scenarioRevision !== undefined && props.scenarioRevision !== route?.revision
                ? ` (now ${props.scenarioRevision})`
                : ''}
            </dd>
          </div>
          <div className="details__row">
            <dt>Sim time</dt>
            <dd>{snap.sim_time_s.toFixed(1)} s</dd>
          </div>
        </dl>
        </details>
      )}
    </section>
  );
}
