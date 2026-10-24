import type { BuildingModel, Point } from './building';

const API_BASE_URL = import.meta.env.PUBLIC_API_BASE_URL ?? '';

export interface HealthResponse {
  status: string;
  environment: string;
  llm_enabled: boolean;
  managed_agent_enabled: boolean;
  tracing_enabled: boolean;
}

export interface ChatResponse {
  reply: string;
  thread_id: string;
}

export interface IngestionResponse {
  dataset: string;
  building_id: string;
  building_name: string;
  floors: number;
  spaces: number;
  openings: number;
  vertical_connectors: number;
  assets: number;
  replaced: boolean;
}

export interface BuildingSummary {
  id: string;
  name: string;
  dataset: string;
  source: string;
  license: string;
  ingested_at: string;
}

export interface DatasetFile {
  name: string;
  size_bytes: number;
}

export interface UploadResponse {
  dataset: string;
  files: DatasetFile[];
  ingestible: boolean;
}

// --- ingestion pipeline (backend/app/pipeline) ---------------------------------------

export type RunStatus = 'queued' | 'running' | 'needs_review' | 'completed' | 'failed' | 'aborted';
export type ReviewOptionId = 'accept_with_warnings' | 'retry' | 'abort';

export interface StageRun {
  status: 'pending' | 'running' | 'passed' | 'failed' | 'skipped';
  attempts: number;
  summary: string | null;
}

export interface ReviewRequest {
  review_id: string;
  run_id: string;
  stage: string;
  title: string;
  message: string;
  issues: string[];
  /** Paths relative to the location folder, see `locationFileUrl`. */
  artifacts: string[];
  options: { id: ReviewOptionId; label: string; description: string }[];
  allow_note: boolean;
}

export interface RunRecord {
  run_id: string;
  dataset: string;
  location_id: string | null;
  status: RunStatus;
  current_stage: string | null;
  offline: boolean;
  created_at: string;
  updated_at: string;
  stages: Record<string, StageRun>;
  pending_review: ReviewRequest | null;
  decisions: { stage: string | null; action: string; reason: string }[];
  error: string | null;
}

export const TERMINAL_RUN: ReadonlySet<RunStatus> = new Set(['completed', 'failed', 'aborted']);

export type PillarState = 'pending' | 'running' | 'done' | 'degraded' | 'failed' | 'skipped';

export interface SpaceMeta {
  space_id: string;
  description: string;
  area_m2: number;
  step_free_access: boolean;
  issues: string[];
}

export interface LocationRecord {
  id: string;
  dataset: string;
  name: string;
  updated_at: string;
  pillars: Record<string, { status: PillarState; note: string | null }>;
  metadata: {
    summary: string;
    key_facts: string[];
    spaces: Record<string, SpaceMeta>;
  } | null;
  artifacts: Record<string, string>;
}

// --- navigation, assistant and voice (docs/PHASE_A_INTERFACES.md) -------------------

/** Start or target of a route: a tagged union. */
export type Endpoint =
  | { kind: 'space'; id: string }
  | { kind: 'asset'; id: string }
  | { kind: 'opening'; id: string; side_space_id?: string }
  | { kind: 'point'; space_id: string; position: Point };

export type NavMode = 'official' | 'precision';
export type ClearanceProfile = 'stationary' | 'all' | 'none';

export interface NavState {
  building_id: string;
  revision: number;
  closed: string[];
  support: { official: boolean; precision: boolean; precision_reason?: string | null };
}

export type SearchKind = 'room' | 'asset' | 'opening' | 'exit' | 'connector';

export interface NavSearchResult {
  kind: SearchKind;
  id: string;
  name: string;
  type: string;
  floor_id: string;
  floor_level: number;
  space_id?: string | null;
  space_name?: string | null;
  is_exit?: boolean;
  closed?: boolean;
  score: number;
}

export interface NavSearchOptions {
  kind?: SearchKind;
  floor_level?: number;
  limit?: number;
}

export interface NearestRequest {
  from: Endpoint;
  /** `exit`, `room_type:<t>`, `asset_type:<t>` or `query:<words>`. */
  category: string;
  step_free?: boolean;
  limit?: number;
}

export interface NearestResult {
  endpoint: Endpoint;
  name: string;
  floor_level: number;
  walk_cost_m: number;
}

export interface NearestResponse {
  results: NearestResult[];
  from: unknown;
}

export interface RouteRequest {
  mode: NavMode;
  start: Endpoint;
  target: Endpoint;
  via?: Endpoint[];
  step_free?: boolean;
  /** Preview only, merged with scenario closures, never stored. */
  assume_closed?: string[];
  profile?: ClearanceProfile;
  width_m?: number;
  safety_m?: number;
  snap_m?: number;
  allow_schematic_vertical?: boolean;
  unofficial?: { no_production_transit?: boolean; forklift?: boolean };
}

export interface RouteEnd {
  kind: string;
  id?: string | null;
  space_id: string;
  floor_id: string;
  position: Point;
}

export interface RoutePathSegment {
  floor_id: string;
  space_id?: string | null;
  kind: 'walk' | 'door' | 'connector';
  points: Point[];
}

export interface RouteTransition {
  kind: 'door' | 'stairs' | 'lift';
  element_id: string;
  from_floor_id: string;
  to_floor_id: string;
  position: Point;
  schematic: boolean;
  cost_m?: number;
}

/** Phase B: a camera/walker follows these. Kept untouched in state for now. */
export interface RouteWaypoint {
  index: number;
  floor_id: string;
  elevation_m: number;
  position: Point;
  heading_deg: number;
  kind: 'start' | 'walk' | 'door' | 'stairs' | 'lift' | 'target';
  element_id?: string | null;
  distance_m: number;
  fraction: number;
}

export interface Diagnosis {
  category?: string;
  summary?: string;
  elements?: { id: string; reason?: string; category?: string }[];
  clearance_m?: number;
  [key: string]: unknown;
}

export interface RouteResponse {
  mode: NavMode;
  status: 'ok' | 'unreachable' | 'unsupported' | 'blocked';
  reachable: boolean;
  checked: 'official_rules' | 'obstacle_checked' | 'schematic_vertical';
  length_m: number | null;
  walk_m?: number | null;
  vertical_cost_m?: number | null;
  revision: number;
  start?: RouteEnd | null;
  target?: RouteEnd | null;
  space_path?: string[];
  element_path?: string[];
  path: RoutePathSegment[];
  transitions: RouteTransition[];
  waypoints?: RouteWaypoint[];
  directions: string[];
  unreachable_reason?: string | null;
  diagnosis?: Diagnosis | null;
  warnings: string[];
  assumptions?: string[];
  elapsed_ms?: number;
}

export interface PreviewClosureRequest {
  element_ids: string[];
  from?: Endpoint;
  step_free?: boolean;
}

/** Rooms that become unreachable / longer; the contract leaves the exact shape open. */
export type PreviewClosureResponse = Record<string, unknown>;

export interface ScenarioRequest {
  action: 'close' | 'open' | 'reset';
  element_id?: string;
  expected_revision: number;
}

export interface OperationResult {
  formatted_answer?: string;
  ids?: string[];
  [key: string]: unknown;
}

export interface OperationSpec {
  name: string;
  label?: string;
  fields?: { name: string; label?: string; type?: string; default?: unknown; required?: boolean }[];
  [key: string]: unknown;
}

/** A train/dev benchmark question; the contract leaves the shape open (prototype fields). */
export interface NavExample {
  id: string;
  kind?: string;
  operation?: string;
  question?: string;
  building_id?: string;
  inputs?: Record<string, unknown>;
  arguments?: Record<string, unknown>;
  answer?: unknown;
  [key: string]: unknown;
}

export interface LocationResolveRequest {
  building_id: string;
  source: 'map_click' | 'room_id' | 'door_qr' | 'simulated';
  space_id?: string;
  opening_id?: string;
  side_space_id?: string;
  position?: Point;
  floor_id?: string;
}

export interface LocationResolveResponse {
  status: 'resolved' | 'ambiguous' | 'outside_building';
  endpoint: Endpoint | null;
  candidates: unknown[];
  explanation: string;
  simulated: boolean;
}

export interface AssistantStatus {
  live: boolean;
  voice: boolean;
  model: string;
  effort: string;
  tts_speed: { min: number; max: number; default: number };
}

/** Explicit user choices only; nothing the assistant chose itself. */
export interface UiState {
  building_id: string | null;
  start?: Endpoint;
  destination?: Endpoint;
  mode?: NavMode;
  step_free?: boolean;
  profile?: ClearanceProfile;
  width_m?: number;
  safety_m?: number;
}

export interface TurnRequest {
  session_id?: string;
  text: string;
  ui: UiState;
  speak?: boolean;
  voice?: boolean;
  tts_speed?: number;
}

export interface Proposal {
  proposal_id: string;
  action: string;
  element_id: string;
  element_name: string;
  element_kind: string;
}

export interface RouteError {
  code?: string;
  message?: string;
  details?: { diagnosis?: Diagnosis | null; [key: string]: unknown };
  /** Some backends nest it: `{error: {...}}`. */
  error?: RouteError;
}

export interface NavSettingsPatch {
  mode?: NavMode;
  profile?: ClearanceProfile;
  width_m?: number;
  safety_m?: number;
  snap_m?: number;
  step_free?: boolean;
  allow_schematic_vertical?: boolean;
}

export interface TurnUi {
  route?: RouteResponse | null;
  route_error?: RouteError | null;
  focus?: { kind: string; id: string } | null;
  highlight?: string[] | null;
  endpoints?: { start?: Endpoint | null; destination?: Endpoint | null } | null;
  settings?: NavSettingsPatch | null;
  proposal?: Proposal | null;
  operation?: OperationResult | null;
  building_id?: string | null;
}

export interface ToolCall {
  tool: string;
  input: unknown;
  status: string;
  output: unknown;
  elapsed_ms: number;
}

export interface TurnResponse {
  session_id: string;
  reply: string;
  status: string;
  phases: string[];
  ui: TurnUi;
  tools: ToolCall[];
  usage?: {
    input_tokens?: number;
    output_tokens?: number;
    cache_creation_input_tokens?: number;
    cache_read_input_tokens?: number;
    estimated_usd?: number;
  };
  timing_ms?: { claude_ms?: number; tools_ms?: number; total_ms?: number; tts_ms?: number };
  /** 16-bit little-endian mono PCM, base64. */
  audio_pcm16?: string | null;
  sample_rate?: number;
  voice_error?: string | null;
}

export interface ConfirmResponse {
  status: 'applied' | 'cancelled';
  message: string;
  state: NavState | null;
}

export interface TranscribeResponse {
  text: string;
  stt_ms: number;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    /** Machine-readable code from `{"error": {"code", ...}}` bodies, e.g. `unknown_place`. */
    readonly code?: string,
    /** `error.details`, e.g. `{diagnosis}` for an unroutable request. */
    readonly details?: Record<string, unknown>,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

interface ErrorBody {
  detail?: unknown;
  error?: { code?: unknown; message?: unknown; details?: unknown };
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  // Let the browser set the multipart boundary for FormData bodies.
  const json = !(init?.body instanceof FormData);
  const response = await fetch(`${API_BASE_URL}/api${path}`, {
    ...init,
    headers: { ...(json && { 'Content-Type': 'application/json' }), ...init?.headers },
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as ErrorBody | null;
    const err = body?.error;
    const message =
      typeof err?.message === 'string'
        ? err.message
        : typeof body?.detail === 'string'
          ? body.detail
          : `Request to ${path} failed with ${response.status}`;
    const details =
      err?.details && typeof err.details === 'object'
        ? (err.details as Record<string, unknown>)
        : undefined;
    throw new ApiError(
      response.status,
      message,
      typeof err?.code === 'string' ? err.code : undefined,
      details,
    );
  }
  return (await response.json()) as T;
}

const post = (body: unknown): RequestInit => ({ method: 'POST', body: JSON.stringify(body) });
const navPath = (buildingId: string) => `/nav/${encodeURIComponent(buildingId)}`;

function query(params: Record<string, string | number | boolean | undefined | null>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== '') q.set(k, String(v));
  }
  const s = q.toString();
  return s ? `?${s}` : '';
}

export const datasetFileUrl = (dataset: string, file: string) =>
  `${API_BASE_URL}/api/datasets/${encodeURIComponent(dataset)}/files/${encodeURIComponent(file)}`;

const locationPath = (id: string) => `/locations/${encodeURIComponent(id)}`;

/** A file the pipeline wrote for a location, e.g. a review preview `3d/previews/plan_floor_0.png`. */
export const locationFileUrl = (id: string, path: string) =>
  `${API_BASE_URL}/api${locationPath(id)}/files/${path.split('/').map(encodeURIComponent).join('/')}`;

/** The pipeline's model; `version` (e.g. the location's updated_at) busts the browser cache. */
export const locationModelUrl = (id: string, version: string) =>
  `${API_BASE_URL}/api${locationPath(id)}/model.glb?v=${encodeURIComponent(version)}`;

export const api = {
  health: () => request<HealthResponse>('/health'),
  chat: (message: string, threadId?: string) =>
    request<ChatResponse>('/chat', {
      method: 'POST',
      body: JSON.stringify({ message, thread_id: threadId }),
    }),
  datasets: () => request<{ datasets: string[] }>('/datasets').then((r) => r.datasets),
  ingest: (dataset: string) =>
    request<IngestionResponse>('/ingestions', { method: 'POST', body: JSON.stringify({ dataset }) }),
  buildings: () => request<BuildingSummary[]>('/buildings'),
  buildingModel: (id: string) => request<BuildingModel>(`/buildings/${encodeURIComponent(id)}/model`),
  datasetFiles: (dataset: string) =>
    request<{ files: DatasetFile[] }>(`/datasets/${encodeURIComponent(dataset)}/files`).then(
      (r) => r.files,
    ),
  upload: (dataset: string, files: File[]) => {
    const body = new FormData();
    for (const f of files) body.append('files', f, f.name);
    return request<UploadResponse>(`/datasets/${encodeURIComponent(dataset)}/files`, {
      method: 'POST',
      body,
    });
  },
  runs: (dataset: string) =>
    request<RunRecord[]>(`/pipeline-runs?dataset=${encodeURIComponent(dataset)}`),
  run: (runId: string) => request<RunRecord>(`/pipeline-runs/${encodeURIComponent(runId)}`),
  startRun: (dataset: string, force = false) =>
    request<RunRecord>('/pipeline-runs', { method: 'POST', body: JSON.stringify({ dataset, force }) }),
  resumeRun: (runId: string, reviewId: string, option: ReviewOptionId, note?: string) =>
    request<RunRecord>(`/pipeline-runs/${encodeURIComponent(runId)}/resume`, {
      method: 'POST',
      body: JSON.stringify({ review_id: reviewId, option, note: note || null }),
    }),
  location: (id: string) => request<LocationRecord>(locationPath(id)),
  locationBuilding: (id: string) => request<BuildingModel>(`${locationPath(id)}/building`),

  // navigation (`/api/nav`)
  navState: (buildingId: string) => request<NavState>(`${navPath(buildingId)}/state`),
  navSearch: (buildingId: string, q: string, options: NavSearchOptions = {}) =>
    request<{ results: NavSearchResult[] }>(
      `${navPath(buildingId)}/search${query({ q, ...options })}`,
    ).then((r) => r.results),
  navNearest: (buildingId: string, body: NearestRequest) =>
    request<NearestResponse>(`${navPath(buildingId)}/nearest`, post(body)),
  navRoute: (buildingId: string, body: RouteRequest) =>
    request<RouteResponse>(`${navPath(buildingId)}/route`, post(body)),
  navPreviewClosure: (buildingId: string, body: PreviewClosureRequest) =>
    request<PreviewClosureResponse>(`${navPath(buildingId)}/preview-closure`, post(body)),
  navScenario: (buildingId: string, body: ScenarioRequest) =>
    request<NavState>(`${navPath(buildingId)}/scenario`, post(body)),
  navOperation: (buildingId: string, operation: string, args: Record<string, unknown>) =>
    request<OperationResult>(
      `${navPath(buildingId)}/operation`,
      post({ operation, arguments: args }),
    ),
  navSpecs: () =>
    request<OperationSpec[] | { operations?: OperationSpec[]; specs?: OperationSpec[] }>(
      '/nav/specs',
    ).then((r) => (Array.isArray(r) ? r : (r.operations ?? r.specs ?? []))),
  navExamples: (split: 'dev' | 'train') =>
    request<NavExample[] | { examples?: NavExample[] }>(`/nav/examples${query({ split })}`).then(
      (r) => (Array.isArray(r) ? r : (r.examples ?? [])),
    ),
  resolveLocation: (body: LocationResolveRequest) =>
    request<LocationResolveResponse>('/location/resolve', post(body)),

  // assistant + voice (`/api/assistant`)
  assistantStatus: () => request<AssistantStatus>('/assistant/status'),
  assistantTurn: (body: TurnRequest) => request<TurnResponse>('/assistant/turn', post(body)),
  assistantConfirm: (sessionId: string, proposalId: string, approved: boolean) =>
    request<ConfirmResponse>(
      '/assistant/confirm',
      post({ session_id: sessionId, proposal_id: proposalId, approved }),
    ),
  assistantReset: (sessionId: string) =>
    request<{ status: string }>('/assistant/reset', post({ session_id: sessionId })),
  assistantTranscribe: (audio: string) =>
    request<TranscribeResponse>('/assistant/transcribe', post({ audio })),
};
