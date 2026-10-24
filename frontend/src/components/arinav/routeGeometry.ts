// What to draw for a RouteResponse, independent of three.js so it can be unit-tested.
import type { RouteResponse } from '../../lib/api';
import type { Point } from '../../lib/building';

export type RouteStyle = 'official' | 'precision';

export interface RouteDrawing {
  style: RouteStyle;
  /** Polylines per floor, drawn at that floor's elevation. */
  lines: { floorId: string; points: Point[] }[];
  /** Stairs/lift moves: a vertical segment at `position`, dashed; `schematic` is labelled. */
  vertical: { kind: string; position: Point; fromFloorId: string; toFloorId: string; schematic: boolean }[];
  /** Doors passed on the same floor (exit doors are told apart by the scene's model). */
  doors: { elementId: string; floorId: string; position: Point }[];
  start: { floorId: string; position: Point } | null;
  target: { floorId: string; position: Point } | null;
}

export function routeDrawing(route: RouteResponse): RouteDrawing {
  const lines = (route.path ?? [])
    .filter((seg) => seg.points?.length >= 2)
    .map((seg) => ({ floorId: seg.floor_id, points: seg.points }));
  const vertical: RouteDrawing['vertical'] = [];
  const doors: RouteDrawing['doors'] = [];
  for (const t of route.transitions ?? []) {
    if (t.kind === 'door') {
      doors.push({ elementId: t.element_id, floorId: t.from_floor_id, position: t.position });
    } else if (t.from_floor_id !== t.to_floor_id) {
      vertical.push({
        kind: t.kind,
        position: t.position,
        fromFloorId: t.from_floor_id,
        toFloorId: t.to_floor_id,
        schematic: !!t.schematic,
      });
    }
  }
  const end = (e: RouteResponse['start']) =>
    e && e.position ? { floorId: e.floor_id, position: e.position } : null;
  return {
    style: route.mode === 'official' ? 'official' : 'precision',
    lines,
    vertical,
    doors,
    start: end(route.start),
    target: end(route.target),
  };
}
