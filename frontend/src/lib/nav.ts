// Pure helpers shared by the assistant chat, the developer panel and the 3D route view.
import type {
  ClearanceProfile,
  Diagnosis,
  Endpoint,
  NavMode,
  NavSearchResult,
  RouteError,
  RouteResponse,
  UiState,
} from './api';
import type { BuildingModel, Point, Selection } from './building';

export type AppMode = 'assistant' | 'developer';

export interface NavSettings {
  mode: NavMode;
  profile: ClearanceProfile;
  width_m: number;
  safety_m: number;
  snap_m: number;
  step_free: boolean;
  allow_schematic_vertical: boolean;
}

export const DEFAULT_NAV: NavSettings = {
  mode: 'official',
  profile: 'stationary',
  width_m: 0.6,
  safety_m: 0.05,
  snap_m: 0,
  step_free: false,
  allow_schematic_vertical: true,
};

/** Who chose an endpoint: only the user's own choices go back to the assistant as `ui`. */
export interface EndpointChoice {
  endpoint: Endpoint;
  source: 'user' | 'assistant';
}

export interface SimulatedLocation {
  endpoint: Endpoint;
  label: string;
}

/** A selection in the 3D view as a route endpoint; stairs/lifts and GLB nodes can't be one. */
export function endpointFromSelection(
  sel: Selection,
  hit?: { position: Point; spaceId?: string | null },
  exactPoint = false,
): Endpoint | null {
  if (sel.kind === 'space') {
    return exactPoint && hit
      ? { kind: 'point', space_id: sel.id, position: hit.position }
      : { kind: 'space', id: sel.id };
  }
  if (sel.kind === 'asset') return { kind: 'asset', id: sel.id };
  if (sel.kind === 'opening') return { kind: 'opening', id: sel.id };
  return null;
}

export function endpointFromSearch(r: NavSearchResult): Endpoint | null {
  if (r.kind === 'room') return { kind: 'space', id: r.id };
  if (r.kind === 'asset') return { kind: 'asset', id: r.id };
  if (r.kind === 'opening' || r.kind === 'exit') return { kind: 'opening', id: r.id };
  return null;
}

/** Map a focus `{kind, id}` from the assistant onto a 3D selection. */
export function selectionFromFocus(focus: { kind: string; id: string }): Selection {
  const kind = focus.kind.toLowerCase();
  if (kind === 'room' || kind === 'space') return { kind: 'space', id: focus.id };
  if (kind === 'opening' || kind === 'exit' || kind === 'door') return { kind: 'opening', id: focus.id };
  if (kind === 'connector' || kind === 'stairs' || kind === 'lift') {
    return { kind: 'connector', id: focus.id };
  }
  return { kind: 'asset', id: focus.id };
}

/** A selection for any element id, with its kind looked up in the model. */
export function selectionForId(id: string, model: BuildingModel | null): Selection {
  if (model?.assets.some((a) => a.id === id)) return { kind: 'asset', id };
  if (model?.openings.some((o) => o.id === id)) return { kind: 'opening', id };
  if (model?.vertical_connectors.some((c) => c.id === id)) return { kind: 'connector', id };
  return { kind: 'space', id };
}

export const endpointId = (e: Endpoint) => (e.kind === 'point' ? e.space_id : e.id);

export function endpointLabel(e: Endpoint, model: BuildingModel | null): string {
  if (e.kind === 'point') {
    const space = model?.spaces.find((s) => s.id === e.space_id);
    return `Point in ${space?.name ?? e.space_id} (${e.position.map((v) => v.toFixed(1)).join(', ')})`;
  }
  if (e.kind === 'space') return model?.spaces.find((s) => s.id === e.id)?.name ?? e.id;
  if (e.kind === 'asset') return model?.assets.find((a) => a.id === e.id)?.name ?? e.id;
  const o = model?.openings.find((x) => x.id === e.id);
  if (!o) return `Opening ${e.id.split('-').pop()}`;
  const names = o.space_ids
    .filter((id) => id !== 'outside')
    .map((id) => model?.spaces.find((s) => s.id === id)?.name ?? id);
  // Same wording as the backend search: "Exit from Lobby", "Door between A and B".
  if (o.space_ids.includes('outside')) return `Exit from ${names[0] ?? 'the building'}`;
  return `${o.kind === 'opening' ? 'Opening' : 'Door'} between ${names.join(' and ')}`;
}

export function elementLabel(id: string, model: BuildingModel | null): string {
  if (!model) return id;
  const space = model.spaces.find((s) => s.id === id);
  if (space) return space.name;
  const asset = model.assets.find((a) => a.id === id);
  if (asset) return asset.name;
  const vc = model.vertical_connectors.find((c) => c.id === id);
  if (vc) return vc.name;
  if (model.openings.some((o) => o.id === id)) return endpointLabel({ kind: 'opening', id }, model);
  return id;
}

/** What kind of guarantee a route carries, as a short badge. */
export function routeBadge(route: RouteResponse): { label: string; tone: 'official' | 'checked' | 'schematic' } {
  if (route.mode === 'official') return { label: 'Official route · furniture ignored', tone: 'official' };
  if (route.checked === 'schematic_vertical') return { label: 'Schematic floor change', tone: 'schematic' };
  return { label: 'Checked precision route', tone: 'checked' };
}

/** Normalise a route error from a turn (`{code, message, details}` or nested in `error`). */
export function readRouteError(e: RouteError | null | undefined): {
  message: string;
  diagnosis: Diagnosis | null;
} | null {
  if (!e) return null;
  const inner = e.error ?? e;
  const diagnosis = inner.details?.diagnosis ?? null;
  const message = diagnosis?.summary || inner.message || 'No route could be found.';
  return { message, diagnosis };
}

export const diagnosisIds = (d: Diagnosis | null | undefined): string[] =>
  (d?.elements ?? []).map((x) => String(x.id)).filter(Boolean);

/** The `ui` sent with a turn: explicit user choices only. */
export function buildUiState(args: {
  buildingId: string | null;
  start: EndpointChoice | null;
  target: EndpointChoice | null;
  simulated: SimulatedLocation | null;
  settings: NavSettings;
  touched: ReadonlySet<keyof NavSettings>;
  /** Start/target the user explicitly cleared since the last turn: sent as null. */
  cleared?: ReadonlySet<'start' | 'target'>;
}): UiState {
  const ui: UiState = { building_id: args.buildingId };
  const start = args.start?.source === 'user' ? args.start.endpoint : args.simulated?.endpoint;
  if (start) ui.start = start;
  else if (args.cleared?.has('start')) ui.start = null;
  if (args.target?.source === 'user') ui.destination = args.target.endpoint;
  else if (args.cleared?.has('target')) ui.destination = null;
  const s = args.settings;
  if (args.touched.has('mode')) ui.mode = s.mode;
  if (args.touched.has('step_free')) ui.step_free = s.step_free;
  if (args.touched.has('profile')) ui.profile = s.profile;
  if (args.touched.has('width_m')) ui.width_m = s.width_m;
  if (args.touched.has('safety_m')) ui.safety_m = s.safety_m;
  return ui;
}

const START_QUESTION =
  /where are you|your (current )?(location|position)|where (do|would|will) you (like to )?start|starting point|where you are/i;

/** Which suggestion chips fit a reply: none, a question about the start, or another question. */
export function replyAsks(reply: string): 'start' | 'choice' | null {
  const text = reply.trim();
  if (!text.includes('?')) return null;
  return START_QUESTION.test(text) ? 'start' : 'choice';
}

export const formatMetres = (m: number | null | undefined) =>
  m === null || m === undefined || !Number.isFinite(m) ? '–' : `${m.toFixed(1)} m`;
