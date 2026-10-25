import { describe, expect, it } from 'vitest';
import type { Asset, BuildingModel } from './building';
import { hqDifferences } from './hqStale';

const asset = (id: string, x: number, rot = 0): Asset => ({
  id,
  name: `Chair ${id}`,
  category: 'furniture',
  type: 'chair',
  space_id: 's1',
  floor_id: 'f1',
  position: [x, 1],
  footprint: { width_m: 0.5, depth_m: 0.5, rotation_deg: rot },
});

const building = (assets: Asset[], id = 'b1') =>
  ({ building: { id }, floors: [], spaces: [], openings: [], connectors: [], assets }) as unknown as BuildingModel;

describe('hqDifferences', () => {
  it('is empty when nothing changed (tiny float noise ignored)', () => {
    expect(hqDifferences(building([asset('a', 1)]), building([asset('a', 1.01)]))).toEqual([]);
  });
  it('reports a moved, rotated, added or removed thing', () => {
    const hq = building([asset('a', 1), asset('b', 2), asset('c', 3)]);
    const now = building([asset('a', 1.6), asset('b', 2, 90), asset('d', 4)]);
    expect(hqDifferences(hq, now).sort()).toEqual(['Chair a', 'Chair b', 'Chair c', 'Chair d']);
  });
  it('treats 359 deg and 1 deg as close', () => {
    expect(hqDifferences(building([asset('a', 1, 359.5)]), building([asset('a', 1, 0.2)]))).toEqual([]);
  });
  it('never compares two different buildings', () => {
    expect(hqDifferences(building([asset('a', 1)], 'x'), building([], 'y'))).toEqual([]);
  });
});
