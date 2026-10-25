// G01/G02/G05: manual walking against the real room polygons of proc-factory-dev-003.
import { describe, expect, it } from 'vitest';
import building from '../../../backend/data/datasets/proc-factory-dev-003/building.json';
import type { BuildingModel } from './building';
import { buildWorld, connectorsHere, locate, move, ride, type WalkState } from './walkable';

const model = building as unknown as BuildingModel;
const B = 'proc-factory-dev-003';
const office: WalkState = { floor_id: `${B}-F1`, space_id: `${B}-F1-S004`, position: [2.5, 2.7] };

describe('manual walking', () => {
  const world = buildWorld(model);

  it('locates the room under a point', () => {
    expect(locate(world, `${B}-F1`, [2.5, 2.7])).toBe(`${B}-F1-S004`);
    expect(locate(world, `${B}-F1`, [6, 30])).toBe(`${B}-F1-S001`);
  });

  it('stops at a wall and keeps the body radius off it', () => {
    const r = move(world, office, [0, -10]); // straight into the south wall
    expect(r.blocked).toBe(true);
    expect(r.state.space_id).toBe(office.space_id);
    expect(r.state.position[1]).toBeGreaterThanOrEqual(0.25 - 1e-9);
  });

  it('never tunnels through a wall with one huge step (low FPS / high speed)', () => {
    const r = move(world, { ...office, position: [2.5, 4.0] }, [8, 0]); // east wall is solid here
    expect(r.state.space_id).toBe(office.space_id);
    expect(r.state.position[0]).toBeLessThan(5);
  });

  it('passes through the open door into the corridor', () => {
    const r = move(world, { ...office, position: [4.0, 2.718] }, [2.0, 0]);
    expect(r.state.space_id).toBe(`${B}-F1-S001`);
    expect(r.state.position[0]).toBeCloseTo(6.0, 6);
  });

  it('a closed door blocks the way', () => {
    const closed = buildWorld(model, [`${B}-F1-O0057`]);
    const r = move(closed, { ...office, position: [4.0, 2.718] }, [2.0, 0]);
    expect(r.state.space_id).toBe(office.space_id);
    expect(r.blocked).toBe(true);
  });

  it('nobody walks out through an exit door into "outside"', () => {
    const hall: WalkState = { floor_id: `${B}-F0`, space_id: `${B}-F0-S012`, position: [-42.5, 25.65] };
    const r = move(world, hall, [-3, 0]);
    expect(r.state.space_id).toBe(hall.space_id);
    expect(r.state.position[0]).toBeGreaterThan(-43.5);
  });

  it('slides along a wall instead of sticking', () => {
    const r = move(world, { ...office, position: [2.5, 5.0] }, [1, 1]); // diagonal into the north wall
    expect(r.blocked).toBe(true);
    expect(r.state.position[0]).toBeGreaterThan(2.5); // moved east along the wall
  });
});

describe('stairs and lifts', () => {
  const stairs: WalkState = { floor_id: `${B}-F1`, space_id: `${B}-F1-S002`, position: [9, 2] };

  it('rides the stairs to the other floor at the same XY', () => {
    const world = buildWorld(model);
    const down = ride(world, stairs, `${B}-V01`, `${B}-F0`, false);
    expect(down).toEqual({ floor_id: `${B}-F0`, space_id: `${B}-F0-S017`, position: [9, 2] });
  });

  it('a step-free person can never take the stairs (C02 for manual walking)', () => {
    const world = buildWorld(model);
    expect(ride(world, stairs, `${B}-V01`, `${B}-F0`, true)).toBeNull();
    expect(connectorsHere(world, stairs, true)[0]).toMatchObject({ usable: false, reason: 'not step-free' });
  });

  it('a closed connector is refused', () => {
    const world = buildWorld(model, [`${B}-V01`]);
    expect(ride(world, stairs, `${B}-V01`, `${B}-F0`, false)).toBeNull();
  });
});
