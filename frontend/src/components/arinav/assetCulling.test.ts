import { describe, expect, it } from 'vitest';
import {
  assetCapFor,
  buildAssetIndex,
  LARGE_SCREEN_ASSETS,
  MAX_VISIBLE_ASSETS,
  visibleAssetIds,
} from './assetCulling';

const assets = Array.from({ length: 10 }, (_, i) => ({
  id: `a${i}`,
  space_id: i < 4 ? 'R1' : i < 6 ? 'R2' : 'R3',
  floor_id: i < 6 ? 'F0' : 'F1',
}));
const index = buildAssetIndex(assets);
const none = { selection: null, focusFloorId: null };
const sorted = (s: Set<string> | null) => [...(s ?? [])].sort();

describe('visibleAssetIds', () => {
  it('shows everything when under the cap', () => {
    expect(visibleAssetIds(index, { ...none, cap: 10 })).toBeNull();
  });
  it('shows nothing without selection or focus', () => {
    expect(sorted(visibleAssetIds(index, { ...none, cap: 5 }))).toEqual([]);
  });
  it('shows only the selected room assets', () => {
    const s = visibleAssetIds(index, { selection: { kind: 'space', id: 'R2' }, focusFloorId: null, cap: 5 });
    expect(sorted(s)).toEqual(['a4', 'a5']);
  });
  it('shows a selected asset plus its room', () => {
    const s = visibleAssetIds(index, { selection: { kind: 'asset', id: 'a1' }, focusFloorId: null, cap: 5 });
    expect(sorted(s)).toEqual(['a0', 'a1', 'a2', 'a3']);
  });
  it('shows a focused floor under the cap', () => {
    const s = visibleAssetIds(index, { ...none, focusFloorId: 'F1', cap: 5 });
    expect(sorted(s)).toEqual(['a6', 'a7', 'a8', 'a9']);
  });
  it('hides a focused floor over the cap but keeps the selection', () => {
    const s = visibleAssetIds(index, { selection: { kind: 'asset', id: 'a9' }, focusFloorId: 'F0', cap: 5 });
    expect(sorted(s)).toEqual(['a6', 'a7', 'a8', 'a9']);
    const t = visibleAssetIds(index, { ...none, focusFloorId: 'F0', cap: 5 });
    expect(sorted(t)).toEqual([]);
  });
  it('includes route highlight assets only', () => {
    const s = visibleAssetIds(index, { ...none, highlightIds: ['a7', 'R1'], cap: 5 });
    expect(sorted(s)).toEqual(['a7']);
  });
});

describe('assetCapFor', () => {
  it('allows more assets on desktop-sized views', () => {
    expect(assetCapFor(1440)).toBe(LARGE_SCREEN_ASSETS);
    expect(assetCapFor(1100)).toBe(MAX_VISIBLE_ASSETS);
  });
});
