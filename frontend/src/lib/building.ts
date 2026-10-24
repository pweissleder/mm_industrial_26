// Building model in the starter-kit format (`building.json`), as served by
// GET /api/buildings/{id}/model, plus small helpers the UI shares.

export type Point = [number, number];

export interface Floor {
  id: string;
  level: number;
  name: string;
  elevation_m: number;
  height_m: number;
}

export interface Space {
  id: string;
  floor_id: string;
  type: string;
  room_type: string;
  name: string;
  ref: string;
  polygon: Point[];
}

export interface Opening {
  id: string;
  floor_id: string;
  space_ids: string[];
  kind: string;
  position: Point;
  width_m: number;
  step_free: boolean;
}

export interface VerticalConnector {
  id: string;
  kind: string;
  name: string;
  floor_ids: string[];
  space_ids: string[];
  step_free: boolean;
}

export interface Asset {
  id: string;
  name: string;
  category: string;
  type: string;
  space_id: string;
  floor_id: string;
  position: Point;
  footprint: { width_m?: number; depth_m?: number; rotation_deg?: number };
}

export interface BuildingModel {
  building: { id: string; name: string; source: string; license: string; attribution: string };
  floors: Floor[];
  spaces: Space[];
  openings: Opening[];
  vertical_connectors: VerticalConnector[];
  assets: Asset[];
}

/** `object` is a node of an uploaded GLB rather than an element of building.json. */
export type SelectionKind = 'space' | 'asset' | 'opening' | 'connector' | 'object';

export interface Selection {
  kind: SelectionKind;
  id: string;
}

export const humanize = (s: string) => s.replace(/_/g, ' ').replace(/^\w/, (c) => c.toUpperCase());

export function polygonArea(polygon: Point[]): number {
  let a = 0;
  for (let i = 0; i < polygon.length; i++) {
    const [x1, y1] = polygon[i];
    const [x2, y2] = polygon[(i + 1) % polygon.length];
    a += x1 * y2 - x2 * y1;
  }
  return a / 2;
}

export function centroid(polygon: Point[]): Point {
  const n = polygon.length || 1;
  return [polygon.reduce((s, p) => s + p[0], 0) / n, polygon.reduce((s, p) => s + p[1], 0) / n];
}

export function floorLabel(floor: Floor): string {
  if (floor.level === 0) return 'G';
  return floor.level > 0 ? `L${floor.level}` : `B${-floor.level}`;
}

// Pastel fills per room type, in the spirit of Synera's light, airy palette.
const ROOM_COLORS: Record<string, string> = {
  production_zone: '#dfe8ff',
  assembly_zone: '#d9e4fb',
  additive_zone: '#e4ddff',
  logistics_zone: '#ffe6d1',
  warehouse_zone: '#ffeedd',
  office: '#ebe9ff',
  meeting_room: '#d9f2ea',
  corridor: '#f3f3f1',
  lobby: '#eeeeea',
  kitchen: '#fff3cf',
  server_room: '#d8eef8',
  toilet: '#f0e7f6',
  locker_room: '#efe6f5',
  stairwell: '#ffd8bf',
  lift: '#ffd8bf',
  quality_lab: '#e2f2d6',
};
const TYPE_COLORS: Record<string, string> = {
  hall: '#e2eaff',
  corridor: '#f3f3f1',
  stair: '#ffd8bf',
  lift: '#ffd8bf',
  room: '#eceef3',
};

export const spaceColor = (s: Space) => ROOM_COLORS[s.room_type] ?? TYPE_COLORS[s.type] ?? '#eceef3';

export const ASSET_COLORS: Record<string, string> = {
  machine: '#004fab',
  equipment: '#fb7928',
  furniture: '#9a9db0',
  fixture: '#12a594',
};
export const assetColor = (a: Asset) => ASSET_COLORS[a.category] ?? '#7b7f93';

// Assumed heights (m); the data only has footprints. Mirrors blender/build_building.py.
const ASSET_HEIGHT: Record<string, number> = {
  desk: 0.75,
  office_chair: 0.9,
  chair: 0.9,
  meeting_table: 0.75,
  dining_table: 0.75,
  bench: 0.45,
  filing_cabinet: 1.3,
  bookshelf: 2.0,
  locker: 1.8,
  shoe_cabinet: 1.0,
  plant: 1.2,
  whiteboard: 1.2,
  display_screen: 1.6,
  server_rack: 2.0,
  ups_unit: 1.6,
  pallet_rack: 4.5,
  pallet_jack: 1.2,
  forklift: 2.2,
  conveyor: 1.0,
  fridge: 1.8,
  kitchen_counter: 0.9,
  stove: 0.9,
  dishwasher: 0.85,
  sink: 0.9,
  toilet: 0.8,
  fire_extinguisher: 0.6,
  cnc_mill: 2.4,
  injection_moulding_machine: 2.2,
  press_brake: 3.0,
  industrial_3d_printer: 2.0,
};
const CATEGORY_HEIGHT: Record<string, number> = {
  furniture: 0.8,
  fixture: 1.0,
  machine: 1.8,
  equipment: 1.2,
};
export const assetHeight = (a: Asset) => ASSET_HEIGHT[a.type] ?? CATEGORY_HEIGHT[a.category] ?? 1.0;
