import { useEffect, useRef, useState, type DragEvent, type SubmitEvent } from 'react';
import { api, ApiError, type UploadResponse } from '../../lib/api';
import { CloseIcon, FileIcon, UploadIcon } from './Icons';

const ACCEPT = '.json,.glb,.gltf,.png,.jpg,.jpeg,.svg,.csv,.pdf,.txt,.md';
const DATASET_NAME = /^[A-Za-z0-9][A-Za-z0-9_-]*$/;
const NEW = '__new__';

interface Props {
  open: boolean;
  datasets: string[];
  current: string | null;
  onClose: () => void;
  onUploaded: (result: UploadResponse) => void;
}

const formatSize = (bytes: number) =>
  bytes > 1e6 ? `${(bytes / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1e3))} kB`;

export default function UploadDialog({ open, datasets, current, onClose, onUploaded }: Props) {
  const dialog = useRef<HTMLDialogElement>(null);
  const picker = useRef<HTMLInputElement>(null);
  const [target, setTarget] = useState<string>(NEW);
  const [newName, setNewName] = useState('');
  const [files, setFiles] = useState<File[]>([]);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const d = dialog.current;
    if (!d) return;
    if (open && !d.open) {
      setTarget(current ?? NEW);
      setFiles([]);
      setError(null);
      d.showModal();
    } else if (!open && d.open) d.close();
  }, [open, current]);

  const dataset = target === NEW ? newName.trim() : target;
  const nameInvalid = target === NEW && !!newName && !DATASET_NAME.test(newName.trim());
  const canSubmit = !busy && files.length > 0 && !!dataset && DATASET_NAME.test(dataset);

  function addFiles(list: FileList | null) {
    if (!list) return;
    const incoming = [...list];
    setFiles((prev) => [...prev.filter((p) => !incoming.some((f) => f.name === p.name)), ...incoming]);
    // A building.json usually names the dataset it belongs to.
    if (target === NEW && !newName) {
      const json = incoming.find((f) => f.name === 'building.json');
      json
        ?.text()
        .then((t) => (JSON.parse(t) as { building?: { id?: string } }).building?.id)
        .then((id) => id && DATASET_NAME.test(id) && setNewName(id))
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
      onUploaded(await api.upload(dataset, files));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Upload failed. Is the backend running?');
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
            <span className="eyebrow">Add assets</span>
            <h2 id="upload-title">Upload building data</h2>
          </div>
          <button type="button" className="icon-btn" onClick={onClose} aria-label="Close">
            <CloseIcon />
          </button>
        </header>

        <p className="muted">
          Files are stored in the backend under <code>data/datasets/&lt;dataset&gt;/</code>. Include a{' '}
          <code>building.json</code> to make the building navigable, or a <code>.glb</code> model to view
          it in 3D.
        </p>

        <div className="field">
          <label className="field__label" htmlFor="upload-target">
            Dataset
          </label>
          <div className="select">
            <select id="upload-target" value={target} onChange={(e) => setTarget(e.target.value)}>
              <option value={NEW}>New dataset…</option>
              {datasets.map((d) => (
                <option key={d} value={d}>
                  {d}
                </option>
              ))}
            </select>
          </div>
          {target === NEW && (
            <input
              className="input"
              placeholder="e.g. plant-munich-hall-3"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              aria-label="New dataset name"
              aria-invalid={nameInvalid}
            />
          )}
          {nameInvalid && <span className="field__hint field__hint--bad">Letters, digits, - and _ only.</span>}
        </div>

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
          <span className="muted">building.json, .glb, floor plans (.png/.svg), equipment.csv</span>
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
            {busy ? 'Uploading…' : `Upload ${files.length || ''} file${files.length === 1 ? '' : 's'}`}
          </button>
        </footer>
      </form>
    </dialog>
  );
}
