import { describe, expect, it } from 'vitest';
import type { RoomGraphData } from '../../lib/api';
import type { BuildingModel } from '../../lib/building';
import { layoutGraph } from './GraphDialog';

const square = (x: number): [number, number][] => [[x, 0], [x + 10, 0], [x + 10, 10], [x, 10]];
const space = (id: string, floor_id: string, x: number) =>
  ({ id, floor_id, type: 'room', room_type: 'office', name: id, ref: id, polygon: square(x) });

const model = {
  floors: [
    { id: 'F0', level: 0, name: 'Ground', elevation_m: 0, height_m: 3 },
    { id: 'F1', level: 1, name: 'First', elevation_m: 3, height_m: 3 },
  ],
  spaces: [space('a', 'F0', 0), space('b', 'F1', 0), space('c', 'F1', 10)],
} as unknown as BuildingModel;

const graph: RoomGraphData = {
  nodes: [
    { id: 'outside', kind: 'outside', name: 'Outside' },
    { id: 'a', kind: 'space', name: 'a' },
    { id: 'b', kind: 'space', name: 'b' },
    { id: 'c', kind: 'space', name: 'c' },
  ],
  edges: [
    { source: 'outside', target: 'a', key: 'd1', kind: 'door', step_free: true },
    { source: 'a', target: 'b', key: 's1', kind: 'stairs', step_free: false, floors: 1 },
  ],
};

describe('layoutGraph', () => {
  it('puts each floor in its own panel and flags spaces cut off from outside', () => {
    const layout = layoutGraph(model, graph);
    expect(layout.panels.map((p) => p.id)).toEqual(['F0', 'F1']);
    expect([...layout.cutOff]).toEqual(['c']);
    // b and c share a floor panel, side by side.
    expect(layout.pos.get('b')![1]).toBe(layout.pos.get('c')![1]);
    expect(layout.pos.get('c')![0]).toBeGreaterThan(layout.pos.get('b')![0]);
  });
});
