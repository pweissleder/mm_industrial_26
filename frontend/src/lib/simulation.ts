// Phase B virtual navigation: spoken/visible instructions derived from a RouteResponse and a
// deterministic walker. Position = progressAt(route, distance); distance = integral of speed over
// simulation time. No randomness, no wall clock, no model calls: the UI feeds `tick(dt)`.
import type { RouteResponse, RouteWaypoint } from './api';
import { progressAt, type RouteProgress } from './guidance';

/** Walking speed at 1x (m/s). Vertical moves advance by their official equivalent cost. */
export const WALK_SPEED = 1.3;
export const SPEEDS = [0.5, 1, 2, 4, 8] as const;
/** Announce an instruction this far (m) before its point. */
export const ANNOUNCE_M = 6;
const TURN_DEG = 50;
const MIN_TURN_LEG_M = 1.5;

export type InstructionKind =
  | 'start'
  | 'turn_left'
  | 'turn_right'
  | 'door'
  | 'exit'
  | 'stairs'
  | 'lift'
  | 'arrive';

export interface Instruction {
  /** Route distance (m) of the point the instruction refers to. */
  at_m: number;
  waypoint_index: number;
  kind: InstructionKind;
  text: string;
  floor_id: string;
  element_id?: string | null;
}

/** Names for ids, so instructions never show technical ids. */
export interface Names {
  space: (id: string | null | undefined) => string | null;
  floor: (id: string | null | undefined) => string | null;
  /** Label of the route target (room, asset, door, point). */
  target: string | null;
}

const angleDelta = (from: number, to: number) => ((to - from + 540) % 360) - 180;

/** Instructions in route order, from the backend's waypoints only (no geometry invented). */
export function buildInstructions(route: RouteResponse, names: Names): Instruction[] {
  const wps: RouteWaypoint[] = route.waypoints ?? [];
  if (!wps.length || route.status !== 'ok') return [];
  const exits = new Set(
    (route.transitions ?? []).filter((t) => t.kind === 'door' && (t as { is_exit?: boolean }).is_exit).map((t) => t.element_id),
  );
  const out: Instruction[] = [];
  const push = (wp: RouteWaypoint, kind: InstructionKind, text: string) =>
    out.push({ at_m: wp.distance_m, waypoint_index: wp.index, kind, text, floor_id: wp.floor_id, element_id: wp.element_id ?? null });

  const first = wps[0];
  const from = names.space(first.space_id);
  push(first, 'start', from ? `Start in ${from} and follow the line.` : 'Follow the line on the map.');

  for (let i = 0; i < wps.length - 1; i++) {
    const wp = wps[i];
    const prev = wps[i - 1] ?? wp;
    // A ride starts here: its departure point (also when the route starts in a stairwell or lift,
    // where the first waypoint is the start and keeps the connector id).
    const ride = wp.kind === 'stairs' || wp.kind === 'lift' ? wp.kind : null;
    const startRide =
      i === 0 && wp.element_id && wps[1]?.element_id === wp.element_id && wps[1].floor_id !== wp.floor_id
        ? (wps[1].kind as 'stairs' | 'lift')
        : null;
    if (i === 0 && !startRide) continue;
    if ((ride || startRide) && (i === 0 || prev.floor_id === wp.floor_id)) {
      const kind = (startRide ?? ride) as 'stairs' | 'lift';
      // A ride starts here; consecutive hops of the same connector are one instruction.
      let j = i + 1;
      while (j + 1 < wps.length && wps[j + 1].element_id === wp.element_id && wps[j + 1].kind === kind) j++;
      const arrival = wps[Math.min(j, wps.length - 1)];
      const floor = names.floor(arrival.floor_id) ?? 'the next floor';
      const dir = arrival.elevation_m > wp.elevation_m ? 'up' : arrival.elevation_m < wp.elevation_m ? 'down' : '';
      const what = kind === 'lift' ? 'the lift' : 'the stairs';
      push(wp, kind, `Take ${what}${dir ? ` ${dir}` : ''} to ${floor}.`);
      i = j;
      continue;
    }
    if (wp.kind === 'door' && wp.element_id) {
      if (exits.has(wp.element_id)) {
        push(wp, 'exit', 'Go through the exit door.');
      } else {
        const next = names.space(wps[i + 1]?.space_id);
        push(wp, 'door', next ? `Go through the door into ${next}.` : 'Go through the door.');
      }
      continue;
    }
    if (wp.kind === 'walk' && prev.floor_id === wp.floor_id) {
      const inLeg = Math.hypot(wp.position[0] - prev.position[0], wp.position[1] - prev.position[1]);
      const next = wps[i + 1];
      const outLeg = next && next.floor_id === wp.floor_id ? Math.hypot(next.position[0] - wp.position[0], next.position[1] - wp.position[1]) : 0;
      const delta = angleDelta(prev.heading_deg, wp.heading_deg);
      const near = out.length && wp.distance_m - out[out.length - 1].at_m < 2;
      if (Math.abs(delta) >= TURN_DEG && inLeg >= MIN_TURN_LEG_M && outLeg >= MIN_TURN_LEG_M && !near) {
        // Building frame is counter-clockwise: a positive change of heading is a left turn.
        push(wp, delta > 0 ? 'turn_left' : 'turn_right', delta > 0 ? 'Turn left.' : 'Turn right.');
      }
    }
  }
  const last = wps[wps.length - 1];
  const target = names.target;
  const exitTarget = !!last.element_id && exits.has(last.element_id);
  push(
    last,
    'arrive',
    exitTarget
      ? 'You have reached the exit door.'
      : target
        ? `You have arrived at ${target}.`
        : 'You have arrived.',
  );
  return out;
}

export type SimStatus = 'idle' | 'active' | 'paused' | 'recomputing' | 'arrived' | 'stopped' | 'blocked';

export type SimEvent =
  | { type: 'start'; route_revision: number }
  | { type: 'instruction'; instruction: Instruction; index: number; key: string }
  | { type: 'arrive' }
  | { type: 'pause' }
  | { type: 'resume' }
  | { type: 'stop' }
  | { type: 'blocked'; message: string };

export interface SimSnapshot {
  status: SimStatus;
  route: RouteResponse | null;
  route_revision: number;
  instructions: Instruction[];
  distance_m: number;
  total_m: number;
  speed: number;
  progress: RouteProgress | null;
  /** Index into `instructions` of the next one ahead (or the arrival). */
  next: number;
  message: string | null;
  sim_time_s: number;
}

/** Deterministic walker along one route at a time; reroutes replace the route, never edit it. */
export class GuidanceSim {
  private route: RouteResponse | null = null;
  private instructions: Instruction[] = [];
  private status: SimStatus = 'idle';
  private distance = 0;
  private speed = 1;
  private revision = 0;
  private announced = new Set<number>();
  private message: string | null = null;
  private time = 0;

  get total(): number {
    const wps = this.route?.waypoints ?? [];
    return wps.length ? wps[wps.length - 1].distance_m : 0;
  }

  /** A new route: the walker starts at its beginning (a reroute starts where the walker is). */
  load(route: RouteResponse, instructions: Instruction[], keepActive = false): SimEvent[] {
    const wasActive = this.status === 'active' || this.status === 'recomputing';
    this.route = route;
    this.instructions = instructions;
    this.distance = 0;
    this.revision += 1;
    this.announced = new Set();
    this.message = null;
    this.status = keepActive && wasActive ? 'active' : 'idle';
    return this.status === 'active' ? [{ type: 'start', route_revision: this.revision }, ...this.announce()] : [];
  }

  start(): SimEvent[] {
    if (!this.route || this.route.status !== 'ok' || !(this.route.waypoints ?? []).length) {
      this.status = 'blocked';
      this.message = 'There is no route to follow.';
      return [{ type: 'blocked', message: this.message }];
    }
    if (this.status === 'paused') return this.resume();
    if (this.status === 'arrived' || this.status === 'stopped' || this.status === 'idle') {
      this.distance = 0;
      this.announced = new Set();
    }
    this.status = 'active';
    this.message = null;
    return [{ type: 'start', route_revision: this.revision }, ...this.announce()];
  }

  pause(): SimEvent[] {
    if (this.status !== 'active') return [];
    this.status = 'paused';
    return [{ type: 'pause' }];
  }

  resume(): SimEvent[] {
    if (this.status !== 'paused') return [];
    this.status = 'active';
    return [{ type: 'resume' }];
  }

  stop(): SimEvent[] {
    if (this.status === 'idle' || this.status === 'stopped') return [];
    this.status = 'stopped';
    return [{ type: 'stop' }];
  }

  /** Halt for a reroute; the position stays where it is. */
  hold(message: string): void {
    if (this.status === 'active' || this.status === 'paused') this.status = 'recomputing';
    this.message = message;
  }

  /** No valid route from here: stay put and say why. */
  block(message: string): SimEvent[] {
    this.status = 'blocked';
    this.message = message;
    return [{ type: 'blocked', message }];
  }

  setSpeed(factor: number): void {
    this.speed = Math.min(8, Math.max(0.25, factor));
  }

  /** Advance simulation time by `dt` seconds. */
  tick(dt: number): SimEvent[] {
    if (dt <= 0 || !Number.isFinite(dt)) return [];
    this.time += dt;
    if (this.status !== 'active') return [];
    this.distance = Math.min(this.total, this.distance + WALK_SPEED * this.speed * dt);
    const events = this.announce();
    if (this.distance >= this.total - 1e-9) {
      this.status = 'arrived';
      events.push({ type: 'arrive' });
    }
    return events;
  }

  private nextIndex(): number {
    const i = this.instructions.findIndex((ins) => ins.at_m > this.distance + 1e-6);
    return i === -1 ? Math.max(0, this.instructions.length - 1) : i;
  }

  private announce(): SimEvent[] {
    const events: SimEvent[] = [];
    this.instructions.forEach((ins, index) => {
      if (this.announced.has(index)) return;
      // Start and arrival are said when reached; everything else a few metres ahead.
      const due =
        ins.kind === 'start' || ins.kind === 'arrive'
          ? this.distance >= ins.at_m - 1e-6
          : ins.at_m - this.distance <= ANNOUNCE_M;
      if (!due) return;
      this.announced.add(index);
      events.push({ type: 'instruction', instruction: ins, index, key: `${this.revision}:${index}` });
    });
    return events;
  }

  snapshot(): SimSnapshot {
    return {
      status: this.status,
      route: this.route,
      route_revision: this.revision,
      instructions: this.instructions,
      distance_m: this.distance,
      total_m: this.total,
      speed: this.speed,
      progress: this.route ? progressAt(this.route, this.distance) : null,
      next: this.nextIndex(),
      message: this.message,
      sim_time_s: this.time,
    };
  }
}

/** Ids along the part of `route` still ahead of `distance` (to tell if a closure affects it). */
export function remainingElements(route: RouteResponse, distance: number): Set<string> {
  const ids = new Set<string>();
  for (const wp of route.waypoints ?? []) {
    if (wp.distance_m + 1e-6 < distance) continue;
    if (wp.element_id) ids.add(wp.element_id);
    if (wp.space_id) ids.add(wp.space_id);
  }
  return ids;
}
