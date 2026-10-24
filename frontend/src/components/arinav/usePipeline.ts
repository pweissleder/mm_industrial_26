// Tracks the backend's ingestion pipeline for the open dataset: starts a run when the
// dataset has none (or when asked, e.g. after an upload), polls it, and loads the
// high-quality result (cleaned building + model.glb) once the run has produced one.
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ApiError,
  api,
  locationModelUrl,
  TERMINAL_RUN,
  type LocationRecord,
  type ReviewOptionId,
  type RunRecord,
} from '../../lib/api';
import type { BuildingModel } from '../../lib/building';

const POLL_MS = 1500;
const REVIEW_POLL_MS = 5000;

export interface HqResult {
  location: LocationRecord;
  building: BuildingModel;
  modelUrl: string;
}

export type PipelineState =
  | { phase: 'idle' }
  | { phase: 'checking' }
  /** Never processed and auto-build is off: the browser preview (LQ) stays until started. */
  | { phase: 'not_built' }
  | { phase: 'running'; run: RunRecord }
  | { phase: 'review'; run: RunRecord }
  | { phase: 'ready'; run: RunRecord | null; hq: HqResult }
  | { phase: 'failed'; run: RunRecord | null; message: string };

export interface Pipeline {
  state: PipelineState;
  /** Start a new run for the open dataset (`force` re-runs an unchanged input). */
  start: (force?: boolean) => Promise<void>;
  resume: (option: ReviewOptionId, note?: string) => Promise<void>;
}

const hasModel = (l: LocationRecord) =>
  !!l.artifacts.model && ['done', 'degraded'].includes(l.pillars.assets_3d?.status ?? '');

async function loadHq(location: LocationRecord): Promise<HqResult> {
  const building = await api.locationBuilding(location.id);
  return { location, building, modelUrl: locationModelUrl(location.id, location.updated_at) };
}

/**
 * `request` asks for a run to be started for `dataset` (a new nonce each time); without
 * it, a run is only started automatically when the dataset has never been processed.
 * With `auto: false` nothing starts on its own (a run costs Claude usage when the backend has
 * an API key): unprocessed datasets end in `not_built` until `start()` is called. Datasets
 * that already have a run still load their result.
 */
export function usePipeline(
  dataset: string | null,
  buildingId: string | null,
  request: { dataset: string; nonce: number } | null,
  { auto = true }: { auto?: boolean } = {},
): Pipeline {
  const [state, setState] = useState<PipelineState>({ phase: 'idle' });
  // Bumped on every new dataset/check so stale async work and polls stop.
  const generation = useRef(0);
  const timer = useRef<number | undefined>(undefined);
  // Read when a dataset is opened, so switching auto-build on never starts a run for the
  // building already on screen.
  const autoRef = useRef(auto);
  autoRef.current = auto;

  const settle = useCallback(
    async (gen: number, run: RunRecord | null) => {
      const live = () => gen === generation.current;
      if (run && !TERMINAL_RUN.has(run.status)) {
        setState({ phase: run.status === 'needs_review' ? 'review' : 'running', run });
        timer.current = window.setTimeout(
          () =>
            void api
              .run(run.run_id)
              .then((next) => (live() ? settle(gen, next) : undefined))
              .catch(() => (live() ? settle(gen, run) : undefined)),
          run.status === 'needs_review' ? REVIEW_POLL_MS : POLL_MS,
        );
        return;
      }
      const locationId = run?.location_id ?? buildingId;
      const location = locationId ? await api.location(locationId).catch(() => null) : null;
      if (!live()) return;
      if (location && hasModel(location) && run?.status !== 'aborted') {
        try {
          const hq = await loadHq(location);
          if (live()) setState({ phase: 'ready', run, hq });
        } catch {
          if (live()) setState({ phase: 'failed', run, message: 'Could not load the processed building.' });
        }
        return;
      }
      const message =
        run?.status === 'aborted'
          ? 'The run was aborted, so no high-quality model was published.'
          : run?.error ?? 'The pipeline did not produce a 3D model.';
      setState({ phase: 'failed', run, message });
    },
    [buildingId],
  );

  const startRun = useCallback(
    async (gen: number, name: string, force: boolean) => {
      try {
        const run = await api.startRun(name, force);
        if (gen === generation.current) await settle(gen, run);
      } catch (err) {
        if (gen !== generation.current) return;
        // 409: a run of this dataset is already going; follow that one instead.
        const [active] = await api.runs(name).catch(() => []);
        if (gen !== generation.current) return;
        if (err instanceof ApiError && err.status === 409 && active) await settle(gen, active);
        else setState({ phase: 'failed', run: null, message: 'Could not start the pipeline.' });
      }
    },
    [settle],
  );

  const requestedNonce = request && request.dataset === dataset ? request.nonce : null;

  useEffect(() => {
    const gen = ++generation.current;
    window.clearTimeout(timer.current);
    if (!dataset || !buildingId) {
      setState({ phase: 'idle' });
      return;
    }
    setState({ phase: 'checking' });
    void (async () => {
      if (requestedNonce !== null && autoRef.current) return startRun(gen, dataset, false);
      const runs = await api.runs(dataset).catch(() => null);
      if (gen !== generation.current) return;
      if (runs === null) {
        setState({ phase: 'failed', run: null, message: 'Could not reach the pipeline.' });
      } else if (runs.length === 0) {
        if (autoRef.current) await startRun(gen, dataset, false);
        else setState({ phase: 'not_built' });
      } else {
        await settle(gen, runs[0]);
      }
    })();
    return () => window.clearTimeout(timer.current);
  }, [dataset, buildingId, requestedNonce, settle, startRun]);

  const start = useCallback(
    async (force = false) => {
      if (!dataset) return;
      const gen = ++generation.current;
      window.clearTimeout(timer.current);
      setState({ phase: 'checking' });
      await startRun(gen, dataset, force);
    },
    [dataset, startRun],
  );

  const resume = useCallback(
    async (option: ReviewOptionId, note?: string) => {
      if (state.phase !== 'review') return;
      const { run } = state;
      const review = run.pending_review;
      if (!review) return;
      const gen = ++generation.current;
      window.clearTimeout(timer.current);
      let next: RunRecord;
      try {
        next = await api.resumeRun(run.run_id, review.review_id, option, note);
      } catch (err) {
        if (gen === generation.current) await settle(gen, run); // keep polling the paused run
        throw err;
      }
      if (gen === generation.current) await settle(gen, next);
    },
    [state, settle],
  );

  return { state, start, resume };
}
