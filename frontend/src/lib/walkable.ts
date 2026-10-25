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
/** Half the span of a door's pass band along its wall (at least this, else half its width), and
 * how far the band reaches into each room beyond the wall's own thickness. */
const MIN_DOOR_HALF_M = 0.45;
/** Edges this close to the nearest one count as the door's wall; the longest of them wins. */
const WALL_EDGE_TOL_M = 0.05;

interface WSpace {
  id: string;
  floor: string;
  type: string;
  poly: Point[];
}
/**
 * An opening's pass band: a rectangle centred on the door, `half` along its wall (the opening's
 * span) and `depth` across it (wall thickness + MIN_DOOR_HALF_M), oriented per side by the wall
 * edge of that side's polygon nearest to the door. Only inside this band may a person stand near
 * or beyond the wall; a wide opening therefore never reaches across parallel walls.
 */
interface WOpening {
  id: string;
  floor: string;
  a: string;
  b: string;
  position: Point;
  half: number;
  depth: number;
  /** Unit direction of the door's wall, per side space id. */
  along: Map<string, Point>;
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
    const along = new Map<string, Point>();
    let wall = 0;
    for (const sid of [a, b]) {
      const space = spaces.get(sid);
      if (!space) continue;
      along.set(sid, wallDirection(o.position, space.poly));
      wall += distanceToEdges(o.position, space.poly);
    }
    const w: WOpening = {
      id: o.id,
      floor: o.floor_id,
      a,
      b,
      position: o.position,
      half: Math.max((o.width_m || 0) / 2, MIN_DOOR_HALF_M),
      depth: wall + MIN_DOOR_HALF_M,
      along,
    };
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

/** Unit direction of the polygon edge a door at `p` sits in (nearest edge; among edges within
 * WALL_EDGE_TOL_M of the nearest, the longest, so a door near a corner takes the long wall). */
function wallDirection(p: Point, poly: Point[]): Point {
  const edges: { d: number; len: number; dir: Point }[] = [];
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [ax, ay] = poly[j];
    const [bx, by] = poly[i];
    const dx = bx - ax;
    const dy = by - ay;
    const len = Math.hypot(dx, dy);
    if (!len) continue;
    const t = Math.max(0, Math.min(1, ((p[0] - ax) * dx + (p[1] - ay) * dy) / (len * len)));
    edges.push({ d: Math.hypot(p[0] - (ax + t * dx), p[1] - (ay + t * dy)), len, dir: [dx / len, dy / len] });
  }
  if (!edges.length) return [1, 0];
  const nearest = Math.min(...edges.map((e) => e.d));
  const wall = edges.filter((e) => e.d <= nearest + WALL_EDGE_TOL_M).sort((x, y) => y.len - x.len)[0];
  return wall.dir;
}

/** `p` lies in the opening's pass band as seen from `space` (see WOpening). */
function inDoorBand(o: WOpening, space: string, p: Point): boolean {
  const u = o.along.get(space);
  if (!u) return false;
  const dx = p[0] - o.position[0];
  const dy = p[1] - o.position[1];
  return Math.abs(dx * u[0] + dy * u[1]) <= o.half && Math.abs(dy * u[0] - dx * u[1]) <= o.depth;
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
  const doors = usableDoors(world, s.space_id).filter((o) => inDoorBand(o, s.space_id, to));
  if (insidePolygon(to, here.poly)) {
    if (distanceToEdges(to, here.poly) >= radius || doors.length) return { ...s, position: to };
    return null; // would brush the wall
  }
  if (!doors.length) return null; // a wall, the outside, or a closed door
  for (const door of doors) {
    const other = door.a === s.space_id ? door.b : door.a;
    const next = world.spaces.get(other);
    if (next && insidePolygon(to, next.poly)) return { floor_id: s.floor_id, space_id: other, position: to };
  }
  // Inside the door gap itself (e.g. a thick wall): only within `half` of the door centre (the
  // band's corners would reach into the wall beside the frame), and never inside a third space:
  // that would be through a wall the opening does not belong to.
  if (!doors.some((o) => Math.hypot(to[0] - o.position[0], to[1] - o.position[1]) <= o.half)) return null;
  for (const space of world.spaces.values()) {
    if (space.floor === s.floor_id && space.id !== s.space_id && insidePolygon(to, space.poly)) return null;
  }
  return { ...s, position: to };
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
      usable: !world.closed.has(c.id) && (!stepFree || c.stepFree),
      reason: world.closed.has(c.id) ? 'closed' : stepFree && !c.stepFree ? 'not step-free' : null,
    }));
}

/** Ride a connector to `floor`: same XY when it lies in the arrival space, else an interior point
 * of it (`interiorPoint`). Refused when closed, not step-free for a step-free person, or not a stop. */
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
  return { floor_id: floor, space_id: stop.space, position: interiorPoint(space.poly) };
}

const INTERIOR_GRID = 24;

/** A point inside the polygon, as far from its walls as found: the area centroid when it lies
 * inside with body clearance, else the best of a grid sample (refined once around the best cell),
 * so a concave room never yields a point outside it. Deterministic. */
export function interiorPoint(poly: Point[]): Point {
  let area = 0;
  let cx = 0;
  let cy = 0;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const cross = poly[j][0] * poly[i][1] - poly[i][0] * poly[j][1];
    area += cross;
    cx += (poly[j][0] + poly[i][0]) * cross;
    cy += (poly[j][1] + poly[i][1]) * cross;
  }
  if (area) {
    const centroid: Point = [cx / (3 * area), cy / (3 * area)];
    if (insidePolygon(centroid, poly) && distanceToEdges(centroid, poly) >= BODY_RADIUS_M) return centroid;
  }
  const xs = poly.map((p) => p[0]);
  const ys = poly.map((p) => p[1]);
  let box = { x0: Math.min(...xs), y0: Math.min(...ys), x1: Math.max(...xs), y1: Math.max(...ys) };
  let best: Point | null = null;
  let bestClear = -1;
  for (let pass = 0; pass < 2; pass++) {
    const dx = (box.x1 - box.x0) / INTERIOR_GRID;
    const dy = (box.y1 - box.y0) / INTERIOR_GRID;
    for (let i = 0; i < INTERIOR_GRID; i++) {
      for (let j = 0; j < INTERIOR_GRID; j++) {
        const p: Point = [box.x0 + (i + 0.5) * dx, box.y0 + (j + 0.5) * dy];
        if (!insidePolygon(p, poly)) continue;
        const clear = distanceToEdges(p, poly);
        if (clear > bestClear) [best, bestClear] = [p, clear];
      }
    }
    if (!best) break;
    box = { x0: best[0] - dx, y0: best[1] - dy, x1: best[0] + dx, y1: best[1] + dy };
  }
  return best ?? poly[0];
}
