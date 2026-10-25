// Opt-in activity memory (backend/app/memory): API client plus a recorder that turns guidance
// events into a few small memory events. Nothing is sent without a session id the user created.
// Walked distance is XY metres along the route actually covered by the walker: stair/lift rides
// advance route distance by their official cost but add 0 m, and previews never record.
import { ApiError, type RouteResponse } from './api';
import type { Names, SimEvent, SimSnapshot } from './simulation';

const API_BASE_URL = import.meta.env.PUBLIC_API_BASE_URL ?? '';

export type MemorySessionKind = 'real' | 'demo' | 'test';

export interface MemorySession {
  session_id: string;
  kind: MemorySessionKind;
  created_at: string;
}

/** Allowed events only (the backend rejects anything else with 422). */
export type MemoryEventInput =
  | { type: 'search_intent'; data: { text: string } }
  | { type: 'destination_confirmed'; data: { name: string; kind: string; id?: string | null } }
  | { type: 'nav_start' | 'nav_pause' | 'nav_resume' | 'nav_stop'; data?: Record<string, never> }
  | { type: 'moved'; data: { xy_m: number; floor_id: string; floor_name?: string | null } }
  | { type: 'reroute'; data: { reason: string } }
  | { type: 'arrival'; data: { name: string; floor_name?: string | null } }
  | { type: 'setting_confirmed'; data: { key: string; value: string | number | boolean } };

export interface MemoryEventRecord {
  type: MemoryEventInput['type'];
  /** Server timestamp (ISO). */
  at: string;
  data: Record<string, unknown>;
}

export interface MemoryExport extends MemorySession {
  events: MemoryEventRecord[];
}

export interface MemoryRecap {
  session_id: string;
  kind: MemorySessionKind;
  /** e.g. "Your session", "Demo session (synthetic)". */
  label: string;
  /** demo or test: never real user history. */
  synthetic: boolean;
  created_at: string;
  event_count: number;
  /** Arrival names in first-reached order, unique. */
  places_reached: string[];
  places_reached_count: number;
  arrivals: number;
  /** Sum of `moved.xy_m`: actually walked XY metres, not route length or official cost. */
  walked_m: number;
  floors_visited: string[];
  favourite_destination: string | null;
  navigations_started: number;
  navigations_completed: number;
  reroutes: number;
  /** Navigation time from server timestamps, paused time excluded. */
  simulated_minutes: number;
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}/api/memory${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: unknown } | null;
    const message = typeof body?.detail === 'string' ? body.detail : `Memory request failed with ${response.status}`;
    throw new ApiError(response.status, message);
  }
  return (response.status === 204 ? undefined : await response.json()) as T;
}

const sessionPath = (id: string) => `/sessions/${encodeURIComponent(id)}`;

export const memoryApi = {
  createSession: (kind: MemorySessionKind = 'real') =>
    call<MemorySession>('/sessions', { method: 'POST', body: JSON.stringify({ kind }) }),
  addEvent: (sessionId: string, event: MemoryEventInput) =>
    call<MemoryEventRecord>('/events', {
      method: 'POST',
      body: JSON.stringify({ session_id: sessionId, type: event.type, data: event.data ?? {} }),
    }),
  recap: (sessionId: string) => call<MemoryRecap>(`/recap?session_id=${encodeURIComponent(sessionId)}`),
  exportSession: (sessionId: string) => call<MemoryExport>(`${sessionPath(sessionId)}/export`),
  deleteSession: (sessionId: string) => call<void>(sessionPath(sessionId), { method: 'DELETE' }),
};

/** Download the session's events as a JSON file (browser only). */
export async function downloadExport(sessionId: string): Promise<void> {
  const data = await memoryApi.exportSession(sessionId);
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `arinav-session-${data.kind}-${data.created_at.slice(0, 10)}.json`;
  a.click();
  URL.revokeObjectURL(url);
}

/** A stretch of route between two distances: XY metres on one floor, or a vertical ride (0 m). */
export interface XyPiece {
  floor_id: string;
  xy_m: number;
  vertical: boolean;
}

/** XY metres covered between route distances `from_m` and `to_m`, per waypoint segment. */
export function xyAlong(route: RouteResponse, from_m: number, to_m: number): XyPiece[] {
  const wps = route.waypoints ?? [];
  const out: XyPiece[] = [];
  if (!(to_m > from_m)) return out;
  for (let i = 0; i + 1 < wps.length; i++) {
    const a = wps[i];
    const b = wps[i + 1];
    const len = b.distance_m - a.distance_m;
    if (len <= 0 || b.distance_m <= from_m || a.distance_m >= to_m) continue;
    const share = (Math.min(to_m, b.distance_m) - Math.max(from_m, a.distance_m)) / len;
    if (a.floor_id !== b.floor_id) {
      out.push({ floor_id: b.floor_id, xy_m: 0, vertical: true });
      continue;
    }
    const xy = Math.hypot(b.position[0] - a.position[0], b.position[1] - a.position[1]) * share;
    const last = out[out.length - 1];
    if (last && !last.vertical && last.floor_id === a.floor_id) last.xy_m += xy;
    else out.push({ floor_id: a.floor_id, xy_m: xy, vertical: false });
  }
  return out;
}

export type RecorderNames = Pick<Names, 'floor' | 'target'>;

export interface MemoryRecorderOptions {
  /** Defaults to `memoryApi.addEvent`. */
  send?: (sessionId: string, event: MemoryEventInput) => Promise<unknown>;
  /** Send a `moved` event once this many XY metres are pending (default 5). */
  batchM?: number;
  names?: RecorderNames;
  onError?: (error: unknown) => void;
}

interface NavState {
  route: RouteResponse;
  /** Route distance (m) already converted into XY. */
  last: number;
  paused: boolean;
}

/**
 * Turns guidance into memory events. Disabled (records nothing) while the session id is null.
 * Sends are sequential so the server's timestamps keep the real order; failures never throw.
 */
export class MemoryRecorder {
  private session: string | null;
  private readonly send: (sessionId: string, event: MemoryEventInput) => Promise<unknown>;
  private readonly batchM: number;
  private readonly onError?: (error: unknown) => void;
  private names: RecorderNames | null;
  private nav: NavState | null = null;
  private pending: { floor_id: string; xy_m: number } | null = null;
  private queue: Promise<void> = Promise.resolve();

  constructor(sessionId: string | null, options: MemoryRecorderOptions = {}) {
    this.session = sessionId;
    this.send = options.send ?? memoryApi.addEvent;
    this.batchM = options.batchM ?? 5;
    this.onError = options.onError;
    this.names = options.names ?? null;
  }

  get sessionId(): string | null {
    return this.session;
  }

  get enabled(): boolean {
    return this.session !== null;
  }

  /** True between a recorded navigation start and its stop/arrival. */
  get navigating(): boolean {
    return this.nav !== null;
  }

  /** A new session (or null to switch memory off). Any open navigation is forgotten, not sent. */
  setSession(sessionId: string | null): void {
    this.session = sessionId;
    this.nav = null;
    this.pending = null;
  }

  setNames(names: RecorderNames): void {
    this.names = names;
  }

  /** Resolves when everything recorded so far has been sent (or failed). */
  idle(): Promise<void> {
    return this.queue;
  }

  searchIntent(text: string): void {
    const t = text.trim().slice(0, 120);
    if (t) this.emit({ type: 'search_intent', data: { text: t } });
  }

  destinationConfirmed(dest: { name: string; kind: string; id?: string | null }): void {
    const name = dest.name.trim().slice(0, 120);
    if (!name) return;
    this.emit({ type: 'destination_confirmed', data: { name, kind: dest.kind.slice(0, 80), id: dest.id ?? null } });
  }

  settingConfirmed(key: string, value: string | number | boolean): void {
    this.emit({ type: 'setting_confirmed', data: { key, value } });
  }

  /** A navigation actually starts on `route` at route distance `from_m` (0 = its beginning). */
  start(route: RouteResponse, from_m = 0): void {
    if (!this.enabled) return;
    if (this.nav) this.stop();
    this.nav = { route, last: from_m, paused: false };
    this.emit({ type: 'nav_start' });
  }

  /** The walker is at route distance `distance_m` on the current route. */
  progress(distance_m: number): void {
    const nav = this.nav;
    if (!nav || !this.enabled) return;
    if (!(distance_m > nav.last)) {
      nav.last = Math.min(nav.last, distance_m);
      return;
    }
    for (const piece of xyAlong(nav.route, nav.last, distance_m)) {
      if (piece.vertical) {
        this.flush(); // a ride: what was walked before it belongs to the floor left behind
        continue;
      }
      if (this.pending && this.pending.floor_id !== piece.floor_id) this.flush();
      this.pending ??= { floor_id: piece.floor_id, xy_m: 0 };
      this.pending.xy_m += piece.xy_m;
      if (this.pending.xy_m >= this.batchM) this.flush();
    }
    nav.last = distance_m;
  }

  pause(): void {
    if (!this.nav || this.nav.paused) return;
    this.flush();
    this.nav.paused = true;
    this.emit({ type: 'nav_pause' });
  }

  resume(): void {
    if (!this.nav?.paused) return;
    this.nav.paused = false;
    this.emit({ type: 'nav_resume' });
  }

  stop(): void {
    if (!this.nav) return;
    this.flush();
    this.nav = null;
    this.emit({ type: 'nav_stop' });
  }

  /** The new route replaces the current one; the walker continues at `from_m` on it. */
  reroute(route: RouteResponse, reason = 'scenario_changed', from_m = 0): void {
    if (!this.nav) return;
    this.flush();
    this.nav.route = route;
    this.nav.last = from_m;
    this.emit({ type: 'reroute', data: { reason: reason.slice(0, 80) } });
  }

  /** Arrival ends the navigation; a second arrival without a new start is ignored. */
  arrive(name?: string | null, floorName?: string | null): void {
    const nav = this.nav;
    if (!nav) return;
    this.flush();
    const wps = nav.route.waypoints ?? [];
    const floorId = wps[wps.length - 1]?.floor_id ?? nav.route.target?.floor_id ?? null;
    const label = (name ?? this.names?.target ?? '').trim().slice(0, 120) || 'Destination';
    const floor = floorName ?? this.names?.floor(floorId) ?? null;
    this.nav = null;
    this.emit({ type: 'arrival', data: { name: label, ...(floor ? { floor_name: floor.slice(0, 120) } : {}) } });
  }

  /** Send the pending walked metres now (pause, ride, floor change, stop, arrival). */
  flush(): void {
    const p = this.pending;
    this.pending = null;
    if (!p || p.xy_m < 0.001) return;
    const floor = this.names?.floor(p.floor_id) ?? null;
    this.emit({
      type: 'moved',
      data: { xy_m: Math.round(p.xy_m * 1000) / 1000, floor_id: p.floor_id, ...(floor ? { floor_name: floor } : {}) },
    });
  }

  /**
   * Feed guidance: call with the events of every GuidanceSim call (start/tick/pause/resume/stop/
   * load) and the snapshot after it; `[]` with a fresh snapshot is fine for per-frame progress.
   */
  observe(events: SimEvent[], snap: SimSnapshot, names?: RecorderNames): void {
    if (names) this.names = names;
    if (!this.enabled) return;
    if (this.nav && snap.route === this.nav.route) this.progress(snap.distance_m);
    for (const e of events) {
      if (e.type === 'start') {
        if (!snap.route) continue;
        if (!this.nav) this.start(snap.route, snap.distance_m);
        else if (snap.route !== this.nav.route) this.reroute(snap.route, 'scenario_changed', snap.distance_m);
      } else if (e.type === 'pause') this.pause();
      else if (e.type === 'resume') this.resume();
      else if (e.type === 'stop') this.stop();
      else if (e.type === 'arrive') this.arrive();
      else if (e.type === 'blocked') this.flush();
    }
    // The followed route was replaced or dropped without a stop (new destination, other building).
    if (this.nav && snap.route !== this.nav.route && (snap.status === 'idle' || !snap.route)) this.stop();
  }

  private emit(event: MemoryEventInput): void {
    const sid = this.session;
    if (!sid) return;
    this.queue = this.queue
      .then(() => this.send(sid, event))
      .then(
        () => undefined,
        (error: unknown) => {
          // A deleted session stays deleted: stop recording into it.
          if (error instanceof ApiError && error.status === 404 && this.session === sid) this.setSession(null);
          this.onError?.(error);
        },
      );
  }
}
