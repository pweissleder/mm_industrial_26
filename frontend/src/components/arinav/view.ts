export type Layer = 'spaces' | 'walls' | 'assets' | 'openings' | 'connectors' | 'labels' | 'graph';

export const LAYERS: { id: Layer; label: string }[] = [
  { id: 'spaces', label: 'Rooms' },
  { id: 'walls', label: 'Walls' },
  { id: 'assets', label: 'Assets' },
  { id: 'openings', label: 'Doors' },
  { id: 'connectors', label: 'Stairs & lifts' },
  { id: 'labels', label: 'Labels' },
  { id: 'graph', label: 'Nav graph' },
];

export interface ViewSettings {
  /** Level to focus: higher floors are hidden, lower ones ghosted. */
  floor: number | 'all';
  layers: Record<Layer, boolean>;
  /** 0..1, pulls floors apart vertically. */
  explode: number;
  /** Wall height in metres, capped at each storey's height. */
  wallHeight: number;
  colorBy: 'room' | 'neutral';
  shadows: boolean;
  grid: boolean;
}

export const DEFAULT_VIEW: ViewSettings = {
  floor: 'all',
  layers: { spaces: true, walls: true, assets: true, openings: true, connectors: true, labels: true, graph: false },
  explode: 0,
  wallHeight: 2.4,
  colorBy: 'room',
  shadows: true,
  grid: true,
};

export type CameraAction = 'iso' | 'top' | 'front' | 'zoomIn' | 'zoomOut' | 'fit';

/** Properties of an object picked in an uploaded GLB (its glTF extras). */
export interface ObjectInfo {
  title: string;
  props: [string, string][];
}
