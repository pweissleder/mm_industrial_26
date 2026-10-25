// MemoryRecorder on real backend routes (campus-routes.json): walked XY excludes stair/lift rides,
// moved events are batched, arrival is recorded once, nothing is recorded without a session.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, type RouteResponse } from './api';
import fixture from './__fixtures__/campus-routes.json';
import { MemoryRecorder, memoryApi, xyAlong, type MemoryEventInput, type RecorderNames } from './memory';
import { buildInstructions, GuidanceSim, type Names } from './simulation';

const routes = fixture as unknown as Record<'official_exit' | 'official_lift_start' | 'precision_exit', RouteResponse> & {
  names: { floors: Record<string, string>; spaces: Record<string, string> };
};
const names: Names = {
  space: (id) => (id ? (routes.names.spaces[id] ?? null) : null),
  floor: (id) => (id ? (routes.names.floors[id] ?? null) : null),
  target: 'the exit',
};
const recorderNames: RecorderNames = { floor: names.floor, target: names.target };

/** XY metres of a route straight from its waypoints, same-floor legs only. */
function walkedXy(route: RouteResponse): number {
  const wps = route.waypoints ?? [];
  let xy = 0;
  for (let i = 0; i + 1 < wps.length; i++) {
    if (wps[i].floor_id !== wps[i + 1].floor_id) continue;
    xy += Math.hypot(wps[i + 1].position[0] - wps[i].position[0], wps[i + 1].position[1] - wps[i].position[1]);
  }
  return xy;
}

function recorder(sessionId: string | null = 'session-aaaaaaaaaaaa') {
  const sent: MemoryEventInput[] = [];
  const rec = new MemoryRecorder(sessionId, {
    send: (_sid, event) => {
      sent.push(event);
      return Promise.resolve();
    },
    names: recorderNames,
  });
  return { rec, sent };
}

/** Drive a GuidanceSim like useGuidance does and feed every call into the recorder. */
function drive(rec: MemoryRecorder, route: RouteResponse, opts: { pauseAt?: number; dt?: number; speed?: number } = {}) {
  const sim = new GuidanceSim();
  sim.setSpeed(opts.speed ?? 1);
  rec.observe(sim.load(route, buildInstructions(route, names)), sim.snapshot(), recorderNames);
  rec.observe(sim.start(), sim.snapshot());
  let paused = false;
  for (let i = 0; i < 100000 && sim.snapshot().status === 'active'; i++) {
    rec.observe(sim.tick(opts.dt ?? 0.1), sim.snapshot());
    if (!paused && opts.pauseAt !== undefined && sim.snapshot().distance_m >= opts.pauseAt) {
      paused = true;
      rec.observe(sim.pause(), sim.snapshot());
      rec.observe(sim.tick(5), sim.snapshot()); // time passes, nothing moves
      rec.observe(sim.resume(), sim.snapshot());
    }
  }
  return sim;
}

const moved = (sent: MemoryEventInput[]) =>
  sent.filter((e): e is Extract<MemoryEventInput, { type: 'moved' }> => e.type === 'moved');
const sum = (events: { data: { xy_m: number } }[]) => events.reduce((s, e) => s + e.data.xy_m, 0);

afterEach(() => vi.restoreAllMocks());

describe('xyAlong', () => {
  it('gives 0 XY metres for stair rides although they advance route distance', () => {
    const route = routes.official_exit;
    const wps = route.waypoints!;
    const rideFrom = wps.find((w) => w.kind === 'stairs')!.distance_m;
    const rideTo = wps.filter((w) => w.kind === 'stairs').at(-1)!.distance_m;
    expect(rideTo - rideFrom).toBeGreaterThan(20); // official cost of three flights
    const pieces = xyAlong(route, rideFrom, rideTo);
    expect(pieces.every((p) => p.vertical && p.xy_m === 0)).toBe(true);
    expect(sum(xyAlong(route, 0, route.waypoints!.at(-1)!.distance_m).map((p) => ({ data: p })))).toBeCloseTo(
      walkedXy(route),
      6,
    );
  });

  it('splits a partial segment proportionally', () => {
    const route = routes.official_exit;
    // waypoints 3 -> 4 are a straight 8.7 m leg on floor 3
    const [p] = xyAlong(route, 3.3, 3.3 + 4.35);
    expect(p.vertical).toBe(false);
    expect(p.xy_m).toBeCloseTo(4.35, 3);
  });
});

describe('MemoryRecorder', () => {
  it('records walked XY excluding the stair rides of official_exit', async () => {
    const { rec, sent } = recorder();
    drive(rec, routes.official_exit);
    await rec.idle();
    const route = routes.official_exit;
    const xy = walkedXy(route);
    expect(route.length_m! - xy).toBeGreaterThan(20); // route length/cost is not walked distance
    expect(sum(moved(sent))).toBeCloseTo(xy, 2);
    // Only the floors actually walked on; floors passed on the stairs are not "moved" on.
    expect(new Set(moved(sent).map((e) => e.data.floor_name))).toEqual(new Set(['Floor 3', 'Ground floor']));
    expect(sent[0].type).toBe('nav_start');
    expect(sent.at(-1)).toEqual({ type: 'arrival', data: { name: 'the exit', floor_name: 'Ground floor' } });
  });

  it('records 0 m for a lift ride (official_lift_start) and the precision stairs', async () => {
    for (const key of ['official_lift_start', 'precision_exit'] as const) {
      const { rec, sent } = recorder();
      drive(rec, routes[key]);
      await rec.idle();
      expect(sum(moved(sent))).toBeCloseTo(walkedXy(routes[key]), 2);
      // The walker's route distance includes the ride; walked XY does not.
      expect(sum(moved(sent))).toBeLessThan(routes[key].waypoints!.at(-1)!.distance_m - 10);
    }
  });

  it('batches moved events (>= 5 m unless flushed by a ride, pause or arrival)', async () => {
    const { rec, sent } = recorder();
    drive(rec, routes.official_exit, { dt: 0.05, pauseAt: 8 });
    await rec.idle();
    const types = sent.map((e) => e.type);
    expect(types.filter((t) => t === 'moved').length).toBeLessThanOrEqual(10);
    sent.forEach((e, i) => {
      if (e.type !== 'moved' || e.data.xy_m >= 5) return;
      const next = sent[i + 1];
      const flushed =
        next?.type === 'nav_pause' ||
        next?.type === 'arrival' ||
        (next?.type === 'moved' && next.data.floor_id !== e.data.floor_id);
      expect(flushed, `short moved #${i} (${e.data.xy_m} m) before ${next?.type}`).toBe(true);
    });
    // The pause flushed what was walked before it.
    const pauseIdx = types.indexOf('nav_pause');
    expect(types[pauseIdx - 1]).toBe('moved');
    expect(types[pauseIdx + 1]).toBe('nav_resume');
    expect(types.filter((t) => t === 'nav_start')).toHaveLength(1);
  });

  it('never records a second arrival for the same navigation', async () => {
    const { rec, sent } = recorder();
    const sim = drive(rec, routes.official_lift_start);
    rec.observe([{ type: 'arrive' }], sim.snapshot());
    rec.arrive('Office 2.14');
    rec.observe(sim.tick(1), sim.snapshot());
    await rec.idle();
    expect(sent.filter((e) => e.type === 'arrival')).toHaveLength(1);
    // A new navigation may arrive again.
    rec.observe(sim.start(), sim.snapshot());
    for (let i = 0; i < 1000 && sim.snapshot().status === 'active'; i++) rec.observe(sim.tick(0.5), sim.snapshot());
    await rec.idle();
    expect(sent.filter((e) => e.type === 'arrival')).toHaveLength(2);
    expect(sent.filter((e) => e.type === 'nav_start')).toHaveLength(2);
  });

  it('records nothing while disabled (session null) and nothing for a preview/plan', async () => {
    const { rec, sent } = recorder(null);
    drive(rec, routes.official_exit);
    rec.searchIntent('where is the exit');
    rec.destinationConfirmed({ name: 'Exit', kind: 'opening' });
    await rec.idle();
    expect(sent).toEqual([]);
    expect(rec.enabled).toBe(false);

    // Loading a route (plan) and previewing it never moves the sim: no events.
    const on = recorder();
    const sim = new GuidanceSim();
    on.rec.observe(sim.load(routes.official_exit, []), sim.snapshot());
    on.rec.observe(sim.tick(30), sim.snapshot());
    await on.rec.idle();
    expect(on.sent).toEqual([]);
  });

  it('records a stop and a cancelled navigation when the route is replaced while walking', async () => {
    const { rec, sent } = recorder();
    const sim = new GuidanceSim();
    rec.observe(sim.load(routes.official_exit, buildInstructions(routes.official_exit, names)), sim.snapshot());
    rec.observe(sim.start(), sim.snapshot());
    for (let i = 0; i < 60; i++) rec.observe(sim.tick(0.1), sim.snapshot());
    // A reroute (scenario change) keeps navigating on the new route.
    rec.observe(sim.load(routes.precision_exit, [], true), sim.snapshot());
    await rec.idle();
    expect(sent.at(-1)).toEqual({ type: 'reroute', data: { reason: 'scenario_changed' } });
    for (let i = 0; i < 60; i++) rec.observe(sim.tick(0.1), sim.snapshot());
    // The user picks a new destination: the sim reloads idle without a stop event.
    rec.observe(sim.load(routes.official_lift_start, []), sim.snapshot());
    await rec.idle();
    expect(sent.at(-1)?.type).toBe('nav_stop');
    expect(sum(moved(sent))).toBeCloseTo(1.3 * 12, 1); // 6 s + 6 s at walking speed, all on floor 3
    expect(sent.filter((e) => e.type === 'arrival')).toHaveLength(0);
  });

  it('truncates search intents to 120 characters', async () => {
    const { rec, sent } = recorder();
    rec.searchIntent('x'.repeat(500));
    await rec.idle();
    expect(sent[0]).toEqual({ type: 'search_intent', data: { text: 'x'.repeat(120) } });
  });

  it('stops recording into a session the server no longer has', async () => {
    const send = vi.fn().mockRejectedValue(new ApiError(404, 'gone'));
    const rec = new MemoryRecorder('session-bbbbbbbbbbbb', { send });
    rec.searchIntent('printer');
    await rec.idle();
    expect(rec.enabled).toBe(false);
    rec.searchIntent('again');
    await rec.idle();
    expect(send).toHaveBeenCalledTimes(1);
  });
});

describe('memoryApi', () => {
  it('posts events to /api/memory/events with the session id', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(new Response(JSON.stringify({ type: 'nav_start', at: 'x', data: {} }), { status: 201 }));
    await memoryApi.addEvent('sid-cccccccccccccccc', { type: 'nav_start' });
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toMatch(/\/api\/memory\/events$/);
    expect(JSON.parse(String(init?.body))).toEqual({ session_id: 'sid-cccccccccccccccc', type: 'nav_start', data: {} });
  });

  it('turns a 404 into an ApiError', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ detail: 'nope' }), { status: 404 }));
    await expect(memoryApi.recap('sid')).rejects.toMatchObject({ status: 404, message: 'nope' });
  });
});
