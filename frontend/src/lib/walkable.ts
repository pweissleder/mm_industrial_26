// Manual walking (night task §5 "game controls"): move a person inside the building's real room
// polygons. A move stays inside the current space (keeping a body radius off walls) and changes
// space only through an open, not closed opening into a neighbouring space; never outside, never
// through a wall. Moves are split into short sub-steps, so a long frame or high speed cannot
// tunnel through a wall. Stairs/lifts change floors only via their connector (schematic), and a
// step-free person can never take stairs. Pure functions, deterministic, no geometry invented.
import type { Point } from './building';
import type { BuildingModel } from './building';

export const BODY_RADIUS_M = 0.25;
const SUB_STEP_M = 0.05;
/** How far around a door's centre a person may pass (at least this, else half its width). */
const MIN_DOOR_HALF_M = 0.45;

interface WSpace {
  id: string;
  floor: string;
  type: string;
  poly: Point[];
}
interface WOpening {
  id: string;
  floor: string;
  a: string;
  b: string;
  position: Point;
  half: number;
}
export interface WalkWorld {
  spaces: Map<string, WSpace>;
  openings: Map<string, WOpening[]>;
  connectors: { id: string; kind: string; name: string; stops: { floor: string; space: string }[]; stepFree: boolean }[];
  closed: Set<string>;
}

export interface WalkState {
  floor_id: string;
  space_id: string;
  position: Point;
}

export function buildWorld(model: BuildingModel, closed: readonly string[] = []): WalkWorld {
  const spaces = new Map<string, WSpace>();
  for (const s of model.spaces) spaces.set(s.id, { id: s.id, floor: s.floor_id, type: s.type, poly: s.polygon });
  const openings = new Map<string, WOpening[]>();
  for (const o of model.openings) {
    if (o.space_ids.length < 2) continue;
    const [a, b] = o.space_ids;
    const w: WOpening = { id: o.id, floor: o.floor_id, a, b, position: o.position, half: Math.max(o.width_m / 2, MIN_DOOR_HALF_M) };
    for (const sid of [a, b]) openings.set(sid, [...(openings.get(sid) ?? []), w]);
  }
  const connectors = model.vertical_connectors.map((c) => ({
    id: c.id,
    kind: c.kind,
    name: c.name,
    stepFree: c.step_free,
    stops: c.floor_ids.map((floor, i) => ({ floor, space: c.space_ids[i] })),
  }));
  return { spaces, openings, connectors, closed: new Set([...closed, ...(model.closed ?? [])]) };
}

export function insidePolygon(p: Point, poly: Point[]): boolean {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i];
    const [xj, yj] = poly[j];
    if (yi > p[1] !== yj > p[1] && p[0] < ((xj - xi) * (p[1] - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

export function distanceToEdges(p: Point, poly: Point[]): number {
  let best = Infinity;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [ax, ay] = poly[j];
    const [bx, by] = poly[i];
    const dx = bx - ax;
    const dy = by - ay;
    const len2 = dx * dx + dy * dy || 1;
    const t = Math.max(0, Math.min(1, ((p[0] - ax) * dx + (p[1] - ay) * dy) / len2));
    best = Math.min(best, Math.hypot(p[0] - (ax + t * dx), p[1] - (ay + t * dy)));
  }
  return best;
}

/** The space on `floor` that contains `p` (rooms before corridors when polygons overlap). */
export function locate(world: WalkWorld, floor: string, p: Point): string | null {
  let hit: string | null = null;
  for (const s of world.spaces.values()) {
    if (s.floor !== floor || !insidePolygon(p, s.poly)) continue;
    if (!hit || s.type === 'room') hit = s.id;
  }
  return hit;
}

function usableDoors(world: WalkWorld, space: string): WOpening[] {
  return (world.openings.get(space) ?? []).filter(
    (o) => !world.closed.has(o.id) && o.a !== 'outside' && o.b !== 'outside' && !world.closed.has(o.a === space ? o.b : o.a),
  );
}

function subStep(world: WalkWorld, s: WalkState, to: Point, radius: number): WalkState | null {
  const here = world.spaces.get(s.space_id);
  if (!here) return null;
  const doors = usableDoors(world, s.space_id);
  const nearDoor = (p: Point) => doors.find((o) => Math.hypot(p[0] - o.position[0], p[1] - o.position[1]) <= o.half);
  if (insidePolygon(to, here.poly)) {
    if (distanceToEdges(to, here.poly) >= radius || nearDoor(to)) return { ...s, position: to };
    return null; // would brush the wall
  }
  const door = nearDoor(to);
  if (!door) return null; // a wall, the outside, or a closed door
  const other = door.a === s.space_id ? door.b : door.a;
  const next = world.spaces.get(other);
  if (next && insidePolygon(to, next.poly)) return { floor_id: s.floor_id, space_id: other, position: to };
  return { ...s, position: to }; // inside the door gap itself
}

/**
 * Move by `delta` (building metres). Returns the reached state and whether any part was blocked.
 * Blocked moves slide along the wall (x or y component) where that stays valid.
 */
export function move(
  world: WalkWorld,
  state: WalkState,
  delta: Point,
  radius = BODY_RADIUS_M,
): { state: WalkState; blocked: boolean } {
  const length = Math.hypot(delta[0], delta[1]);
  if (!length) return { state, blocked: false };
  const n = Math.max(1, Math.ceil(length / SUB_STEP_M));
  const step: Point = [delta[0] / n, delta[1] / n];
  let s = state;
  let blocked = false;
  for (let i = 0; i < n; i++) {
    const tries: Point[] = [step, [step[0], 0], [0, step[1]]];
    let moved: WalkState | null = null;
    for (const t of tries) {
      if (!t[0] && !t[1]) continue;
      moved = subStep(world, s, [s.position[0] + t[0], s.position[1] + t[1]], radius);
      if (moved) {
        if (t !== step) blocked = true;
        break;
      }
    }
    if (!moved) return { state: s, blocked: true };
    s = moved;
  }
  return { state: s, blocked };
}

/** Stairs/lifts usable from the current space, with the floors they reach. */
export function connectorsHere(world: WalkWorld, state: WalkState, stepFree: boolean) {
  return world.connectors
    .filter((c) => c.stops.some((st) => st.space === state.space_id))
    .map((c) => ({
      ...c,
      usable: !world.closed.has(c.id) && (!stepFree || c.stepFree || c.kind === 'lift'),
      reason: world.closed.has(c.id) ? 'closed' : stepFree && c.kind === 'stairs' && !c.stepFree ? 'not step-free' : null,
    }));
}

/** Ride a connector to `floor`: same XY when it lies in the arrival space, else its first vertex
 * nudged inside. Refused when closed, not step-free for a step-free person, or not a stop. */
export function ride(
  world: WalkWorld,
  state: WalkState,
  connectorId: string,
  floor: string,
  stepFree: boolean,
): WalkState | null {
  const c = connectorsHere(world, state, stepFree).find((x) => x.id === connectorId);
  const stop = c?.stops.find((st) => st.floor === floor);
  if (!c || !c.usable || !stop) return null;
  const space = world.spaces.get(stop.space);
  if (!space) return null;
  if (insidePolygon(state.position, space.poly)) return { floor_id: floor, space_id: stop.space, position: state.position };
  const cx = space.poly.reduce((a, p) => a + p[0], 0) / space.poly.length;
  const cy = space.poly.reduce((a, p) => a + p[1], 0) / space.poly.length;
  return { floor_id: floor, space_id: stop.space, position: [cx, cy] };
}
