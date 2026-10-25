// Reproducible manual-walking cases (night task: "Reproduzierbare Navigation/Spielsteuerung ...")
// on REAL building.json files of three building families with a fixed seed. Every generated move
// is checked by an INDEPENDENT checker (own point-in-polygon, own opening table), not by the
// walkable.ts helpers it tests:
// - positions are always finite;
// - the end point lies inside a space of the floor (or inside the gap of an open door of the
//   current space, which walkable.ts allows explicitly: "inside the door gap itself");
// - the walked trace (every 0.05 m sub-step) never changes space except between two spaces that
//   share an open opening, and never stands in a space other than the current one / its door gap;
// - a huge single step (frame drop) gives exactly the same result as the same motion in 0.05 m
//   increments, so the trace of the increments is the trace of the huge step;
// - stairs/lifts: a ride lands inside the arrival space; a step-free person never takes stairs;
//   a closed connector or door is never used.
// Data: ARINAV_DATASETS_DIR (default: the local SyneraKit dev folder); skipped when it is missing.
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import type { BuildingModel, Point } from './building';
import { BODY_RADIUS_M, buildWorld, connectorsHere, locate, move, ride, type WalkState, type WalkWorld } from './walkable';

const DATASETS_DIR = process.env.ARINAV_DATASETS_DIR ?? 'C:/Users/noah3/Documents/HackathonTransfer/SyneraKit-data/dev';
const BUILDINGS = ['proc-factory-dev-003', 'swiss-1785', 'resplan-1465'];
const SEED = 20261025;
/** Multiplies the generated walk/huge-step/door counts (e.g. 25 for a stress run); default 1. */
const SCALE = Math.max(1, Number(process.env.ARINAV_WALK_CASES_SCALE ?? 1) || 1);
const SUB = 0.05;
const MIN_DOOR_HALF_M = 0.45; // walkable.ts door-gap radius (max(width/2, 0.45)), restated here
const EPS = 1e-9;

// ------------------------------------------------------------------ seeded PRNG

function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function hash(text: string): number {
  let h = 2166136261;
  for (let i = 0; i < text.length; i++) h = Math.imul(h ^ text.charCodeAt(i), 16777619);
  return h >>> 0;
}

// ------------------------------------------------------------------ independent geometry

function pip(p: Point, poly: Point[]): boolean {
  // winding-number test (different algorithm from walkable.ts's even-odd ray cast)
  let wn = 0;
  for (let i = 0; i < poly.length; i++) {
    const a = poly[i];
    const b = poly[(i + 1) % poly.length];
    const cross = (b[0] - a[0]) * (p[1] - a[1]) - (p[0] - a[0]) * (b[1] - a[1]);
    if (a[1] <= p[1]) {
      if (b[1] > p[1] && cross > 0) wn++;
    } else if (b[1] <= p[1] && cross < 0) wn--;
  }
  return wn !== 0;
}

function edgeDistance(p: Point, poly: Point[]): number {
  let best = Infinity;
  for (let i = 0; i < poly.length; i++) {
    const a = poly[i];
    const b = poly[(i + 1) % poly.length];
    const dx = b[0] - a[0];
    const dy = b[1] - a[1];
    const t = Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy || 1)));
    best = Math.min(best, Math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy));
  }
  return best;
}

const finite = (p: Point) => Number.isFinite(p[0]) && Number.isFinite(p[1]);

class Checker {
  readonly pairs = new Set<string>();
  constructor(
    readonly model: BuildingModel,
    readonly closed: ReadonlySet<string> = new Set(),
  ) {
    for (const o of model.openings) {
      if (o.space_ids.length < 2 || closed.has(o.id)) continue;
      const [a, b] = o.space_ids;
      if (a === 'outside' || b === 'outside' || closed.has(a) || closed.has(b)) continue;
      this.pairs.add(`${a}|${b}`).add(`${b}|${a}`);
    }
  }

  spacesAt(floor: string, p: Point): string[] {
    return this.model.spaces.filter((s) => s.floor_id === floor && pip(p, s.polygon)).map((s) => s.id);
  }

  /** p is inside the pass radius of an open door (to a non-outside space) of `space`. */
  inDoorGap(space: string, p: Point): boolean {
    return this.model.openings.some(
      (o) =>
        o.space_ids.includes(space) &&
        this.pairs.has(`${o.space_ids[0]}|${o.space_ids[1]}`) &&
        Math.hypot(p[0] - o.position[0], p[1] - o.position[1]) <= Math.max(o.width_m / 2, MIN_DOOR_HALF_M) + EPS,
    );
  }

  /** Problems of one state: non-finite, or standing where its space does not allow. */
  stateProblems(s: WalkState): string[] {
    if (!finite(s.position)) return [`non-finite position ${JSON.stringify(s.position)}`];
    const here = this.model.spaces.find((x) => x.id === s.space_id);
    if (!here || here.floor_id !== s.floor_id) return [`state space ${s.space_id} not on floor ${s.floor_id}`];
    const at = this.spacesAt(s.floor_id, s.position);
    if (at.includes(s.space_id)) return [];
    if (this.inDoorGap(s.space_id, s.position)) {
      // a door gap may overlap only the current space or the space behind that door
      const foreign = at.filter((x) => !this.pairs.has(`${s.space_id}|${x}`));
      return foreign.length ? [`in door gap of ${s.space_id} but inside foreign space(s) ${foreign.join(',')}`] : [];
    }
    return [`position ${fmt(s.position)} outside ${s.space_id} (inside: ${at.join(',') || 'nothing'})`];
  }

  /** Wall-crossing check of a trace of states (consecutive points <= 0.05 m apart). */
  traceProblems(trace: WalkState[]): string[] {
    const problems: string[] = [];
    let last: WalkState | null = null;
    for (const s of trace) {
      for (const p of this.stateProblems(s)) problems.push(p);
      if (last) {
        const step = Math.hypot(s.position[0] - last.position[0], s.position[1] - last.position[1]);
        if (step > SUB + 1e-6) problems.push(`sub-step of ${step.toFixed(4)} m > ${SUB} m`);
        if (s.floor_id !== last.floor_id) problems.push(`floor changed while walking`);
        if (s.space_id !== last.space_id && !this.pairs.has(`${last.space_id}|${s.space_id}`))
          problems.push(`space ${last.space_id} -> ${s.space_id} without an open shared opening at ${fmt(s.position)}`);
        const before = this.spacesAt(last.floor_id, last.position);
        const after = this.spacesAt(s.floor_id, s.position);
        if (before.length && after.length && !after.some((x) => before.includes(x))) {
          const ok = before.some((a) => after.some((b) => this.pairs.has(`${a}|${b}`)));
          if (!ok) problems.push(`crossed ${before.join(',')} -> ${after.join(',')} through a wall at ${fmt(s.position)}`);
        }
      }
      last = s;
      if (problems.length > 5) break;
    }
    return problems;
  }
}

const fmt = (p: Point) => `[${p[0].toFixed(3)}, ${p[1].toFixed(3)}]`;

/** `delta` scaled (by at most 0.1 %) so that move()'s own sub-steps are clearly below 0.05 m: then
 * one call per sub-step does not split again (hypot rounding), and `incremental` is exact. */
function clean(delta: Point): Point {
  const length = Math.hypot(delta[0], delta[1]);
  const n = Math.max(1, Math.ceil(length / SUB));
  if (length / n < SUB * (1 - 1e-6)) return delta;
  return [delta[0] * 1.001, delta[1] * 1.001];
}

/** The same motion as one `move(delta)` call, but in its own 0.05 m increments (frame by frame). */
function incremental(world: WalkWorld, start: WalkState, delta: Point): WalkState[] {
  const length = Math.hypot(delta[0], delta[1]);
  const n = Math.max(1, Math.ceil(length / SUB));
  const step: Point = [delta[0] / n, delta[1] / n];
  expect(Math.hypot(step[0], step[1])).toBeLessThan(SUB * (1 - 1e-7));
  const trace = [start];
  let s = start;
  for (let i = 0; i < n; i++) {
    s = move(world, s, step).state;
    trace.push(s);
  }
  return trace;
}

// ------------------------------------------------------------------ case generation

interface Ctx {
  id: string;
  model: BuildingModel;
  world: WalkWorld;
  checker: Checker;
}

function randomStart(ctx: Ctx, rnd: () => number, spaceFilter?: (id: string) => boolean): WalkState | null {
  const spaces = ctx.model.spaces.filter((s) => s.type !== 'outdoor' && (!spaceFilter || spaceFilter(s.id)));
  for (let attempt = 0; attempt < 400; attempt++) {
    const s = spaces[Math.floor(rnd() * spaces.length)];
    const xs = s.polygon.map((p) => p[0]);
    const ys = s.polygon.map((p) => p[1]);
    const p: Point = [
      Math.min(...xs) + rnd() * (Math.max(...xs) - Math.min(...xs)),
      Math.min(...ys) + rnd() * (Math.max(...ys) - Math.min(...ys)),
    ];
    if (pip(p, s.polygon) && edgeDistance(p, s.polygon) >= BODY_RADIUS_M + 0.02 && ctx.checker.spacesAt(s.floor_id, p).length === 1)
      return { floor_id: s.floor_id, space_id: s.id, position: p };
  }
  return null;
}

/** A start 0.6 m in front of a door on its side `side` (null when no clear spot). */
function doorStart(ctx: Ctx, openingId: string, side: string): { start: WalkState; through: Point } | null {
  const o = ctx.model.openings.find((x) => x.id === openingId)!;
  const space = ctx.model.spaces.find((s) => s.id === side)!;
  for (let k = 0; k < 16; k++) {
    const angle = (k / 16) * 2 * Math.PI;
    const dir: Point = [Math.cos(angle), Math.sin(angle)];
    const p: Point = [o.position[0] + 0.6 * dir[0], o.position[1] + 0.6 * dir[1]];
    if (pip(p, space.polygon) && edgeDistance(p, space.polygon) >= BODY_RADIUS_M + 0.02 && ctx.checker.spacesAt(space.floor_id, p).length === 1)
      return { start: { floor_id: space.floor_id, space_id: side, position: p }, through: [-dir[0], -dir[1]] };
  }
  return null;
}

const datasetPath = (id: string) => join(DATASETS_DIR, id, 'building.json');
const available = BUILDINGS.every((id) => existsSync(datasetPath(id)));

describe.skipIf(!available)('walkable cases on real buildings (seed 20261025)', () => {
  if (!available) return;
  const stats = { doorCrossings: 0, doorwayEnds: 0, slid: 0 };

  for (const id of BUILDINGS) {
    const model = JSON.parse(readFileSync(datasetPath(id), 'utf-8')) as BuildingModel;
    const ctx: Ctx = { id, model, world: buildWorld(model), checker: new Checker(model) };
    const rnd = mulberry32(SEED ^ hash(id));

    describe(id, () => {
      // 1) random walks: many frames with random headings and lengths (0.02..1.5 m per frame)
      for (let c = 0; c < 6 * SCALE; c++) {
        const start = randomStart(ctx, rnd);
        const frames = Array.from({ length: 40 }, () => {
          const heading = rnd() * 2 * Math.PI;
          const length = 0.02 + rnd() * 1.48;
          return clean([Math.cos(heading) * length, Math.sin(heading) * length]);
        });
        it(`walk #${c}: 40 random frames from ${start ? `${start.space_id} ${fmt(start.position)}` : 'n/a'}`, () => {
          expect(start).not.toBeNull();
          let s = start!;
          const trace: WalkState[] = [s];
          for (const delta of frames) {
            const big = move(ctx.world, s, delta).state;
            const inc = incremental(ctx.world, s, delta);
            expect(inc.at(-1)).toEqual(big); // frame-size independent
            trace.push(...inc.slice(1));
            s = big;
          }
          expect(ctx.checker.traceProblems(trace)).toEqual([]);
          if (s.space_id !== start!.space_id) stats.doorCrossings++;
        });
      }

      // 2) huge single steps (frame drops): 5 m and 12 m in one call
      for (let c = 0; c < 4 * SCALE; c++) {
        const start = randomStart(ctx, rnd);
        const heading = rnd() * 2 * Math.PI;
        const length = c % 2 === 0 ? 5 : 12;
        const delta = clean([Math.cos(heading) * length, Math.sin(heading) * length]);
        it(`huge step #${c}: ${length} m at ${((heading * 180) / Math.PI).toFixed(1)} deg from ${start ? fmt(start.position) : 'n/a'}`, () => {
          expect(start).not.toBeNull();
          const r = move(ctx.world, start!, delta);
          expect(finite(r.state.position)).toBe(true);
          const trace = incremental(ctx.world, start!, delta);
          expect(trace.at(-1)).toEqual(r.state);
          expect(ctx.checker.traceProblems(trace)).toEqual([]);
          const moved = Math.hypot(r.state.position[0] - start!.position[0], r.state.position[1] - start!.position[1]);
          expect(moved).toBeLessThanOrEqual(length * 1.001 + 1e-6);
          if (r.blocked) stats.slid++;
          if (!ctx.checker.spacesAt(r.state.floor_id, r.state.position).length) stats.doorwayEnds++;
        });
      }

      // 3) through a random open interior door, and 4) the same door closed
      const interior = model.openings.filter((o) => o.space_ids.length === 2 && !o.space_ids.includes('outside'));
      for (let c = 0; c < 3 * SCALE; c++) {
        const o = interior[Math.floor(rnd() * interior.length)];
        const side = o.space_ids[Math.floor(rnd() * 2)];
        const other = o.space_ids.find((x) => x !== side) ?? side;
        const setup = doorStart(ctx, o.id, side);
        it(`door #${c}: ${o.id} from ${side} (open, then closed)`, () => {
          expect(setup).not.toBeNull();
          const { start, through } = setup!;
          const delta = clean([through[0] * 1.6, through[1] * 1.6]);
          const open = incremental(ctx.world, start, delta);
          expect(open.at(-1)).toEqual(move(ctx.world, start, delta).state);
          expect(ctx.checker.traceProblems(open)).toEqual([]);
          if (open.at(-1)!.space_id === other) stats.doorCrossings++;
          const closedWorld = buildWorld(model, [o.id]);
          const closedChecker = new Checker(model, new Set([o.id]));
          const shut = incremental(closedWorld, start, delta);
          expect(closedChecker.traceProblems(shut)).toEqual([]);
          expect(shut.every((s) => s.space_id === side)).toBe(true);
          // closing the space behind the door blocks the door as well
          const roomClosed = incremental(buildWorld(model, [other]), start, delta);
          expect(roomClosed.every((s) => s.space_id === side)).toBe(true);
        });
      }

      // 5) exit doors never lead outside
      const exits = model.openings.filter((o) => o.space_ids.includes('outside'));
      if (exits.length) {
        const o = exits[Math.floor(rnd() * exits.length)];
        const inside = o.space_ids.find((x) => x !== 'outside')!;
        const setup = doorStart(ctx, o.id, inside);
        it(`exit ${o.id}: walking 3 m out of the building is refused`, () => {
          expect(setup).not.toBeNull();
          const { start, through } = setup!;
          const trace = incremental(ctx.world, start, clean([through[0] * 3, through[1] * 3]));
          // never outside: every point is inside its space or inside an open INTERIOR door gap
          // (swiss-type thick walls: an interior door next to the entrance has its gap there)
          expect(ctx.checker.traceProblems(trace)).toEqual([]);
          expect(trace.every((s) => s.space_id === inside)).toBe(true);
        });
      }

      // 6) rides: every connector, each stop pair once, step-free and not, then a walk on arrival
      for (const c of model.vertical_connectors) {
        const stops = c.floor_ids.map((floor, i) => ({ floor, space: c.space_ids[i] }));
        for (let i = 0; i + 1 < stops.length; i++) {
          const [a, b] = rnd() < 0.5 ? [stops[i], stops[i + 1]] : [stops[i + 1], stops[i]];
          const start = randomStart(ctx, rnd, (sid) => sid === a.space);
          const heading = rnd() * 2 * Math.PI;
          it(`ride ${c.id} (${c.kind}, step_free=${c.step_free}) ${a.floor} -> ${b.floor}`, () => {
            expect(start).not.toBeNull();
            const stepFreeRide = ride(ctx.world, start!, c.id, b.floor, true);
            const offer = connectorsHere(ctx.world, start!, true).find((x) => x.id === c.id)!;
            if (c.kind === 'stairs' && !c.step_free) {
              expect(stepFreeRide).toBeNull();
              expect(offer.usable).toBe(false);
            } else {
              expect(stepFreeRide).not.toBeNull();
            }
            expect(ride(buildWorld(model, [c.id]), start!, c.id, b.floor, false)).toBeNull();
            const arrived = ride(ctx.world, start!, c.id, b.floor, false);
            expect(arrived).not.toBeNull();
            expect(arrived!.floor_id).toBe(b.floor);
            expect(arrived!.space_id).toBe(b.space);
            // independent: the arrival point lies inside the arrival space
            expect(ctx.checker.stateProblems(arrived!)).toEqual([]);
            expect(locate(ctx.world, b.floor, arrived!.position)).toBe(b.space);
            const delta = clean([Math.cos(heading) * 5, Math.sin(heading) * 5]);
            const trace = incremental(ctx.world, arrived!, delta);
            expect(ctx.checker.traceProblems(trace)).toEqual([]);
          });
        }
      }
    });
  }

  it('summary (informational)', () => {
    console.info(`walkable cases: ${JSON.stringify(stats)}`);
    expect(true).toBe(true);
  });
});

// ------------------------------------------------------------------ regressions (fixed defects)
// Minimal reproducers of walkable.ts defects found by the generator in a stress run
// (ARINAV_WALK_CASES_SCALE=25 plus more buildings); fixed, kept as regression tests.

const load = (id: string) => JSON.parse(readFileSync(datasetPath(id), 'utf-8')) as BuildingModel;

describe.skipIf(!existsSync(datasetPath('resplan-1465')) || !existsSync(datasetPath('proc-factory-dev-003')))(
  'regression: wide openings do not open parallel walls (pass band, not a circle)',
  () => {
    // A wide opening (resplan-1465 O0008 corridor<->living, 3.9 m; proc-factory-dev-003 O0002
    // hall<->hall, 14.6 m) used to get a pass circle of 1.95 m / 7.3 m round its centre, inside
    // which the wall test was waived: the walker crossed a PARALLEL wall into a third space that
    // has no opening to the current one, while space_id stayed unchanged.
    it('resplan-1465: corridor S010 -> bedroom S002 straight through the wall (no door)', () => {
      const model = load('resplan-1465');
      const world = buildWorld(model);
      const checker = new Checker(model);
      const start: WalkState = { floor_id: 'resplan-1465-F0', space_id: 'resplan-1465-F0-S010', position: [19.862, 8.5] };
      const trace = incremental(world, start, clean([0, 1.5])); // north, the corridor wall is at y=8.86
      // before the fix: ended at about [19.862, 10.0], inside bedroom S002 (y >= 9.18), "in S010"
      expect(checker.traceProblems(trace)).toEqual([]);
    });

    it('proc-factory-dev-003: hall F0-S001 -> toilet F0-S022 across the x=0 wall', () => {
      const model = load('proc-factory-dev-003');
      const world = buildWorld(model);
      const checker = new Checker(model);
      const start: WalkState = { floor_id: 'proc-factory-dev-003-F0', space_id: 'proc-factory-dev-003-F0-S001', position: [-0.5, 16.6] };
      const trace = incremental(world, start, clean([1, 0])); // east into the wall shared with the toilet
      // before the fix: ended at about [0.03, 16.6], inside toilet S022 (x >= 0), "in hall S001"
      expect(checker.traceProblems(trace)).toEqual([]);
    });
  },
);

describe.skipIf(!existsSync(datasetPath('osm-way22744230')))('regression: ride() arrival fallback', () => {
  // When the XY is not inside the arrival space, ride() used to land on the arithmetic mean of the
  // polygon's vertices, which lies OUTSIDE a concave arrival space (osm-way22744230 F2-S020).
  it('osm-way22744230: stairs V05 F1 -> F2 lands outside F2-S020', () => {
    const model = load('osm-way22744230');
    const world = buildWorld(model);
    const start: WalkState = { floor_id: 'osm-way22744230-F1', space_id: 'osm-way22744230-F1-S039', position: [-33.713, -21.545] };
    const arrived = ride(world, start, 'osm-way22744230-V05', 'osm-way22744230-F2', false);
    expect(arrived).not.toBeNull();
    // before the fix: position [-32.907, -19.907], not inside F2-S020 (nor any other space)
    expect(new Checker(model).stateProblems(arrived!)).toEqual([]);
  });
});
