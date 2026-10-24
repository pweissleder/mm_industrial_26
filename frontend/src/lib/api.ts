import type { BuildingModel } from './building';

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

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  // Let the browser set the multipart boundary for FormData bodies.
  const json = !(init?.body instanceof FormData);
  const response = await fetch(`${API_BASE_URL}/api${path}`, {
    ...init,
    headers: { ...(json && { 'Content-Type': 'application/json' }), ...init?.headers },
  });
  if (!response.ok) {
    const detail = await response
      .json()
      .then((body: { detail?: unknown }) => (typeof body.detail === 'string' ? body.detail : null))
      .catch(() => null);
    throw new ApiError(response.status, detail ?? `Request to ${path} failed with ${response.status}`);
  }
  return (await response.json()) as T;
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
};
