// Virtual navigation on real backend routes (campus-routes.json is exported from NavigationService
// for proc-campus-dev-001): deterministic positions, pause/resume, floor order, arrival at the
// actual target point, no ids in instructions.
import { describe, expect, it } from 'vitest';
import type { RouteResponse } from './api';
import fixture from './__fixtures__/campus-routes.json';
import { progressAt } from './guidance';
import { buildInstructions, GuidanceSim, remainingElements, WALK_SPEED, type Names } from './simulation';

const routes = fixture as unknown as Record<'official_exit' | 'official_lift_start' | 'precision_exit', RouteResponse> & {
  names: { floors: Record<string, string>; spaces: Record<string, string>; exits: string[]; opening_positions: Record<string, [number, number]> };
};
const names = (target: string | null): Names => ({
  space: (id) => (id ? (routes.names.spaces[id] ?? null) : null),
  floor: (id) => (id ? (routes.names.floors[id] ?? null) : null),
  target,
});

function run(route: RouteResponse, dt = 0.1, speed = 1) {
  const sim = new GuidanceSim();
  sim.setSpeed(speed);
  const events = [...sim.load(route, buildInstructions(route, names('the exit'))), ...sim.start()];
  const samples = [sim.snapshot()];
  for (let i = 0; i < 100000 && sim.snapshot().status === 'active'; i++) {
    events.push(...sim.tick(dt));
    samples.push(sim.snapshot());
  }
  return { sim, events, samples };
}

const floorsInOrder = (samples: { progress: { floor_id: string } | null }[]) =>
  samples.reduce<string[]>((acc, s) => {
    const f = s.progress?.floor_id;
    if (f && acc[acc.length - 1] !== f) acc.push(f);
    return acc;
  }, []);

describe('virtual navigation on backend routes', () => {
  it('walks the whole official route and arrives exactly at the exit door', () => {
    const route = routes.official_exit;
    const { samples, events } = run(route);
    const end = samples[samples.length - 1];
    expect(end.status).toBe('arrived');
    expect(end.progress?.position).toEqual(route.waypoints!.at(-1)!.position);
    expect(end.progress?.position).toEqual(routes.names.opening_positions[route.target!.id!]);
    expect(events.filter((e) => e.type === 'arrive')).toHaveLength(1);
  });

  it('changes floors in route order (F3 -> F2 -> F1 -> F0) and never jumps back', () => {
    const { samples } = run(routes.official_exit);
    expect(floorsInOrder(samples).map((f) => f.slice(-2))).toEqual(['F3', 'F2', 'F1', 'F0']);
    const lift = run(routes.official_lift_start);
    expect(floorsInOrder(lift.samples).map((f) => f.slice(-2))).toEqual(['F3', 'F2']);
  });

  it('is deterministic: the same simulation time gives the same position at any step size', () => {
    const route = routes.official_exit;
    const a = new GuidanceSim();
    const b = new GuidanceSim();
    a.load(route, []);
    b.load(route, []);
    a.start();
    b.start();
    for (let i = 0; i < 100; i++) a.tick(0.1);
    b.tick(4);
    b.tick(6);
    expect(a.snapshot().distance_m).toBeCloseTo(b.snapshot().distance_m, 9);
    const [pa, pb] = [a.snapshot().progress!.position, b.snapshot().progress!.position];
    expect(pa[0]).toBeCloseTo(pb[0], 9);
    expect(pa[1]).toBeCloseTo(pb[1], 9);
    expect(a.snapshot().distance_m).toBeCloseTo(10 * WALK_SPEED, 9);
  });

  it('pause and resume cause no position jump; paused time does not move the walker', () => {
    const sim = new GuidanceSim();
    sim.load(routes.official_exit, []);
    sim.start();
    sim.tick(5);
    const before = sim.snapshot();
    sim.pause();
    sim.tick(30);
    expect(sim.snapshot().distance_m).toBe(before.distance_m);
    sim.resume();
    sim.tick(0.01);
    const after = sim.snapshot();
    expect(after.distance_m - before.distance_m).toBeCloseTo(0.01 * WALK_SPEED, 9);
    const [x0, y0] = before.progress!.position;
    const [x1, y1] = after.progress!.position;
    expect(Math.hypot(x1 - x0, y1 - y0)).toBeLessThan(0.02);
  });

  it('precision walker stays on the checked polyline (never cuts corners)', () => {
    const route = routes.precision_exit;
    expect(['obstacle_checked', 'schematic_vertical']).toContain(route.checked); // 4 floors
    const { samples } = run(route, 0.05);
    // Walk polylines plus the rides' landing points (a ride keeps XY; floors change at its end).
    const segs = [
      ...route.path,
      ...route.transitions
        .filter((t) => t.kind !== 'door')
        .map((t) => ({ floor_id: t.to_floor_id, points: [t.position, (t as { to_position?: [number, number] }).to_position ?? t.position] })),
    ];
    for (const s of samples) {
      const p = s.progress!;
      const best = Math.min(
        ...segs
          .filter((seg) => seg.floor_id === p.floor_id)
          .flatMap((seg) =>
            seg.points.slice(1).map((b, i) => {
              const a = seg.points[i];
              const [dx, dy] = [b[0] - a[0], b[1] - a[1]];
              const len2 = dx * dx + dy * dy || 1;
              const t = Math.max(0, Math.min(1, ((p.position[0] - a[0]) * dx + (p.position[1] - a[1]) * dy) / len2));
              return Math.hypot(p.position[0] - (a[0] + t * dx), p.position[1] - (a[1] + t * dy));
            }),
          ),
      );
      expect(best).toBeLessThan(1e-6);
    }
  });

  it('official and precision routes stay distinguishable', () => {
    expect(routes.official_exit.mode).toBe('official');
    expect(routes.official_exit.checked).toBe('official_rules');
    expect(routes.precision_exit.mode).toBe('precision');
  });
});

describe('instructions', () => {
  it('cover start, doors, every floor change once, exit and arrival without ids', () => {
    const ins = buildInstructions(routes.official_exit, names('Exit from Corridor 0.01'));
    const kinds = ins.map((i) => i.kind);
    expect(kinds[0]).toBe('start');
    expect(kinds.at(-1)).toBe('arrive');
    expect(kinds.filter((k) => k === 'stairs')).toHaveLength(1); // three hops, one instruction
    expect(ins.find((i) => i.kind === 'stairs')?.text).toMatch(/down to /);
    for (const i of ins) expect(i.text).not.toMatch(/proc-|-F\d-|O\d{4}|S\d{3}/);
    expect(ins.map((i) => i.at_m)).toEqual([...ins.map((i) => i.at_m)].sort((a, b) => a - b));
  });

  it('a lift start is announced first', () => {
    const ins = buildInstructions(routes.official_lift_start, names('the printer'));
    expect(ins[1].kind).toBe('lift');
  });

  it('are announced once each, a few metres ahead, arrival only at the end', () => {
    const { events, sim } = run(routes.official_exit);
    const said = events.filter((e) => e.type === 'instruction');
    const keys = said.map((e) => (e.type === 'instruction' ? e.key : ''));
    expect(new Set(keys).size).toBe(keys.length);
    expect(said.length).toBe(sim.snapshot().instructions.length);
    const arrive = said.at(-1);
    expect(arrive?.type === 'instruction' && arrive.instruction.kind).toBe('arrive');
  });
});

describe('closures and missing routes', () => {
  it('knows which closures touch the part of the route still ahead', () => {
    const route = routes.official_exit;
    const ahead0 = remainingElements(route, 0);
    const stairs = route.transitions.find((t) => t.kind === 'stairs')!.element_id;
    expect(ahead0.has(stairs)).toBe(true);
    const total = route.waypoints!.at(-1)!.distance_m;
    expect(remainingElements(route, total).has(stairs)).toBe(false);
  });

  it('a missing route blocks instead of moving', () => {
    const sim = new GuidanceSim();
    sim.load({ ...routes.official_exit, status: 'unreachable', reachable: false, waypoints: [] }, []);
    expect(sim.start()[0].type).toBe('blocked');
    sim.tick(10);
    expect(sim.snapshot().status).toBe('blocked');
    expect(sim.snapshot().distance_m).toBe(0);
  });

  it('a reroute keeps moving from the start of the new route; hold stops the walker', () => {
    const sim = new GuidanceSim();
    sim.load(routes.official_exit, []);
    sim.start();
    sim.tick(10);
    sim.hold('Recalculating');
    const held = sim.snapshot().distance_m;
    sim.tick(10);
    expect(sim.snapshot().distance_m).toBe(held);
    sim.load(routes.official_lift_start, [], true);
    expect(sim.snapshot().status).toBe('active');
    expect(sim.snapshot().distance_m).toBe(0);
    expect(progressAt(routes.official_lift_start, 0)?.position).toEqual(sim.snapshot().progress?.position);
  });
});

describe('readable messages', () => {
  it('replaces element ids by names and never leaks unknown ids', async () => {
    const { readable } = await import('./simulation');
    const n = { ...names('x'), element: (id: string) => (id.endsWith('V01') ? 'Staircase 1' : null) };
    expect(readable('reachable if closures are lifted: proc-campus-dev-001-V01', n)).toBe(
      'reachable if closures are lifted: Staircase 1',
    );
    expect(readable('blocked by proc-campus-dev-001-F0-O0003', n)).not.toMatch(/proc-|O0003/);
  });
});

describe('door instructions say which way to turn', () => {
  const wp = (index: number, x: number, y: number, kind: string, space: string, element: string | null = null) => ({
    index,
    position: [x, y] as [number, number],
    floor_id: 'F0',
    space_id: space,
    kind,
    element_id: element,
    distance_m: 0,
    heading_deg: 0,
    elevation_m: 0,
  });
  const route = (doorTo: [number, number]) => {
    // walk east along a corridor, through a door at (6, 0), then on to doorTo
    const wps = [wp(0, 0, 0, 'start', 'C'), wp(1, 6, 0, 'door', 'C', 'D1'), wp(2, doorTo[0], doorTo[1], 'walk', 'R')];
    let d = 0;
    wps.forEach((w, i) => {
      if (i) d += Math.hypot(w.position[0] - wps[i - 1].position[0], w.position[1] - wps[i - 1].position[1]);
      w.distance_m = d;
    });
    return { status: 'ok', waypoints: wps, transitions: [] } as unknown as RouteResponse;
  };
  const room: Names = { space: (id) => (id === 'R' ? 'Kitchen 0.28' : id === 'C' ? 'Corridor' : null), floor: () => 'Ground floor', target: 'the coffee machine' };
  const doorText = (to: [number, number]) => buildInstructions(route(to), room).find((i) => i.kind === 'door')?.text;

  it('left, right and straight from the walking direction (counter-clockwise frame)', () => {
    expect(doorText([6, 4])).toBe('Turn left through the door into Kitchen 0.28.');
    expect(doorText([6, -4])).toBe('Turn right through the door into Kitchen 0.28.');
    expect(doorText([10, 0])).toBe('Go through the door into Kitchen 0.28.');
  });
});
