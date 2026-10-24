// Guidance hook: reroute on scenario change, stale async answers dropped, audio failures never
// block, missing routes stop the walker (B5 #6, #7, #10, #11).
import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, type RouteResponse } from '../../lib/api';
import fixture from '../../lib/__fixtures__/campus-routes.json';
import { DEFAULT_NAV } from '../../lib/nav';
import type { Names } from '../../lib/simulation';
import { useGuidance } from './useGuidance';

const routes = fixture as unknown as Record<string, RouteResponse>;
const names: Names = { space: () => null, floor: () => null, target: 'the exit' };

afterEach(() => vi.restoreAllMocks());

function setup(voice?: { say: (k: string, t: string) => void; cancel: () => void }) {
  const onRoute = vi.fn();
  const hook = renderHook(
    (props: { route: RouteResponse | null; revision: number }) =>
      useGuidance({
        buildingId: 'proc-campus-dev-001',
        route: props.route,
        names,
        scenarioRevision: props.revision,
        settings: DEFAULT_NAV,
        onRoute,
        voice,
      }),
    { initialProps: { route: { ...routes.official_exit, revision: 0 } as RouteResponse | null, revision: 0 } },
  );
  return { ...hook, onRoute };
}

const walk = (hook: ReturnType<typeof setup>, seconds: number) =>
  act(() => {
    (window as unknown as { __arinavGuidance: { manualClock(b: boolean): void; advance(s: number): void } }).__arinavGuidance.manualClock(true);
    (window as unknown as { __arinavGuidance: { advance(s: number): void } }).__arinavGuidance.advance(seconds);
  });

describe('useGuidance', () => {
  it('reroutes from the walker position when the scenario changes and shows the new route', async () => {
    const next = { ...routes.official_lift_start, revision: 1 };
    const navRoute = vi.spyOn(api, 'navRoute').mockResolvedValue(next);
    const hook = setup();
    act(() => hook.result.current.start());
    walk(hook, 5);
    const at = hook.result.current.snapshot.progress!.position;
    hook.rerender({ route: hook.result.current.snapshot.route, revision: 1 });
    await waitFor(() => expect(hook.result.current.snapshot.route_revision).toBe(2));
    const request = navRoute.mock.calls[0][1];
    expect(request.start).toMatchObject({ kind: 'point', position: at });
    expect(request.exact_start).toBe(true);
    expect(hook.onRoute).toHaveBeenCalledWith(next);
    expect(hook.result.current.snapshot.status).toBe('active');
  });

  it('drops a late reroute answer when the user picked a new route meanwhile', async () => {
    let resolve: (r: RouteResponse) => void = () => {};
    vi.spyOn(api, 'navRoute').mockImplementation(() => new Promise((r) => (resolve = r)));
    const hook = setup();
    act(() => hook.result.current.start());
    walk(hook, 3);
    hook.rerender({ route: hook.result.current.snapshot.route, revision: 1 }); // reroute pending
    await waitFor(() => expect(hook.result.current.snapshot.status).toBe('recomputing'));
    const userRoute = { ...routes.official_lift_start, revision: 1 };
    hook.rerender({ route: userRoute, revision: 1 }); // newer state wins
    await act(async () => resolve({ ...routes.precision_exit, revision: 1 }));
    expect(hook.result.current.snapshot.route).toBe(userRoute);
    expect(hook.onRoute).not.toHaveBeenCalled();
  });

  it('stops and explains when no route is left', async () => {
    vi.spyOn(api, 'navRoute').mockResolvedValue({
      ...routes.official_exit,
      status: 'unreachable',
      reachable: false,
      unreachable_reason: 'All staircases are closed.',
      waypoints: [],
    });
    const hook = setup();
    act(() => hook.result.current.start());
    walk(hook, 3);
    const d = hook.result.current.snapshot.distance_m;
    hook.rerender({ route: hook.result.current.snapshot.route, revision: 1 });
    await waitFor(() => expect(hook.result.current.snapshot.status).toBe('blocked'));
    expect(hook.result.current.snapshot.message).toContain('staircases');
    walk(hook, 10);
    expect(hook.result.current.snapshot.distance_m).toBe(d);
  });

  it('a failing voice never blocks the walk', () => {
    const voice = {
      say: vi.fn((_key: string, _text: string) => {
        throw new Error('speaker broken');
      }),
      cancel: vi.fn(),
    };
    const hook = setup({ say: (k, t) => { try { voice.say(k, t); } catch { /* GuidanceVoice never throws; guard */ } }, cancel: voice.cancel });
    act(() => hook.result.current.start());
    walk(hook, 200);
    expect(hook.result.current.snapshot.status).toBe('arrived');
    expect(voice.say).toHaveBeenCalled();
  });
});
