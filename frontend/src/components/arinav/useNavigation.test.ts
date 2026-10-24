// QA regressions (Phase A report): D1 cleared start reaches the assistant as null, U1 a route
// disappears when its start is cleared, U2 a closure preview disappears when the scenario moves.
import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, type NavState, type RouteResponse } from '../../lib/api';
import { useNavigation } from './useNavigation';

const B = 'b1';
const state = (revision: number, closed: string[] = []): NavState => ({
  building_id: B,
  revision,
  closed,
  support: { official: true, precision: false },
});
const route = (revision: number) =>
  ({
    mode: 'official',
    status: 'ok',
    reachable: true,
    checked: 'official_rules',
    length_m: 10,
    revision,
    path: [],
    transitions: [],
    waypoints: [],
    directions: [],
    warnings: [],
  }) as unknown as RouteResponse;

afterEach(() => vi.restoreAllMocks());

async function ready() {
  vi.spyOn(api, 'navState').mockResolvedValue(state(0));
  const hook = renderHook(() => useNavigation(B));
  await waitFor(() => expect(hook.result.current.state?.revision).toBe(0));
  return hook;
}

describe('useNavigation QA regressions', () => {
  it('D1: a cleared start is sent to the assistant as null until the next turn', async () => {
    const { result } = await ready();
    act(() => result.current.setEndpoint('start', { kind: 'space', id: 'S1' }));
    expect(result.current.ui.start).toEqual({ kind: 'space', id: 'S1' });
    act(() => result.current.setEndpoint('start', null));
    expect(result.current.ui).toHaveProperty('start', null);
    act(() =>
      result.current.applyTurn({ session_id: 's', reply: 'ok', status: 'ok', phases: [], ui: {}, tools: [] }),
    );
    expect(result.current.ui).not.toHaveProperty('start');
  });

  it('D1: clearing the simulated location is an explicit clear too', async () => {
    const { result } = await ready();
    act(() => result.current.setSimulated({ endpoint: { kind: 'space', id: 'S2' }, label: 'Office' } as never));
    act(() => result.current.setSimulated(null));
    expect(result.current.ui).toHaveProperty('start', null);
  });

  it('U1: clearing the start removes the route computed for it', async () => {
    const { result } = await ready();
    vi.spyOn(api, 'navRoute').mockResolvedValue(route(0));
    act(() => result.current.setEndpoint('start', { kind: 'space', id: 'S1' }));
    act(() => result.current.setEndpoint('target', { kind: 'space', id: 'S2' }));
    await act(() => result.current.computeRoute());
    expect(result.current.route?.status).toBe('ok');
    act(() => result.current.setEndpoint('start', null));
    expect(result.current.route).toBeNull();
  });

  it('U2: a closure preview made under an older revision is dropped', async () => {
    const { result } = await ready();
    vi.spyOn(api, 'navPreviewClosure').mockResolvedValue({ unreachable: ['S3'] });
    await act(() => result.current.previewClosure(['D1']));
    expect(result.current.preview).not.toBeNull();
    vi.spyOn(api, 'navScenario').mockResolvedValue(state(1, ['D1']));
    await act(() => result.current.scenario('close', 'D1'));
    await waitFor(() => expect(result.current.preview).toBeNull());
  });

  it('keeps a route that was computed under the current revision', async () => {
    const { result } = await ready();
    act(() => result.current.applyState(state(2)));
    act(() =>
      result.current.applyTurn({
        session_id: 's',
        reply: 'ok',
        status: 'ok',
        phases: [],
        ui: { route: route(2) },
        tools: [],
      }),
    );
    await waitFor(() => expect(result.current.route?.revision).toBe(2));
  });
});
