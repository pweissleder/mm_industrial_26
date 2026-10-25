import { describe, expect, it } from 'vitest';
import type { RouteResponse } from './api';
import type { BuildingModel } from './building';
import { buildUiState, DEFAULT_NAV, endpointLabel, readRouteError, replyAsks, routeBadge, routeStart } from './nav';
import { decodePcm16, encodeWav, STT_RATE } from './voice';
import { routeDrawing } from '../components/arinav/routeGeometry';

const model = {
  building: { id: 'b', name: 'B' },
  floors: [
    { id: 'F0', level: 0, name: 'Ground' },
    { id: 'F1', level: 1, name: 'First' },
  ],
  spaces: [
    { id: 'S1', name: 'Lobby 0.01', floor_id: 'F0' },
    { id: 'S2', name: 'Office 0.02', floor_id: 'F0' },
  ],
  openings: [
    { id: 'b-F0-O1', kind: 'entrance', space_ids: ['S1', 'outside'], floor_id: 'F0' },
    { id: 'b-F0-O2', kind: 'door', space_ids: ['S1', 'S2'], floor_id: 'F0' },
  ],
  assets: [],
  vertical_connectors: [],
} as unknown as BuildingModel;

const route = {
  mode: 'official',
  status: 'ok',
  reachable: true,
  checked: 'official_rules',
  length_m: 20,
  revision: 0,
  start: { kind: 'space', id: 'S9', space_id: 'S9', floor_id: 'F1', position: [1, 1] },
  target: { kind: 'opening', id: 'b-F0-O1', space_id: 'S1', floor_id: 'F0', position: [0, 5] },
  path: [
    { floor_id: 'F1', kind: 'walk', points: [[1, 1], [4, 1]] },
    { floor_id: 'F1', kind: 'connector', points: [[4, 1], [4, 1]] },
    { floor_id: 'F0', kind: 'walk', points: [[4, 1], [0, 5]] },
  ],
  transitions: [
    { kind: 'stairs', element_id: 'V', from_floor_id: 'F1', to_floor_id: 'F0', position: [4, 1], schematic: true },
    { kind: 'door', element_id: 'b-F0-O1', from_floor_id: 'F0', to_floor_id: 'F0', position: [0, 5], schematic: false },
  ],
  directions: [],
  warnings: [],
} as unknown as RouteResponse;

describe('endpoint labels', () => {
  it('names exits and doors like the backend search does', () => {
    expect(endpointLabel({ kind: 'opening', id: 'b-F0-O1' }, model)).toBe('Exit from Lobby 0.01');
    expect(endpointLabel({ kind: 'opening', id: 'b-F0-O2' }, model)).toBe('Door between Lobby 0.01 and Office 0.02');
    expect(endpointLabel({ kind: 'space', id: 'S2' }, model)).toBe('Office 0.02');
  });
});

describe('route drawing', () => {
  it('draws walks on every floor, the stairs between them and ends at the exit door', () => {
    const d = routeDrawing(route);
    expect(d.lines.map((l) => l.floorId)).toEqual(['F1', 'F0']);
    expect(d.vertical).toEqual([
      { kind: 'stairs', position: [4, 1], fromFloorId: 'F1', toFloorId: 'F0', schematic: true },
    ]);
    expect(d.doors).toEqual([{ elementId: 'b-F0-O1', floorId: 'F0', position: [0, 5] }]);
    expect(d.target).toEqual({ floorId: 'F0', position: [0, 5] });
    expect(d.style).toBe('official');
  });

  it('labels official and precision routes honestly', () => {
    expect(routeBadge(route).tone).toBe('official');
    expect(routeBadge({ ...route, mode: 'precision', checked: 'obstacle_checked' }).tone).toBe('checked');
    expect(routeBadge({ ...route, mode: 'precision', checked: 'schematic_vertical' }).tone).toBe('schematic');
  });
});

describe('assistant context', () => {
  it('sends only explicit user choices', () => {
    const ui = buildUiState({
      buildingId: 'b',
      start: { endpoint: { kind: 'space', id: 'S1' }, source: 'assistant' },
      target: { endpoint: { kind: 'space', id: 'S2' }, source: 'user' },
      simulated: null,
      settings: { ...DEFAULT_NAV, step_free: true },
      touched: new Set(['step_free']),
    });
    expect(ui).toEqual({ building_id: 'b', destination: { kind: 'space', id: 'S2' }, step_free: true });
  });

  it('reads nested route errors and diagnosis summaries', () => {
    expect(readRouteError({ error: { code: 'x', message: 'No path' } })?.message).toBe('No path');
    expect(
      readRouteError({ message: 'm', details: { diagnosis: { summary: 'Door D is too narrow' } } })?.message,
    ).toBe('Door D is too narrow');
    expect(readRouteError(null)).toBeNull();
  });

  it('offers quick answers only for questions', () => {
    expect(replyAsks('Where are you right now?')).toBe('start');
    expect(replyAsks('Do you mean the north or the south lift?')).toBe('choice');
    expect(replyAsks('The kitchen is on floor 1.')).toBeNull();
  });
});

describe('voice encoding', () => {
  it('encodes 16 kHz mono WAV within the 10 s limit and decodes PCM back', () => {
    const second = new Float32Array(48000).fill(0.5);
    const b64 = encodeWav([second], 48000);
    const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
    expect(String.fromCharCode(...bytes.slice(0, 4))).toBe('RIFF');
    expect(bytes.length).toBe(44 + STT_RATE * 2);
    const long = encodeWav([new Float32Array(16000 * 12)], 16000);
    expect(atob(long).length).toBe(44 + STT_RATE * 10 * 2);
    const pcm = btoa(String.fromCharCode(0x00, 0x40, 0x00, 0xc0));
    const out = decodePcm16(pcm);
    expect(out[0]).toBeCloseTo(0.5, 2);
    expect(out[1]).toBeCloseTo(-0.5, 2);
  });
});

describe('location after navigation (G07)', () => {
  it('a walked-to location is the next start, labelled as simulation', () => {
    const ui = buildUiState({
      buildingId: 'b',
      start: { endpoint: { kind: 'space', id: 'S1' }, source: 'user' },
      target: null,
      simulated: { endpoint: { kind: 'opening', id: 'EXIT' }, label: 'Exit', source: 'simulation' },
      settings: DEFAULT_NAV,
      touched: new Set(),
    });
    expect(ui.start).toEqual({ kind: 'opening', id: 'EXIT' });
    expect(ui.start_source).toBe('simulation');
  });

  it('a manual start still wins over a manually set simulated room', () => {
    const ui = buildUiState({
      buildingId: 'b',
      start: { endpoint: { kind: 'space', id: 'S1' }, source: 'user' },
      target: null,
      simulated: { endpoint: { kind: 'space', id: 'S2' }, label: 'Office', source: 'manual' },
      settings: DEFAULT_NAV,
      touched: new Set(),
    });
    expect(ui.start).toEqual({ kind: 'space', id: 'S1' });
    expect(ui.start_source).toBeUndefined();
  });

  it('local routes start where the assistant would: walked location, else the chosen start', () => {
    const user = { endpoint: { kind: 'space', id: 'S1' }, source: 'user' } as const;
    const walked = { endpoint: { kind: 'opening', id: 'EXIT' }, label: 'Exit', source: 'simulation' } as const;
    const manual = { endpoint: { kind: 'space', id: 'S2' }, label: 'Office', source: 'manual' } as const;
    expect(routeStart(user, walked)).toEqual({ kind: 'opening', id: 'EXIT' });
    expect(routeStart(user, manual)).toEqual({ kind: 'space', id: 'S1' });
    expect(routeStart(null, manual)).toEqual({ kind: 'space', id: 'S2' });
    expect(routeStart(null, null)).toBeUndefined();
  });
});
