import { useEffect, useRef, useState, type DragEvent, type SubmitEvent } from 'react';
import { api, ApiError, type UploadResponse } from '../../lib/api';
import { AlertIcon, CheckIcon, CloseIcon, FileIcon, UploadIcon } from './Icons';

const ACCEPT = '.json,.glb,.gltf,.png,.jpg,.jpeg,.svg,.csv,.pdf,.txt,.md';
const DATASET_NAME = /^[A-Za-z0-9][A-Za-z0-9_-]*$/;
const CHECK_DELAY_MS = 300;
const FLOOR_PLAN = /^floor_-?\d+(_plain)?\.(svg|png|jpe?g)$/i;

interface Props {
  open: boolean;
  /** Existing locations, so a taken name shows at once (the backend has the final say). */
  datasets: string[];
  onClose: () => void;
  onUploaded: (result: UploadResponse) => void;
}

type NameState =
  | { state: 'empty' | 'checking' | 'available' | 'unknown' }
  | { state: 'invalid' | 'taken'; reason: string };

const INVALID = 'Use letters, digits, - and _ only, starting with a letter or digit.';

const formatSize = (bytes: number) =>
  bytes > 1e6 ? `${(bytes / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1e3))} kB`;

const isTaken = (name: string, datasets: string[]) =>
  datasets.some((d) => d.toLowerCase() === name.toLowerCase());

/** `base`, or `base-2`, `base-3`, … if that is already a location. */
function freeName(base: string, datasets: string[]): string {
  let name = base;
  for (let i = 2; isTaken(name, datasets); i++) name = `${base}-${i}`;
  return name;
}

export default function UploadDialog({ open, datasets, onClose, onUploaded }: Props) {
  const dialog = useRef<HTMLDialogElement>(null);
  const nameInput = useRef<HTMLInputElement>(null);
  const picker = useRef<HTMLInputElement>(null);
  const [name, setName] = useState('');
  const [check, setCheck] = useState<NameState>({ state: 'empty' });
  const [files, setFiles] = useState<File[]>([]);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const d = dialog.current;
    if (!d) return;
    if (open && !d.open) {
      setName('');
      setFiles([]);
      setError(null);
      d.showModal();
      nameInput.current?.focus();
    } else if (!open && d.open) d.close();
  }, [open]);

  // Validate locally right away; ask the backend once typing pauses.
  const trimmed = name.trim();
  useEffect(() => {
    if (!trimmed) return setCheck({ state: 'empty' });
    if (!DATASET_NAME.test(trimmed)) return setCheck({ state: 'invalid', reason: INVALID });
    if (isTaken(trimmed, datasets)) {
      return setCheck({ state: 'taken', reason: `A location named “${trimmed}” already exists.` });
    }
    setCheck({ state: 'checking' });
    let stale = false;
    const timer = window.setTimeout(() => {
      api
        .datasetAvailability(trimmed)
        .then((r) => {
          if (stale) return;
          setCheck(
            r.available
              ? { state: 'available' }
              : { state: 'taken', reason: r.reason ?? 'Name not available.' },
          );
        })
        // Can't check now; the upload itself still refuses a taken name.
        .catch(() => !stale && setCheck({ state: 'unknown' }));
    }, CHECK_DELAY_MS);
    return () => {
      stale = true;
      window.clearTimeout(timer);
    };
  }, [trimmed, datasets]);

  const nameOk = check.state === 'available' || check.state === 'unknown';
  const nothingToBuild =
    files.length > 0 &&
    !files.some((f) => f.name === 'building.json' || FLOOR_PLAN.test(f.name) || /\.glb$/i.test(f.name));
  const canSubmit = !busy && files.length > 0 && nameOk;

  function addFiles(list: FileList | null) {
    if (!list) return;
    const incoming = [...list];
    setFiles((prev) => [...prev.filter((p) => !incoming.some((f) => f.name === p.name)), ...incoming]);
    // A building.json usually names the building; offer it when no name is set yet.
    if (!trimmed) {
      const json = incoming.find((f) => f.name === 'building.json');
      json
        ?.text()
        .then((t) => (JSON.parse(t) as { building?: { id?: string } }).building?.id)
        .then((id) => id && DATASET_NAME.test(id) && setName((n) => n || freeName(id, datasets)))
        .catch(() => {});
    }
  }

  function handleDrop(e: DragEvent) {
    e.preventDefault();
    setDragging(false);
    addFiles(e.dataTransfer.files);
  }

  async function handleSubmit(e: SubmitEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!canSubmit) return;
    setBusy(true);
    setError(null);
    try {
      onUploaded(await api.upload(trimmed, files));
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setCheck({ state: 'taken', reason: err.message });
        nameInput.current?.focus();
      } else {
        setError(err instanceof ApiError ? err.message : 'Upload failed. Is the backend running?');
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <dialog
      ref={dialog}
      className="dialog"
      onClose={onClose}
      onClick={(e) => e.target === dialog.current && onClose()}
      aria-labelledby="upload-title"
    >
      <form className="dialog__body" onSubmit={handleSubmit}>
        <header className="dialog__head">
          <div>
            <span className="eyebrow">Upload assets</span>
            <h2 id="upload-title">New location</h2>
          </div>
          <button type="button" className="icon-btn" onClick={onClose} aria-label="Close">
            <CloseIcon />
          </button>
        </header>

        <p className="muted">
          Every upload creates a new location. Include a <code>building.json</code>, or floor plans
          (<code>floor_N.svg</code> works best, .png/.jpg too) with <code>equipment.csv</code> — the
          pipeline reconstructs the building from them. A <code>.glb</code> model can be viewed in 3D on
          its own.
        </p>

        <div className="step">
          <label className="step__head" htmlFor="location-name">
            <span className="step__num">1</span> Name the location
          </label>
          <input
            ref={nameInput}
            id="location-name"
            className="input"
            placeholder="e.g. plant-munich-hall-3"
            value={name}
            onChange={(e) => setName(e.target.value)}
            autoComplete="off"
            spellCheck={false}
            aria-invalid={check.state === 'invalid' || check.state === 'taken'}
            aria-describedby="location-name-status"
          />
          <NameStatus check={check} />
        </div>

        <div className="step">
          <span className="step__head">
            <span className="step__num">2</span> Add its files
          </span>
          <div
            className={`dropzone${dragging ? ' dropzone--active' : ''}`}
            onDragOver={(e) => {
              e.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={handleDrop}
          >
            <span className="dropzone__icon">
              <UploadIcon width={22} height={22} />
            </span>
            <p>
              <strong>Drop files here</strong> or{' '}
              <button type="button" className="link" onClick={() => picker.current?.click()}>
                browse
              </button>
            </p>
            <span className="muted">building.json, .glb, floor_N.svg/.png plans, equipment.csv</span>
            <input
              ref={picker}
              type="file"
              multiple
              accept={ACCEPT}
              hidden
              onChange={(e) => {
                addFiles(e.target.files);
                e.target.value = '';
              }}
            />
          </div>

          {files.length > 0 && (
            <ul className="file-list">
              {files.map((f) => (
                <li key={f.name}>
                  <FileIcon />
                  <span className="file-list__name">{f.name}</span>
                  <span className="muted">{formatSize(f.size)}</span>
                  <button
                    type="button"
                    className="icon-btn icon-btn--sm"
                    onClick={() => setFiles((prev) => prev.filter((p) => p !== f))}
                    aria-label={`Remove ${f.name}`}
                  >
                    <CloseIcon width={14} height={14} />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        {nothingToBuild && (
          <span className="name-status name-status--bad">
            <AlertIcon width={14} height={14} />
            Nothing to build from: add building.json, floor_N.svg/.png plans, or a .glb.
          </span>
        )}

        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}

        <footer className="dialog__foot">
          <button type="button" className="btn btn--ghost" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn btn--accent" disabled={!canSubmit}>
            {busy ? 'Uploading…' : 'Create location'}
          </button>
        </footer>
      </form>
    </dialog>
  );
}

function NameStatus({ check }: { check: NameState }) {
  const [tone, icon, text] =
    check.state === 'invalid' || check.state === 'taken'
      ? ['bad', <AlertIcon key="i" width={14} height={14} />, check.reason]
      : check.state === 'available'
        ? ['good', <CheckIcon key="i" width={14} height={14} />, 'Name is available.']
        : check.state === 'checking'
          ? ['muted', null, 'Checking name…']
          : check.state === 'unknown'
            ? ['muted', null, 'Could not check the name; it is checked again on upload.']
            : ['muted', null, 'Letters, digits, - and _. It must not be in use yet.'];
  return (
    <span id="location-name-status" className={`name-status name-status--${tone}`} aria-live="polite">
      {icon}
      {text}
    </span>
  );
}
