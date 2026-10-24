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
};
