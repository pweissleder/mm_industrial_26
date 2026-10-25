import { describe, expect, it } from 'vitest';
import type { Asset, BuildingModel, Point, Space } from './building';
import {
  addAsset,
  addOpening,
  catalog,
  alignAsset,
  alignRotation,
  constrainItemMove,
  deleteAsset,
  deleteOpening,
  duplicateAsset,
  isLockedSpace,
  moveAsset,
  moveOpening,
  neighbours,
  OUTSIDE,
  PASS_M,
  startItemDrag,
  updateOpening,
  wallAngleAt,
  pointInside,
  rotateAsset,
  spaceAt,
  toBuildingFile,
  updateAsset,
} from './edit';

const room = (id: string, ref: string, polygon: [number, number][]): Space => ({
  id,
  floor_id: 'B-F0',
  type: 'room',
  room_type: 'office',
  name: `Office ${ref}`,
  ref,
  polygon,
});

const desk: Asset = {
  id: 'B-A00007',
  name: 'Desk 0.01',
  category: 'furniture',
  type: 'desk',
  space_id: 'B-S1',
  floor_id: 'B-F0',
  position: [1, 1],
  footprint: { width_m: 1.6, depth_m: 0.8, rotation_deg: 0 },
  source: 'generated',
};

const model: BuildingModel = {
  building: { id: 'B', name: 'Test', source: 'procedural', license: 'CC0-1.0', attribution: '' },
  floors: [{ id: 'B-F0', level: 0, name: 'Ground floor', elevation_m: 0, height_m: 3 }],
  spaces: [
    room('B-S1', '0.01', [[0, 0], [4, 0], [4, 4], [0, 4]]),
    room('B-S2', '0.02', [[4, 0], [8, 0], [8, 4], [4, 4]]),
  ],
  openings: [],
  vertical_connectors: [],
  assets: [desk],
};

describe('layout edits', () => {
  it('moves an asset and reassigns it to the room it lands in', () => {
    const next = moveAsset(model, desk.id, [6.12345, 2]);
    expect(next.assets[0].position).toEqual([6.123, 2]);
    expect(next.assets[0].space_id).toBe('B-S2');
    expect(model.assets[0].position).toEqual([1, 1]); // the original stays for undo
  });

  it('keeps the room when an asset is moved outside every room', () => {
    expect(moveAsset(model, desk.id, [20, 20]).assets[0].space_id).toBe('B-S1');
  });

  it('merges footprint changes', () => {
    const next = updateAsset(model, desk.id, { footprint: { width_m: 2 } });
    expect(next.assets[0].footprint).toEqual({ width_m: 2, depth_m: 0.8, rotation_deg: 0 });
  });

  it('wraps rotation into 0..360', () => {
    expect(rotateAsset(model, desk.id, -15).assets[0].footprint.rotation_deg).toBe(345);
    expect(rotateAsset(model, desk.id, 375).assets[0].footprint.rotation_deg).toBe(15);
  });

  it('deletes an asset', () => {
    expect(deleteAsset(model, desk.id).assets).toEqual([]);
  });

  it('adds an asset in the room with a fresh id and unique name', () => {
    const item = { type: 'desk', category: 'furniture', width_m: 1.6, depth_m: 0.8 };
    const { model: next, id } = addAsset(model, 'B-S1', item);
    const added = next.assets.find((a) => a.id === id)!;
    expect(id).toBe('B-A00008');
    expect(added).toMatchObject({ name: 'Desk 0.01-B', space_id: 'B-S1', floor_id: 'B-F0', source: 'user' });
    expect(added.position).toEqual([2, 2]);
  });

  it('duplicates an asset next to the original', () => {
    const { model: next, id } = duplicateAsset(model, desk.id);
    const copy = next.assets.find((a) => a.id === id)!;
    expect(copy.name).toBe('Desk 0.01-B');
    expect(copy.position).toEqual([2.8, 1]);
    expect(next.assets).toHaveLength(2);
  });
});

describe('dragging items', () => {
  const drag = (m: BuildingModel, to: [number, number], from: [number, number] = [1, 1]) =>
    constrainItemMove(m, desk.id, startItemDrag(m, desk.id)!, from, to);

  it('starts strict when the item fits its room', () => {
    expect(startItemDrag(model, desk.id)).toEqual({ spaceId: 'B-S1', strict: true, free: false });
  });

  it('stops at the wall, keeping the whole footprint inside', () => {
    const { position, state } = drag(model, [3.9, 1]);
    expect(position[0]).toBeCloseTo(4 - 0.8 - 0.12, 2);
    expect(position[1]).toBe(1);
    expect(state.spaceId).toBe('B-S1');
  });

  it('slides along the wall on a diagonal move', () => {
    const { position } = drag(model, [3.9, 2]);
    expect(position[0]).toBeCloseTo(3.08, 2);
    expect(position[1]).toBeCloseTo(2, 5);
  });

  it('stays in the room until the pointer is far enough past the wall', () => {
    const { position, state } = drag(model, [4 + PASS_M - 0.2, 1]);
    expect(state.spaceId).toBe('B-S1');
    expect(position[0]).toBeCloseTo(3.08, 2);
  });

  it('moves into the next room once the pointer has passed the wall', () => {
    const { position, state } = drag(model, [4 + PASS_M + 0.4, 1]);
    expect(state.spaceId).toBe('B-S2');
    expect(position).toEqual([4 + PASS_M + 0.4, 1]);
    expect(moveAsset(model, desk.id, position, { spaceId: state.spaceId }).assets[0].space_id).toBe('B-S2');
  });

  it('never moves into a stairwell', () => {
    const stairs = { ...model, spaces: [model.spaces[0], { ...model.spaces[1], type: 'stair' }] };
    const { state, position } = drag(stairs, [6, 1]);
    expect(state.spaceId).toBe('B-S1');
    expect(position[0]).toBeLessThan(4);
  });

  it('crosses freely between open halls and corridors', () => {
    const open = {
      ...model,
      spaces: model.spaces.map((s) => ({ ...s, type: s.id === 'B-S1' ? 'hall' : 'corridor' })),
    };
    const { state, position } = drag(open, [5, 1]);
    expect(position).toEqual([5, 1]);
    expect(state.spaceId).toBe('B-S2');
  });

  it('only keeps the centre inside when the item never fit', () => {
    const huge = { ...model, assets: [{ ...desk, footprint: { width_m: 6, depth_m: 6 } }] };
    expect(startItemDrag(huge, desk.id)?.strict).toBe(false);
    expect(drag(huge, [3, 3]).position).toEqual([3, 3]);
  });

  it('leaves items that started outside every room unconstrained', () => {
    const lost = { ...model, assets: [{ ...desk, position: [20, 20] as [number, number] }] };
    expect(startItemDrag(lost, desk.id)?.free).toBe(true);
  });
});

describe('wall alignment', () => {
  // A 6 x 6 m room turned 30°, next to nothing.
  const turned = (deg: number): Space => {
    const r = (deg * Math.PI) / 180;
    const corner = ([x, y]: [number, number]): [number, number] => [
      +(x * Math.cos(r) - y * Math.sin(r)).toFixed(6),
      +(x * Math.sin(r) + y * Math.cos(r)).toFixed(6),
    ];
    return room('B-T', 'T', [corner([0, 0]), corner([6, 0]), corner([6, 6]), corner([0, 6])]);
  };
  const tilted = { ...model, spaces: [turned(30)], assets: [] };

  it('snaps a rotation to the nearest multiple of 90° from the wall', () => {
    expect(alignRotation(0, 30)).toBe(30);
    expect(alignRotation(85, 0)).toBe(90);
    expect(alignRotation(44, 0)).toBe(0);
    expect(alignRotation(-80, 0)).toBe(270);
    expect(alignRotation(359.9, 0)).toBe(0);
  });

  it('finds the direction of the nearest wall', () => {
    expect(wallAngleAt(model.spaces[0], [2, 0.2])).toBeCloseTo(0, 5);
    expect(wallAngleAt(turned(30), [3, 0.2])).toBeCloseTo(30, 5);
  });

  it('adds items square to a turned room', () => {
    const item = { type: 'desk', category: 'furniture', width_m: 1.6, depth_m: 0.8 };
    const { model: next, id } = addAsset(tilted, 'B-T', item);
    expect(next.assets.find((a) => a.id === id)!.footprint.rotation_deg).toBeCloseTo(30, 3);
  });

  it('lines an item up when it is dragged into a turned room', () => {
    const square = room('B-S1', '0.01', [[-8, -4], [0, -4], [0, 4], [-8, 4]]);
    const m = { ...model, spaces: [square, turned(30)], assets: [{ ...desk, position: [-4, 0] as Point }] };
    const result = constrainItemMove(m, desk.id, startItemDrag(m, desk.id)!, [-4, 0], [1.5, 3.5]);
    expect(result.state.spaceId).toBe('B-T');
    expect(result.rotation).toBeCloseTo(30, 3);
  });

  it('aligns a skewed item with its wall', () => {
    const skewed = { ...model, assets: [{ ...desk, footprint: { ...desk.footprint, rotation_deg: 7 } }] };
    expect(alignAsset(skewed, desk.id).assets[0].footprint.rotation_deg).toBe(0);
  });

  it('duplicates side by side along the item’s own width', () => {
    const r90 = { ...model, assets: [{ ...desk, footprint: { ...desk.footprint, rotation_deg: 90 } }] };
    const { model: next, id } = duplicateAsset(r90, desk.id);
    const copy = next.assets.find((a) => a.id === id)!;
    expect(copy.position[0]).toBeCloseTo(1, 3);
    expect(copy.position[1]).toBeCloseTo(1 + 1.6 + 0.2, 3);
  });
});

describe('doors', () => {
  const added = addOpening(model, 'B-S1', 'B-S2');
  const door = () => added.model.openings.find((o) => o.id === added.id)!;

  it('lists rooms sharing a wall, and the outside', () => {
    const ids = neighbours(model, 'B-S1').map((n) => n.id);
    expect(ids).toEqual(['B-S2', OUTSIDE]);
    expect(neighbours(model, 'B-S1')[0].position[0]).toBe(4);
  });

  it('adds a door in the middle of the shared wall', () => {
    expect(added.id).toBe('B-F0-O0001');
    expect(door()).toMatchObject({ kind: 'door', floor_id: 'B-F0', width_m: 0.9, synthetic: false });
    expect(door().space_ids).toEqual(['B-S1', 'B-S2']);
    expect(door().position[0]).toBe(4);
  });

  it('adds an entrance to the outside', () => {
    const { model: next, id } = addOpening(model, 'B-S1', OUTSIDE);
    expect(next.openings.find((o) => o.id === id)).toMatchObject({
      kind: 'entrance',
      space_ids: ['B-S1', OUTSIDE],
    });
  });

  it('snaps a moved door onto the nearest wall', () => {
    const moved = moveOpening(added.model, added.id, [4.2, 1]).openings[0];
    expect(moved.position).toEqual([4, 1]);
    expect([...moved.space_ids].sort()).toEqual(['B-S1', 'B-S2']);
  });

  it('turns a door on an outer wall into an entrance, and back', () => {
    const out = moveOpening(added.model, added.id, [0.1, 2]);
    expect(out.openings[0]).toMatchObject({ kind: 'entrance', position: [0, 2] });
    expect(out.openings[0].space_ids).toEqual(['B-S1', OUTSIDE]);
    expect(moveOpening(out, added.id, [3.9, 3]).openings[0].kind).toBe('door');
  });

  it('keeps a plain opening a plain opening', () => {
    const opening = updateOpening(added.model, added.id, { kind: 'opening' });
    expect(moveOpening(opening, added.id, [4, 3]).openings[0].kind).toBe('opening');
  });

  it('enforces a minimum width and deletes', () => {
    expect(updateOpening(added.model, added.id, { width_m: 0.1 }).openings[0].width_m).toBe(0.3);
    expect(deleteOpening(added.model, added.id).openings).toEqual([]);
  });

  it('locks stair and lift spaces', () => {
    expect(isLockedSpace({ ...model.spaces[0], type: 'stair' })).toBe(true);
    expect(isLockedSpace({ ...model.spaces[0], room_type: 'lift' })).toBe(true);
    expect(isLockedSpace(model.spaces[0])).toBe(false);
  });
});

describe('saving', () => {
  it('keeps a door closure from the file but not from the scenario', async () => {
    const original = { openings: [{ id: 'D1', closed: true }], assets: [] };
    const base = { floor_id: 'B-F0', space_ids: ['B-S1', 'B-S2'], kind: 'door', position: [4, 2] as [number, number], width_m: 0.9, step_free: true };
    const file = toBuildingFile(original, {
      assets: [],
      openings: [
        { ...base, id: 'D1', closed: false },
        { ...base, id: 'D2', closed: true },
      ],
    });
    const saved = JSON.parse(await file.text());
    expect(saved.openings[0]).toMatchObject({ id: 'D1', closed: true, synthetic: false });
    expect(saved.openings[1]).not.toHaveProperty('closed');
  });

  it('replaces only the assets of the original file, without scenario state', async () => {
    const original = { building: { id: 'B', split: 'dev' }, spaces: [{ id: 'B-S1' }], assets: [] };
    const edited = { ...desk, closed: true } as Asset;
    const file = toBuildingFile(original, {
      assets: [edited, { ...desk, id: 'B-A9', source: undefined }],
      openings: [],
    });
    const saved = JSON.parse(await file.text());
    expect(file.name).toBe('building.json');
    expect(saved.building).toEqual(original.building);
    expect(saved.spaces).toEqual(original.spaces);
    expect(saved.assets[0]).not.toHaveProperty('closed');
    expect(saved.assets[0].source).toBe('generated');
    expect(saved.assets[1].source).toBe('user');
  });
});

describe('geometry helpers', () => {
  it('finds a point inside an L-shaped room whose centroid is outside', () => {
    const l = room('B-L', 'L', [[0, 0], [10, 0], [10, 1], [1, 1], [1, 10], [0, 10]]);
    const p = pointInside(l);
    expect(spaceAt({ ...model, spaces: [l] }, 'B-F0', p)?.id).toBe('B-L');
  });

  it('prefers the smallest containing room', () => {
    const hall = room('B-H', 'H', [[0, 0], [8, 0], [8, 8], [0, 8]]);
    expect(spaceAt({ ...model, spaces: [hall, ...model.spaces] }, 'B-F0', [1, 1])?.id).toBe('B-S1');
  });

  it('lists building types alongside the presets, with the building footprint', () => {
    const items = catalog({ ...model, assets: [{ ...desk, footprint: { width_m: 1.4, depth_m: 0.7 } }] });
    expect(items.find((i) => i.type === 'desk')).toMatchObject({ width_m: 1.4, depth_m: 0.7 });
    expect(items.some((i) => i.type === 'cnc_mill')).toBe(true);
  });
});
