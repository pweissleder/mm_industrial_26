import type { ReactNode } from 'react';
import type { Proposal, RouteResponse, TurnResponse } from '../../lib/api';
import { formatMetres, readRouteError, routeBadge } from '../../lib/nav';
import { AlertIcon, CheckIcon, ReplayIcon, RouteIcon } from './Icons';

/** Steps the assistant went through, as plain progress (never raw tool calls). */
export function Phases({ phases }: { phases: string[] }) {
  if (!phases.length) return null;
  return (
    <ul className="phases" aria-label="Steps">
      {phases.map((p, i) => (
        <li key={i}>
          <CheckIcon width={13} height={13} /> {p}
        </li>
      ))}
    </ul>
  );
}

export function RouteBadge({ route }: { route: RouteResponse }) {
  const badge = routeBadge(route);
  return <span className={`route-badge route-badge--${badge.tone}`}>{badge.label}</span>;
}

function Directions({ route }: { route: RouteResponse }) {
  if (route.status !== 'ok') {
    return (
      <p className="chat__note chat__note--bad">
        <AlertIcon width={14} height={14} /> {route.unreachable_reason || `No route (${route.status}).`}
      </p>
    );
  }
  const steps = route.directions ?? [];
  return (
    <details className="directions" open>
      <summary>
        <RouteIcon width={15} height={15} />
        <span>
          Directions · {formatMetres(route.length_m)}
          {steps.length ? ` · ${steps.length} step${steps.length === 1 ? '' : 's'}` : ''}
        </span>
        <RouteBadge route={route} />
      </summary>
      {steps.length > 0 && (
        <ol>
          {steps.map((s, i) => (
            <li key={i}>{s}</li>
          ))}
        </ol>
      )}
      {route.warnings?.length > 0 && (
        <p className="directions__warn">{route.warnings.join(' · ')}</p>
      )}
    </details>
  );
}

function DevDetails({ turn }: { turn: TurnResponse }) {
  const t = turn.timing_ms ?? {};
  const u = turn.usage ?? {};
  const meta = [
    t.claude_ms !== undefined && `Claude ${(t.claude_ms / 1000).toFixed(1)} s`,
    t.tools_ms !== undefined && `tools ${Math.round(t.tools_ms)} ms`,
    t.tts_ms !== undefined && `TTS ${Math.round(t.tts_ms)} ms`,
    t.total_ms !== undefined && `total ${(t.total_ms / 1000).toFixed(1)} s`,
    `${u.input_tokens ?? 0}/${u.output_tokens ?? 0} tokens`,
    u.estimated_usd !== undefined && `≈ $${u.estimated_usd.toFixed(4)}`,
    `status ${turn.status}`,
  ].filter(Boolean);
  return (
    <div className="chat__dev">
      <p className="chat__meta">{meta.join(' · ')}</p>
      {turn.tools?.length > 0 && (
        <details className="tool-calls">
          <summary>Tool calls ({turn.tools.length})</summary>
          <pre>{JSON.stringify(turn.tools, null, 2)}</pre>
        </details>
      )}
    </div>
  );
}

export function AssistantReply(props: {
  text: string;
  /** Renders the reply text, e.g. with rooms and assets as links. */
  renderText?: (text: string) => ReactNode;
  turn?: TurnResponse;
  developer: boolean;
  canReplay: boolean;
  onReplay: () => void;
}) {
  const { turn } = props;
  const routeError = readRouteError(turn?.ui?.route_error);
  return (
    <>
      <span className="chat__text">{props.renderText ? props.renderText(props.text) : props.text}</span>
      {turn?.ui?.route && <Directions route={turn.ui.route} />}
      {!turn?.ui?.route && routeError && (
        <p className="chat__note chat__note--bad">
          <AlertIcon width={14} height={14} /> {routeError.message}
        </p>
      )}
      {turn && <Phases phases={turn.phases ?? []} />}
      {props.canReplay && (
        <button type="button" className="chat__replay" onClick={props.onReplay}>
          <ReplayIcon width={14} height={14} /> Replay
        </button>
      )}
      {props.developer && turn && <DevDetails turn={turn} />}
    </>
  );
}

export type ProposalState = 'open' | 'busy' | 'applied' | 'cancelled' | 'failed';

export function ProposalCard(props: {
  proposal: Proposal;
  state: ProposalState;
  onAnswer: (approved: boolean) => void;
}) {
  const { proposal: p, state } = props;
  const verb = p.action === 'close' ? 'Close' : p.action === 'open' ? 'Reopen' : p.action;
  return (
    <div className="proposal" role="group" aria-label="Confirm change">
      <span className="eyebrow">Needs your confirmation</span>
      <p>
        {verb} {p.element_kind} “{p.element_name}”?
      </p>
      {state === 'open' || state === 'busy' ? (
        <div className="proposal__actions">
          <button
            type="button"
            className="btn btn--primary btn--sm"
            disabled={state === 'busy'}
            onClick={() => props.onAnswer(true)}
          >
            Confirm
          </button>
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            disabled={state === 'busy'}
            onClick={() => props.onAnswer(false)}
          >
            Cancel
          </button>
        </div>
      ) : (
        <p className="muted">
          {state === 'applied' ? 'Applied.' : state === 'cancelled' ? 'Cancelled.' : 'Not applied.'}
        </p>
      )}
    </div>
  );
}
