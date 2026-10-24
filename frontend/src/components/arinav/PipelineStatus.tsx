// High-quality build status: a chip for the top bar and a dialog with the stage list,
// the pipeline's review requests (accept / retry / abort with a note) and retry on failure.
import { useEffect, useRef, useState } from 'react';
import { ApiError, locationFileUrl, type ReviewOptionId, type RunRecord } from '../../lib/api';
import { AlertIcon, CheckIcon, CloseIcon, SparkleIcon } from './Icons';
import type { Pipeline, PipelineState } from './usePipeline';

const STAGES: [string, string][] = [
  ['raw_validation', 'Validate and clean data'],
  ['graph_build', 'Build relationship graph'],
  ['graph_validate', 'Check relationship graph'],
  ['floorplan', 'Floor plan geometry'],
  ['equipment', 'Equipment models'],
  ['variant_creator', 'Design missing models'],
  ['model_validate', 'Check 3D model'],
  ['summarize', 'Describe building'],
];
const stageLabel = (stage: string | null | undefined) =>
  STAGES.find(([id]) => id === stage)?.[1] ?? stage ?? '';
const isImage = (path: string) => /\.(png|jpe?g|svg)$/i.test(path);
/** How long the "HQ ready" chip stays before it gets out of the way. */
export const READY_CHIP_MS = 10_000;

function progress(run: RunRecord): string {
  const done = STAGES.filter(([id]) => ['passed', 'skipped'].includes(run.stages[id]?.status)).length;
  return `${Math.min(done + 1, STAGES.length)}/${STAGES.length}`;
}

export function PipelineChip({ state, onOpen }: { state: PipelineState; onOpen: () => void }) {
  // Once ready, the HQ badge in the top bar says it all; the chip only adds clutter.
  const ready = state.phase === 'ready';
  const [settled, setSettled] = useState(false);
  useEffect(() => {
    setSettled(false);
    if (!ready) return;
    const timer = window.setTimeout(() => setSettled(true), READY_CHIP_MS);
    return () => window.clearTimeout(timer);
  }, [ready]);
  if (state.phase === 'idle' || (ready && settled)) return null;
  const [tone, label, title] =
    state.phase === 'not_built'
      ? [
          'off',
          'LQ only · Build HQ',
          'No high-quality model yet. Auto-build is off (Developer mode); open to build it now.',
        ]
      : state.phase === 'checking'
      ? ['busy', 'Checking HQ model…', 'Looking for a processed version of this building']
      : state.phase === 'running'
        ? [
            'busy',
            `Building HQ · ${progress(state.run)}`,
            `High-quality model in progress: ${stageLabel(state.run.current_stage) || 'queued'}`,
          ]
        : state.phase === 'review'
          ? ['attention', 'Review needed', state.run.pending_review?.title ?? 'The pipeline needs a decision']
          : state.phase === 'ready'
            ? ['good', 'HQ ready', 'High-quality model available']
            : ['bad', 'HQ failed', state.message];
  return (
    <button
      className={`pipeline-chip pipeline-chip--${tone}`}
      onClick={onOpen}
      title={title}
      aria-label={`${label}. Show processing details`}
    >
      {tone === 'busy' && <span className="spinner" aria-hidden="true" />}
      {tone === 'good' && <CheckIcon width={15} height={15} />}
      {tone === 'off' && <SparkleIcon width={15} height={15} />}
      {(tone === 'attention' || tone === 'bad') && <AlertIcon width={15} height={15} />}
      <span className="pipeline-chip__label">{label}</span>
    </button>
  );
}

interface DialogProps {
  open: boolean;
  pipeline: Pipeline;
  onClose: () => void;
}

export function PipelineDialog({ open, pipeline, onClose }: DialogProps) {
  const dialog = useRef<HTMLDialogElement>(null);
  const { state } = pipeline;
  const run =
    state.phase === 'idle' || state.phase === 'checking' || state.phase === 'not_built'
      ? null
      : state.run;
  const review = state.phase === 'review' ? state.run.pending_review : null;
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const d = dialog.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    else if (!open && d.open) d.close();
  }, [open]);

  useEffect(() => {
    setNote('');
    setError(null);
  }, [review?.review_id]);

  async function act(fn: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'The backend did not respond.');
    } finally {
      setBusy(false);
    }
  }

  const answer = (option: ReviewOptionId) => act(() => pipeline.resume(option, note.trim()));
  const restart = () => act(() => pipeline.start(true));
  const build = () => act(() => pipeline.start(false));

  const heading =
    state.phase === 'not_built'
      ? 'No high-quality model yet'
      : state.phase === 'review'
      ? 'Your decision is needed'
      : state.phase === 'ready'
        ? 'High-quality model ready'
        : state.phase === 'failed'
          ? 'High-quality build failed'
          : 'Building the high-quality model';

  return (
    <dialog
      ref={dialog}
      className="dialog dialog--wide"
      onClose={onClose}
      onClick={(e) => e.target === dialog.current && onClose()}
      aria-labelledby="pipeline-title"
    >
      <div className="dialog__body">
        <header className="dialog__head">
          <div>
            <span className="eyebrow">Processing</span>
            <h2 id="pipeline-title">{heading}</h2>
          </div>
          <button type="button" className="icon-btn" onClick={onClose} aria-label="Close">
            <CloseIcon />
          </button>
        </header>

        {state.phase === 'not_built' && (
          <p className="muted">
            You are looking at the quick preview generated in the browser; routing, search and the
            assistant work on it as usual. Building the high-quality version runs the ingestion
            pipeline, which calls Claude (Opus, Sonnet, Haiku; roughly $0.30 to $1.50 per building)
            when the server has an API key. Automatic builds are off; change that in Developer mode.
          </p>
        )}

        {state.phase !== 'ready' && state.phase !== 'not_built' && (
          <p className="muted">
            Until then you see a quick preview generated in the browser. The pipeline validates and
            cleans the data, builds a to-scale 3D model and describes every room; the view switches
            to it once it is ready.
            {run?.offline && ' No API key is set, so only the deterministic checks run.'}
          </p>
        )}

        {review && (
          <section className="review" aria-label="Review request">
            <div className="review__head">
              <AlertIcon />
              <div>
                <strong>{review.title}</strong>
                <p>{review.message}</p>
              </div>
            </div>
            {review.issues.length > 0 && (
              <ul className="review__issues">
                {review.issues.map((issue, i) => (
                  <li key={i}>{issue}</li>
                ))}
              </ul>
            )}
            {run?.location_id && review.artifacts.some(isImage) && (
              <div className="review__artifacts">
                {review.artifacts.filter(isImage).map((path) => {
                  const url = locationFileUrl(run.location_id!, path);
                  return (
                    <a key={path} href={url} target="_blank" rel="noreferrer" title={path}>
                      <img src={url} alt={path.split('/').pop()} loading="lazy" />
                    </a>
                  );
                })}
              </div>
            )}
            {review.allow_note && (
              <label className="field">
                <span className="field__label">Note for the pipeline (optional)</span>
                <textarea
                  className="input textarea"
                  rows={2}
                  value={note}
                  placeholder="e.g. Room 2.04 really has no door, keep it."
                  onChange={(e) => setNote(e.target.value)}
                />
              </label>
            )}
            <div className="review__options">
              {review.options.map((o) => (
                <button
                  key={o.id}
                  className={`btn btn--sm ${
                    o.id === 'accept_with_warnings'
                      ? 'btn--primary'
                      : o.id === 'abort'
                        ? 'btn--danger'
                        : 'btn--ghost'
                  }`}
                  title={o.description}
                  disabled={busy}
                  onClick={() => void answer(o.id)}
                >
                  {o.label}
                </button>
              ))}
            </div>
          </section>
        )}

        {state.phase === 'failed' && <p className="form-error">{state.message}</p>}
        {state.phase === 'ready' && state.hq.location.metadata?.summary && (
          <p className="pipeline-summary">{state.hq.location.metadata.summary}</p>
        )}

        {run && (
          <ol className="stages" aria-label="Pipeline stages">
            {STAGES.map(([id, label]) => {
              const stage = run.stages[id];
              const status =
                run.current_stage === id && run.status === 'needs_review' ? 'review' : stage?.status;
              return (
                <li key={id} className={`stage stage--${status ?? 'pending'}`}>
                  <span className="stage__dot" aria-hidden="true">
                    {status === 'running' ? <span className="spinner" /> : null}
                    {status === 'passed' ? <CheckIcon width={12} height={12} /> : null}
                  </span>
                  <span className="stage__label">{label}</span>
                  <span className="stage__status">
                    {status === 'review'
                      ? 'needs review'
                      : status === 'pending'
                        ? ''
                        : `${status}${stage && stage.attempts > 1 ? ` · try ${stage.attempts}` : ''}`}
                  </span>
                </li>
              );
            })}
          </ol>
        )}

        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}

        <footer className="dialog__foot">
          {state.phase === 'not_built' && (
            <button className="btn btn--accent" disabled={busy} onClick={() => void build()}>
              Build HQ model
            </button>
          )}
          {(state.phase === 'failed' || state.phase === 'ready') && (
            <button className="btn btn--ghost" disabled={busy} onClick={() => void restart()}>
              {state.phase === 'failed' ? 'Try again' : 'Rebuild'}
            </button>
          )}
          <button className="btn btn--primary" onClick={onClose}>
            {state.phase === 'running' ? 'Keep working' : 'Close'}
          </button>
        </footer>
      </div>
    </dialog>
  );
}
