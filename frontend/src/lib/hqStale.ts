// Is the baked HQ model still showing the building the router uses? (night task Q05)
// The HQ GLB bakes furniture in. When a thing is moved, added or removed after the build (e.g. by
// the agent's move_asset), the HQ view would show it in the wrong place, so the app falls back to
// the live low-quality view until someone deliberately rebuilds. Plain comparison, no hashes.
import type { Asset, BuildingModel } from './building';

const POS_M = 0.05;
const ROT_DEG = 1;

function changed(a: Asset, b: Asset): boolean {
  const fa = a.footprint ?? {};
  const fb = b.footprint ?? {};
  const rot = Math.abs(((((fa.rotation_deg ?? 0) - (fb.rotation_deg ?? 0)) % 360) + 540) % 360 - 180);
  return (
    Math.hypot(a.position[0] - b.position[0], a.position[1] - b.position[1]) > POS_M ||
    rot > ROT_DEG ||
    Math.abs((fa.width_m ?? 0) - (fb.width_m ?? 0)) > POS_M ||
    Math.abs((fa.depth_m ?? 0) - (fb.depth_m ?? 0)) > POS_M ||
    a.floor_id !== b.floor_id
  );
}

/** Names of things that differ between the HQ build and the current building ([] = up to date). */
export function hqDifferences(hq: BuildingModel, current: BuildingModel): string[] {
  if (hq.building.id !== current.building.id) return [];
  const before = new Map(hq.assets.map((a) => [a.id, a]));
  const out: string[] = [];
  for (const a of current.assets) {
    const old = before.get(a.id);
    if (!old || changed(old, a)) out.push(a.name || a.id);
    before.delete(a.id);
  }
  for (const gone of before.values()) out.push(gone.name || gone.id);
  return out;
}
