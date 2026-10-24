import { describe, expect, it } from 'vitest';
import type { RouteResponse } from './api';
import { progressAt } from './guidance';

const wp = (index: number, floor_id: string, space_id: string, position: [number, number], kind: string, distance_m: number, element_id?: string) => ({
  index, floor_id, space_id, position, kind, distance_m, element_id, elevation_m: floor_id === 'F1' ? 3 : 0, heading_deg: 0, fraction: distance_m / 20,
});

const route = {
  mode: 'official', status: 'ok', reachable: true, checked: 'official_rules', length_m: 20, revision: 2,
  path: [], directions: [], warnings: [],
  transitions: [
    { kind: 'stairs', element_id: 'V', from_floor_id: 'F1', to_floor_id: 'F0', position: [4, 0], schematic: true, cost_m: 6 },
    { kind: 'door', element_id: 'EXIT', from_floor_id: 'F0', to_floor_id: 'F0', position: [4, 10], schematic: false },
  ],
  waypoints: [
    wp(0, 'F1', 'A', [0, 0], 'start', 0),
    wp(1, 'F1', 'S1', [4, 0], 'stairs', 4, 'V'),
    wp(2, 'F0', 'S0', [4, 0], 'stairs', 10, 'V'),
    wp(3, 'F0', 'B', [4, 10], 'target', 20, 'EXIT'),
  ],
} as unknown as RouteResponse;

describe('progressAt (Phase B contract)', () => {
  it('interpolates on a floor and reports the next transition ahead', () => {
    const p = progressAt(route, 2)!;
    expect(p.position).toEqual([2, 0]);
    expect(p.floor_id).toBe('F1');
    expect(p.space_id).toBe('A');
    expect(p.next_transition?.element_id).toBe('V');
    expect(p.to_next_transition_m).toBe(2);
    expect(p.remaining_m).toBe(18);
  });

  it('keeps XY during a vertical move and switches floor at its end', () => {
    expect(progressAt(route, 7)!.floor_id).toBe('F1');
    expect(progressAt(route, 7)!.position).toEqual([4, 0]);
    expect(progressAt(route, 10)!.floor_id).toBe('F0');
  });

  it('clamps to the route and arrives at the target', () => {
    const end = progressAt(route, 99)!;
    expect(end.fraction).toBe(1);
    expect(end.position).toEqual([4, 10]);
    expect(end.next_transition).toBeNull();
    expect(progressAt({ ...route, waypoints: [] }, 1)).toBeNull();
  });
});
