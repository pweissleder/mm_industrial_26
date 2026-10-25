// Layout editing: pure operations on a BuildingModel's assets and openings (doors). Stairs and
// lifts are not editable. Every edit returns a new model, so the app can keep the previous
// ones for undo and save the result as building.json.
import {
  centroid,
  humanize,
  pointInPolygon,
  polygonArea,
  type Asset,
  type BuildingModel,
  type Opening,
  type Point,
  type Space,
} from './building';

export const OUTSIDE = 'outside';

/** Stair and lift spaces belong to the vertical connectors and stay as they are. */
export const isLockedSpace = (s: Space) =>
  s.type === 'stair' || s.type === 'lift' || s.room_type === 'stairwell' || s.room_type === 'lift';

export interface CatalogItem {
  type: string;
  category: string;
  width_m: number;
  depth_m: number;
}

// Default footprints (m) for items a room may not have yet.
const PRESETS: CatalogItem[] = [
  { type: 'desk', category: 'furniture', width_m: 1.6, depth_m: 0.8 },
  { type: 'office_chair', category: 'furniture', width_m: 0.6, depth_m: 0.6 },
  { type: 'chair', category: 'furniture', width_m: 0.5, depth_m: 0.5 },
  { type: 'meeting_table', category: 'furniture', width_m: 2.4, depth_m: 1.2 },
  { type: 'bench', category: 'furniture', width_m: 1.5, depth_m: 0.4 },
  { type: 'bookshelf', category: 'furniture', width_m: 1.0, depth_m: 0.4 },
  { type: 'filing_cabinet', category: 'furniture', width_m: 0.5, depth_m: 0.6 },
  { type: 'locker', category: 'furniture', width_m: 0.4, depth_m: 0.5 },
  { type: 'plant', category: 'furniture', width_m: 0.5, depth_m: 0.5 },
  { type: 'whiteboard', category: 'fixture', width_m: 1.8, depth_m: 0.1 },
  { type: 'fire_extinguisher', category: 'fixture', width_m: 0.3, depth_m: 0.3 },
  { type: 'display_screen', category: 'equipment', width_m: 1.4, depth_m: 0.2 },
  { type: 'server_rack', category: 'equipment', width_m: 0.6, depth_m: 1.0 },
  { type: 'pallet_rack', category: 'equipment', width_m: 2.7, depth_m: 1.1 },
  { type: 'conveyor', category: 'equipment', width_m: 4.0, depth_m: 0.8 },
  { type: 'forklift', category: 'equipment', width_m: 2.5, depth_m: 1.2 },
  { type: 'pallet_jack', category: 'equipment', width_m: 1.6, depth_m: 0.6 },
  { type: 'cnc_mill', category: 'machine', width_m: 3.0, depth_m: 2.2 },
  { type: 'injection_moulding_machine', category: 'machine', width_m: 5.0, depth_m: 1.8 },
  { type: 'press_brake', category: 'machine', width_m: 3.5, depth_m: 1.8 },
  { type: 'industrial_3d_printer', category: 'machine', width_m: 1.5, depth_m: 1.2 },
];

/** Item types to add: the presets plus every type already in the building (its footprint wins). */
export function catalog(model: BuildingModel | null): CatalogItem[] {
  const items = new Map(PRESETS.map((p) => [p.type, p]));
  const seen = new Set<string>();
  for (const a of model?.assets ?? []) {
    if (seen.has(a.type)) continue;
    seen.add(a.type);
    items.set(a.type, {
      type: a.type,
      category: a.category,
      width_m: a.footprint.width_m ?? 0.6,
      depth_m: a.footprint.depth_m ?? 0.6,
    });
  }
  return [...items.values()].sort(
    (a, b) => a.category.localeCompare(b.category) || a.type.localeCompare(b.type),
  );
}

const round = (v: number) => Math.round(v * 1000) / 1000;

/** The smallest room on `floorId` containing `p`, so a zone inside a hall wins over the hall. */
export function spaceAt(model: BuildingModel, floorId: string, p: Point): Space | undefined {
  let best: Space | undefined;
  let bestArea = Infinity;
  for (const s of model.spaces) {
    if (s.floor_id !== floorId || s.polygon.length < 3 || !pointInPolygon(p, s.polygon)) continue;
    const area = Math.abs(polygonArea(s.polygon));
    if (area < bestArea) [best, bestArea] = [s, area];
  }
  return best;
}

/** A point inside the room: its centroid, or (for L- and U-shapes) the inner grid point nearest to it. */
export function pointInside(space: Space): Point {
  const c = centroid(space.polygon);
  if (pointInPolygon(c, space.polygon)) return c;
  const xs = space.polygon.map((p) => p[0]);
  const ys = space.polygon.map((p) => p[1]);
  const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
  let best: Point = space.polygon[0];
  let bestD = Infinity;
  for (let i = 1; i < 20; i++) {
    for (let j = 1; j < 20; j++) {
      const p: Point = [x0 + ((x1 - x0) * i) / 20, y0 + ((y1 - y0) * j) / 20];
      const d = Math.hypot(p[0] - c[0], p[1] - c[1]);
      if (d < bestD && pointInPolygon(p, space.polygon)) [best, bestD] = [p, d];
    }
  }
  return best;
}

export type AssetPatch = Partial<Pick<Asset, 'name' | 'position'>> & {
  footprint?: Partial<Asset['footprint']>;
};

/** Change an asset; a new position also moves it to the room it now stands in. */
export function updateAsset(model: BuildingModel, id: string, patch: AssetPatch): BuildingModel {
  return {
    ...model,
    assets: model.assets.map((a) => {
      if (a.id !== id) return a;
      const next: Asset = { ...a, ...patch, footprint: { ...a.footprint, ...patch.footprint } };
      if (patch.position) {
        next.position = [round(patch.position[0]), round(patch.position[1])];
        next.space_id = spaceAt(model, a.floor_id, next.position)?.id ?? a.space_id;
      }
      return next;
    }),
  };
}

/**
 * Move an asset. A constrained drag passes the room it ended in (wins over the lookup) and
 * the rotation that lines it up with that room's walls.
 */
export function moveAsset(
  model: BuildingModel,
  id: string,
  position: Point,
  { spaceId, rotation }: { spaceId?: string; rotation?: number } = {},
): BuildingModel {
  const next = updateAsset(
    model,
    id,
    rotation === undefined ? { position } : { position, footprint: { rotation_deg: rotation } },
  );
  if (!spaceId) return next;
  return { ...next, assets: next.assets.map((a) => (a.id === id ? { ...a, space_id: spaceId } : a)) };
}

// ---------------------------------------------------------------- wall alignment

const normDeg = (deg: number) => round(((deg % 360) + 360) % 360) % 360;

/** Direction (deg, building frame) of the room wall nearest to `p`. */
export function wallAngleAt(space: Space, [px, py]: Point): number | null {
  let best: number | null = null;
  let bestD = Infinity;
  const poly = space.polygon;
  for (let i = 0; i < poly.length; i++) {
    const [ax, ay] = poly[i];
    const [bx, by] = poly[(i + 1) % poly.length];
    const len2 = (bx - ax) ** 2 + (by - ay) ** 2;
    if (len2 < 0.25) continue; // ignore tiny jogs in the outline
    const t = Math.max(0, Math.min(1, ((px - ax) * (bx - ax) + (py - ay) * (by - ay)) / len2));
    const d = Math.hypot(px - (ax + t * (bx - ax)), py - (ay + t * (by - ay)));
    if (d < bestD) [best, bestD] = [(Math.atan2(by - ay, bx - ax) * 180) / Math.PI, d];
  }
  return best;
}

/** `rotation` turned by the least amount (a multiple of 90° from the wall) so the item's sides run along the wall. */
export function alignRotation(rotation: number, wallDeg: number): number {
  return normDeg(wallDeg + 90 * Math.round((rotation - wallDeg) / 90));
}

/** The rotation lining `a` up with the walls of `space` near `p` (unchanged without walls). */
function alignedIn(a: Asset, space: Space, p: Point): number {
  const rotation = a.footprint.rotation_deg ?? 0;
  const wall = wallAngleAt(space, p);
  return wall === null ? rotation : alignRotation(rotation, wall);
}

const withRotation = (a: Asset, rotation_deg: number): Asset => ({
  ...a,
  footprint: { ...a.footprint, rotation_deg },
});

/** Line an asset up with the nearest wall of its room. */
export function alignAsset(model: BuildingModel, id: string): BuildingModel {
  const a = model.assets.find((x) => x.id === id);
  const space = a && model.spaces.find((s) => s.id === a.space_id);
  if (!a || !space) return model;
  return updateAsset(model, id, { footprint: { rotation_deg: alignedIn(a, space, a.position) } });
}

// ---------------------------------------------------------------- constrained item moves

/** Halls and corridors have no walls between them (as drawn in the 3D view). */
const OPEN_TYPES = new Set(['hall', 'corridor']);
/** Gap kept between an item and the wall (m): walls are drawn just inside the room outline. */
const CLEARANCE = 0.12;
/** How far past a wall the pointer must go before the item changes room (m). */
export const PASS_M = 0.8;

export interface ItemDrag {
  /** The room the item is kept in. */
  spaceId: string;
  /** The whole footprint must stay inside; off when it didn't fit to begin with. */
  strict: boolean;
  /** No constraint at all: the item started outside every room. */
  free: boolean;
}

/** Footprint outline points (corners, edge midpoints, centre) around `p`, with clearance. */
function footprintPoints(a: Asset, [px, py]: Point): Point[] {
  const hw = (a.footprint.width_m ?? 0.6) / 2 + CLEARANCE;
  const hd = (a.footprint.depth_m ?? 0.6) / 2 + CLEARANCE;
  const r = ((a.footprint.rotation_deg ?? 0) * Math.PI) / 180;
  const [c, s] = [Math.cos(r), Math.sin(r)];
  const points: Point[] = [];
  for (const [lx, lz] of [[-1, -1], [0, -1], [1, -1], [1, 0], [1, 1], [0, 1], [-1, 1], [-1, 0], [0, 0]]) {
    const [x, z] = [lx * hw, lz * hd];
    points.push([px + x * c + z * s, py + x * s - z * c]);
  }
  return points;
}

function distanceToPolygon([px, py]: Point, polygon: Point[]): number {
  let best = Infinity;
  for (let i = 0; i < polygon.length; i++) {
    const [ax, ay] = polygon[i];
    const [bx, by] = polygon[(i + 1) % polygon.length];
    const len2 = (bx - ax) ** 2 + (by - ay) ** 2 || 1;
    const t = Math.max(0, Math.min(1, ((px - ax) * (bx - ax) + (py - ay) * (by - ay)) / len2));
    best = Math.min(best, Math.hypot(px - (ax + t * (bx - ax)), py - (ay + t * (by - ay))));
  }
  return best;
}

/** Whether `p` lies in the drag's room, or in an open area joined to an open room. */
function inRegion(model: BuildingModel, floorId: string, room: Space, p: Point): boolean {
  const s = spaceAt(model, floorId, p);
  if (!s || isLockedSpace(s)) return false;
  return s.id === room.id || (OPEN_TYPES.has(room.type) && OPEN_TYPES.has(s.type));
}

function fits(model: BuildingModel, a: Asset, room: Space, state: ItemDrag, p: Point): boolean {
  if (state.free) return true;
  const points = state.strict ? footprintPoints(a, p) : [p];
  return points.every((q) => inRegion(model, a.floor_id, room, q));
}

export function startItemDrag(model: BuildingModel, id: string): ItemDrag | null {
  const a = model.assets.find((x) => x.id === id);
  if (!a) return null;
  const here = spaceAt(model, a.floor_id, a.position);
  const room = here && !isLockedSpace(here) ? here : model.spaces.find((s) => s.id === a.space_id);
  if (!room || !pointInPolygon(a.position, room.polygon)) return { spaceId: a.space_id, strict: false, free: true };
  const state: ItemDrag = { spaceId: room.id, strict: true, free: false };
  return { ...state, strict: fits(model, a, room, state, a.position) };
}

/**
 * Where an item dragged from `from` towards `to` may go: walls stop it (it slides along
 * them), but once the pointer is PASS_M past the wall inside another room it moves there.
 */
export function constrainItemMove(
  model: BuildingModel,
  id: string,
  state: ItemDrag,
  from: Point,
  to: Point,
): { position: Point; rotation: number; state: ItemDrag } {
  const original = model.assets.find((x) => x.id === id);
  const room = model.spaces.find((s) => s.id === state.spaceId);
  if (!original || !room || state.free) {
    return { position: to, rotation: original?.footprint.rotation_deg ?? 0, state };
  }

  // Far enough into a neighbouring (walled-off) room: change rooms.
  const target = spaceAt(model, original.floor_id, to);
  if (
    target &&
    target.id !== room.id &&
    !isLockedSpace(target) &&
    !inRegion(model, original.floor_id, room, to) &&
    distanceToPolygon(to, room.polygon) >= PASS_M
  ) {
    const next: ItemDrag = { spaceId: target.id, strict: true, free: false };
    // Line it up with the new room's walls before fitting it in.
    const rotation = alignedIn(original, target, to);
    const a = withRotation(original, rotation);
    const entry = pointInside(target);
    if (!fits(model, a, target, next, entry)) next.strict = false;
    return { position: furthest(model, a, target, next, entry, to), rotation, state: next };
  }

  // Kept along the walls of this room (a no-op when it already is), unless turning it where
  // it stands would push it into a wall.
  const aligned = withRotation(original, alignedIn(original, room, from));
  const a = fits(model, aligned, room, state, from) ? aligned : original;
  const rotation = a.footprint.rotation_deg ?? 0;

  // Slide: as far as possible along the move, then along x, then along y.
  const p1 = furthest(model, a, room, state, from, to);
  const p2 = furthest(model, a, room, state, p1, [to[0], p1[1]]);
  const p3 = furthest(model, a, room, state, p2, [p2[0], to[1]]);
  // Open areas: the item belongs to whichever of them it now stands in.
  const now = spaceAt(model, a.floor_id, p3);
  const spaceId = now && inRegion(model, a.floor_id, room, p3) ? now.id : room.id;
  return { position: p3, rotation, state: { ...state, spaceId } };
}

/** The last point on from → to where the item still fits (binary search). */
function furthest(model: BuildingModel, a: Asset, room: Space, state: ItemDrag, from: Point, to: Point): Point {
  if (fits(model, a, room, state, to)) return to;
  if (!fits(model, a, room, state, from)) return from;
  let [lo, hi] = [0, 1];
  for (let i = 0; i < 14; i++) {
    const mid = (lo + hi) / 2;
    const p: Point = [from[0] + (to[0] - from[0]) * mid, from[1] + (to[1] - from[1]) * mid];
    if (fits(model, a, room, state, p)) lo = mid;
    else hi = mid;
  }
  return [from[0] + (to[0] - from[0]) * lo, from[1] + (to[1] - from[1]) * lo];
}

export function rotateAsset(model: BuildingModel, id: string, deltaDeg: number): BuildingModel {
  const a = model.assets.find((x) => x.id === id);
  if (!a) return model;
  return updateAsset(model, id, { footprint: { rotation_deg: normDeg((a.footprint.rotation_deg ?? 0) + deltaDeg) } });
}

export const deleteAsset = (model: BuildingModel, id: string): BuildingModel => ({
  ...model,
  assets: model.assets.filter((a) => a.id !== id),
});

/** Next free id in the dataset's `<building>-A00042` scheme. */
function nextAssetId(model: BuildingModel): string {
  const taken = new Set(model.assets.map((a) => a.id));
  let n = 0;
  for (const a of model.assets) {
    const m = /-A(\d+)$/.exec(a.id);
    if (m) n = Math.max(n, Number(m[1]));
  }
  let id: string;
  do id = `${model.building.id}-A${String(++n).padStart(5, '0')}`;
  while (taken.has(id));
  return id;
}

/** Asset names are unique per building: `Desk 2.03`, then `Desk 2.03-B`, `Desk 2.03-C`, … */
function uniqueName(model: BuildingModel, base: string): string {
  const taken = new Set(model.assets.map((a) => a.name));
  if (!taken.has(base)) return base;
  for (let n = 1; ; n++) {
    // Bijective base 26 from B: 1 -> B, 25 -> Z, 26 -> AA.
    let suffix = '';
    for (let k = n + 1; k > 0; k = Math.floor(k / 26)) {
      k--;
      suffix = String.fromCharCode(65 + (k % 26)) + suffix;
    }
    const name = `${base}-${suffix}`;
    if (!taken.has(name)) return name;
  }
}

export function addAsset(
  model: BuildingModel,
  spaceId: string,
  item: CatalogItem,
  at?: Point,
): { model: BuildingModel; id: string } {
  const space = model.spaces.find((s) => s.id === spaceId);
  if (!space) return { model, id: '' };
  const id = nextAssetId(model);
  const position = at ?? pointInside(space);
  const asset: Asset = {
    id,
    name: uniqueName(model, `${humanize(item.type)} ${space.ref || space.name}`),
    category: item.category,
    type: item.type,
    space_id: space.id,
    floor_id: space.floor_id,
    position: [round(position[0]), round(position[1])],
    // Sides along the nearest wall, so it sits square in the room.
    footprint: {
      width_m: item.width_m,
      depth_m: item.depth_m,
      rotation_deg: alignRotation(0, wallAngleAt(space, position) ?? 0),
    },
    source: 'user',
  };
  return { model: { ...model, assets: [...model.assets, asset] }, id };
}

/** A copy next to the original (offset along x), in whichever room that lands. */
export function duplicateAsset(model: BuildingModel, id: string): { model: BuildingModel; id: string } {
  const a = model.assets.find((x) => x.id === id);
  if (!a) return { model, id: '' };
  const newId = nextAssetId(model);
  const base = a.name.replace(/-[A-Z]+$/, '');
  // Side by side along its own width, so a row of copies stays in line.
  const offset = (a.footprint.width_m ?? 0.6) + 0.2;
  const r = ((a.footprint.rotation_deg ?? 0) * Math.PI) / 180;
  const position: Point = [
    round(a.position[0] + offset * Math.cos(r)),
    round(a.position[1] + offset * Math.sin(r)),
  ];
  const copy: Asset = {
    ...a,
    id: newId,
    name: uniqueName(model, base),
    position,
    space_id: spaceAt(model, a.floor_id, position)?.id ?? a.space_id,
    footprint: { ...a.footprint },
    source: 'user',
  };
  return { model: { ...model, assets: [...model.assets, copy] }, id: newId };
}

// ---------------------------------------------------------------- openings (doors)

/** How far either side of a wall to look for the space behind it (m). */
const PROBE = 0.3;

/** The point on the nearest room edge of the floor, with that edge's unit normal. */
function nearestWall(
  model: BuildingModel,
  floorId: string,
  [px, py]: Point,
): { point: Point; normal: Point } | null {
  let best: { point: Point; normal: Point } | null = null;
  let bestD = Infinity;
  for (const s of model.spaces) {
    if (s.floor_id !== floorId || s.polygon.length < 3) continue;
    for (let i = 0; i < s.polygon.length; i++) {
      const [ax, ay] = s.polygon[i];
      const [bx, by] = s.polygon[(i + 1) % s.polygon.length];
      const len = Math.hypot(bx - ax, by - ay);
      if (len < 0.05) continue;
      const [ux, uy] = [(bx - ax) / len, (by - ay) / len];
      const t = Math.max(0, Math.min(len, (px - ax) * ux + (py - ay) * uy));
      const point: Point = [ax + ux * t, ay + uy * t];
      const d = Math.hypot(px - point[0], py - point[1]);
      if (d < bestD) [best, bestD] = [{ point, normal: [-uy, ux] }, d];
    }
  }
  return best;
}

/** The spaces on both sides of a wall point; a side with no space is `outside`. */
function sidesOf(model: BuildingModel, floorId: string, point: Point, normal: Point): string[] {
  const side = (k: number) =>
    spaceAt(model, floorId, [point[0] + normal[0] * PROBE * k, point[1] + normal[1] * PROBE * k])?.id ??
    OUTSIDE;
  return [...new Set([side(1), side(-1)])].sort((a, b) => Number(a === OUTSIDE) - Number(b === OUTSIDE));
}

/** An opening to the outside is an entrance; an entrance moved indoors becomes a door. */
const kindFor = (kind: string, spaceIds: string[]) =>
  spaceIds.includes(OUTSIDE) ? 'entrance' : kind === 'entrance' ? 'door' : kind;

/**
 * Move an opening: it snaps onto the nearest wall and joins the spaces on both sides of it.
 * Returns the model unchanged when there is no wall between two different spaces there.
 */
export function moveOpening(model: BuildingModel, id: string, at: Point): BuildingModel {
  const o = model.openings.find((x) => x.id === id);
  const wall = o && nearestWall(model, o.floor_id, at);
  if (!o || !wall) return model;
  const space_ids = sidesOf(model, o.floor_id, wall.point, wall.normal);
  if (space_ids.length < 2) return model;
  const next: Opening = {
    ...o,
    position: [round(wall.point[0]), round(wall.point[1])],
    space_ids,
    kind: kindFor(o.kind, space_ids),
  };
  return { ...model, openings: model.openings.map((x) => (x.id === id ? next : x)) };
}

export type OpeningPatch = Partial<Pick<Opening, 'width_m' | 'kind' | 'step_free'>>;

export function updateOpening(model: BuildingModel, id: string, patch: OpeningPatch): BuildingModel {
  return {
    ...model,
    openings: model.openings.map((o) => {
      if (o.id !== id) return o;
      const next = { ...o, ...patch };
      if (patch.width_m !== undefined) next.width_m = round(Math.max(0.3, patch.width_m));
      next.kind = kindFor(next.kind, next.space_ids);
      return next;
    }),
  };
}

export const deleteOpening = (model: BuildingModel, id: string): BuildingModel => ({
  ...model,
  openings: model.openings.filter((o) => o.id !== id),
});

export interface Neighbour {
  /** A space id, or `outside`. */
  id: string;
  name: string;
  /** Where a new door to it goes: the middle of the shared wall. */
  position: Point;
}

/** Spaces sharing a wall with `spaceId` (and the outside), each with a spot for a door. */
export function neighbours(model: BuildingModel, spaceId: string): Neighbour[] {
  const space = model.spaces.find((s) => s.id === spaceId);
  if (!space || space.polygon.length < 3) return [];
  const outward = -(Math.sign(polygonArea(space.polygon)) || 1); // CCW: interior on the left
  const samples = new Map<string, Point[]>();
  const poly = space.polygon;
  for (let i = 0; i < poly.length; i++) {
    const [ax, ay] = poly[i];
    const [bx, by] = poly[(i + 1) % poly.length];
    const len = Math.hypot(bx - ax, by - ay);
    if (len < 0.6) continue;
    const [ux, uy] = [(bx - ax) / len, (by - ay) / len];
    const [nx, ny] = [-uy * outward, ux * outward];
    // Every half metre, keeping clear of the corners.
    for (let t = 0.4; t <= len - 0.4; t += 0.5) {
      const p: Point = [ax + ux * t, ay + uy * t];
      const other = spaceAt(model, space.floor_id, [p[0] + nx * PROBE, p[1] + ny * PROBE]);
      const id = other ? other.id : OUTSIDE;
      if (id === spaceId) continue;
      samples.set(id, [...(samples.get(id) ?? []), p]);
    }
  }
  const nameOf = (id: string) =>
    id === OUTSIDE ? 'Outside (entrance)' : (model.spaces.find((s) => s.id === id)?.name ?? id);
  return [...samples]
    .map(([id, points]) => {
      const [x, y] = points[Math.floor(points.length / 2)];
      return { id, name: nameOf(id), position: [round(x), round(y)] as Point };
    })
    .sort((a, b) => Number(a.id === OUTSIDE) - Number(b.id === OUTSIDE) || a.name.localeCompare(b.name));
}

/** Next free id in the dataset's `<floor>-O0042` scheme (numbers are unique per building). */
function nextOpeningId(model: BuildingModel, floorId: string): string {
  const taken = new Set(model.openings.map((o) => o.id));
  let n = 0;
  for (const o of model.openings) {
    const m = /-O(\d+)$/.exec(o.id);
    if (m) n = Math.max(n, Number(m[1]));
  }
  let id: string;
  do id = `${floorId}-O${String(++n).padStart(4, '0')}`;
  while (taken.has(id));
  return id;
}

/** A door from `spaceId` to a neighbour (or the outside), in the middle of their shared wall. */
export function addOpening(
  model: BuildingModel,
  spaceId: string,
  to: string,
): { model: BuildingModel; id: string } {
  const space = model.spaces.find((s) => s.id === spaceId);
  const spot = neighbours(model, spaceId).find((n) => n.id === to);
  if (!space || !spot) return { model, id: '' };
  const id = nextOpeningId(model, space.floor_id);
  const space_ids = to === OUTSIDE ? [spaceId, OUTSIDE] : [spaceId, to];
  const opening: Opening = {
    id,
    floor_id: space.floor_id,
    space_ids,
    kind: kindFor('door', space_ids),
    position: spot.position,
    width_m: to === OUTSIDE ? 1.2 : 0.9,
    step_free: true,
    synthetic: false,
  };
  return { model: { ...model, openings: [...model.openings, opening] }, id };
}

// ---------------------------------------------------------------- saving

type Raw = Record<string, unknown>;

/**
 * The dataset's original building.json with its assets and openings replaced. The served
 * model also carries scenario state (`closed` flags), which must not be baked into the file;
 * an element keeps the `closed` value its file entry had.
 */
export function toBuildingFile(
  original: Raw,
  model: Pick<BuildingModel, 'assets' | 'openings'>,
  copy?: { id: string; name: string },
): File {
  // A copy is a building of its own: the backend stores buildings by id.
  const building = copy ? { ...(original.building as Raw), id: copy.id, name: copy.name } : original.building;
  const originalOpenings = new Map(
    ((original.openings as Raw[] | undefined) ?? []).map((o) => [o.id as string, o]),
  );
  const file = {
    ...original,
    building,
    openings: model.openings.map((o) => {
      const before = originalOpenings.get(o.id);
      return {
        id: o.id,
        floor_id: o.floor_id,
        space_ids: o.space_ids,
        kind: o.kind,
        position: o.position,
        width_m: o.width_m,
        step_free: o.step_free,
        synthetic: o.synthetic ?? false,
        ...(before && 'closed' in before ? { closed: before.closed } : {}),
      };
    }),
    assets: model.assets.map((a) => ({
      id: a.id,
      name: a.name,
      category: a.category,
      type: a.type,
      space_id: a.space_id,
      floor_id: a.floor_id,
      position: a.position,
      footprint: a.footprint,
      source: a.source ?? 'user',
    })),
  };
  return new File([JSON.stringify(file, null, 1)], 'building.json', { type: 'application/json' });
}
