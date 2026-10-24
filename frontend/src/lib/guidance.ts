// Phase B data contract (docs/PHASE_B_CONTRACT.md): what live guidance along a computed route
// reads and emits. Types plus one pure helper; no animation, camera or walking simulation here.
import type { Endpoint, RouteResponse, RouteTransition, RouteWaypoint } from './api';
import type { Point } from './building';

/** Where the user is. Today `simulated` / `map_click` / `room_id`; QR and indoor positioning later. */
export interface GuidanceLocation {
  source: 'simulated' | 'map_click' | 'room_id' | 'door_qr' | 'indoor_positioning';
  floor_id: string;
  space_id: string;
  position: Point;
  /** Direction the user faces/moves, building XY frame (0° = +x, counter-clockwise). */
  heading_deg?: number;
  accuracy_m?: number;
  /** ms since epoch. */
  at: number;
}

export type GuidanceStatus = 'idle' | 'active' | 'paused' | 'recomputing' | 'arrived' | 'off_route';

/** Progress along one route, derived from its waypoints only. */
export interface RouteProgress {
  distance_m: number;
  remaining_m: number;
  fraction: number;
  /** The last waypoint at or before `distance_m`. */
  waypoint_index: number;
  floor_id: string;
  space_id: string | null;
  position: Point;
  heading_deg: number;
  /** The next door, stairs or lift ahead, if any. */
  next_transition: RouteTransition | null;
  /** Walking distance to `next_transition` (null when none). */
  to_next_transition_m: number | null;
}

/** One guidance session: a route under a scenario revision, plus the user's live state. */
export interface GuidanceState {
  status: GuidanceStatus;
  start: Endpoint;
  target: Endpoint;
  route: RouteResponse;
  /** `route.revision`: the scenario revision the route was computed under. */
  scenario_revision: number;
  /** Increments on every recomputation within the session. */
  route_revision: number;
  location: GuidanceLocation | null;
  progress: RouteProgress | null;
}

export type RecomputeReason = 'scenario_changed' | 'off_route' | 'user_request' | 'settings_changed';

/** Events a guidance session emits (and a UI, voice or Phase B renderer consumes). */
export type GuidanceEvent =
  | { type: 'start'; at: number; route_revision: number; scenario_revision: number }
  | { type: 'pause'; at: number; progress: RouteProgress | null }
  | { type: 'resume'; at: number }
  | { type: 'progress'; at: number; progress: RouteProgress }
  | { type: 'transition_ahead'; at: number; transition: RouteTransition; distance_m: number }
  | { type: 'recompute'; at: number; reason: RecomputeReason; route_revision: number }
  | { type: 'arrive'; at: number; target: Endpoint }
  | { type: 'error'; at: number; message: string };

const lerp = (a: Point, b: Point, t: number): Point => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];

/**
 * Position, room, heading and next transition at `distance_m` along a route, interpolated
 * between its waypoints (vertical moves keep XY and switch floor at their end). Returns null for
 * routes without waypoints (unreachable).
 */
export function progressAt(route: RouteResponse, distance_m: number): RouteProgress | null {
  const points: RouteWaypoint[] = route.waypoints ?? [];
  if (!points.length) return null;
  const total = points[points.length - 1].distance_m;
  const d = Math.min(Math.max(distance_m, 0), total);
  let i = 0;
  while (i + 1 < points.length && points[i + 1].distance_m <= d) i++;
  const here = points[i];
  const next = points[i + 1];
  let position = here.position;
  if (next && next.floor_id === here.floor_id && next.distance_m > here.distance_m) {
    position = lerp(here.position, next.position, (d - here.distance_m) / (next.distance_m - here.distance_m));
  }
  const ahead = points.slice(i + 1).find((p) => p.element_id && ['door', 'stairs', 'lift', 'target'].includes(p.kind));
  const transition =
    ahead && ahead.kind !== 'target'
      ? ((route.transitions ?? []).find((t) => t.element_id === ahead.element_id) ?? null)
      : null;
  return {
    distance_m: d,
    remaining_m: total - d,
    fraction: total > 0 ? d / total : 1,
    waypoint_index: i,
    floor_id: here.floor_id,
    space_id: here.space_id ?? null,
    position,
    heading_deg: here.heading_deg,
    next_transition: transition,
    to_next_transition_m: transition && ahead ? ahead.distance_m - d : null,
  };
}
