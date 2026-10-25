/**
 * Asset culling for very large buildings: thousands of boxes are slow to draw and
 * unreadable, so past MAX_VISIBLE_ASSETS only assets the user is looking at are shown.
 * Pure (no three.js) so it is unit-testable.
 */

/** The same cap at every screen size: a typical building (a few hundred assets) shows whole. */
export const MAX_VISIBLE_ASSETS = 600;

export interface AssetIndex {
  total: number;
  bySpace: Map<string, string[]>;
  byFloor: Map<string, string[]>;
  spaceOf: Map<string, string>;
}

export interface AssetLike {
  id: string;
  space_id?: string | null;
  floor_id?: string | null;
}

export function buildAssetIndex(assets: readonly AssetLike[]): AssetIndex {
  const bySpace = new Map<string, string[]>();
  const byFloor = new Map<string, string[]>();
  const spaceOf = new Map<string, string>();
  const push = (m: Map<string, string[]>, k: string, v: string) => {
    const list = m.get(k);
    if (list) list.push(v);
    else m.set(k, [v]);
  };
  for (const a of assets) {
    if (a.space_id) {
      push(bySpace, a.space_id, a.id);
      spaceOf.set(a.id, a.space_id);
    }
    if (a.floor_id) push(byFloor, a.floor_id, a.id);
  }
  return { total: assets.length, bySpace, byFloor, spaceOf };
}

export interface CullInput {
  selection: { kind: string; id: string } | null;
  /** Floor id of the focused floor, or null when all floors are shown. */
  focusFloorId: string | null;
  highlightIds?: readonly string[];
  cap?: number;
}

/** Ids of assets to show, or null when everything is shown (no culling needed). */
export function visibleAssetIds(index: AssetIndex, input: CullInput): Set<string> | null {
  const cap = input.cap ?? MAX_VISIBLE_ASSETS;
  if (index.total <= cap) return null;
  const out = new Set<string>();
  const addAll = (ids?: readonly string[]) => ids?.forEach((id) => out.add(id));
  const sel = input.selection;
  if (sel?.kind === 'space') addAll(index.bySpace.get(sel.id));
  else if (sel?.kind === 'asset') {
    out.add(sel.id);
    const room = index.spaceOf.get(sel.id);
    if (room) addAll(index.bySpace.get(room));
  }
  for (const id of input.highlightIds ?? []) if (index.spaceOf.has(id)) out.add(id);
  if (input.focusFloorId) {
    const floor = index.byFloor.get(input.focusFloorId);
    if (floor && floor.length <= cap) addAll(floor);
  }
  return out;
}
