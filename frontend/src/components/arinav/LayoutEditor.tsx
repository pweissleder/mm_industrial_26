// Layout editing controls (developer profile): forms for a selected asset or door, "add item"
// and "add door" for a selected room, and the save/undo group in the top bar. The edits
// themselves live in lib/edit.ts.
import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type SubmitEvent } from 'react';
import { humanize, type Asset, type BuildingModel, type Opening, type Space } from '../../lib/building';
import {
  catalog,
  neighbours,
  OUTSIDE,
  type AssetPatch,
  type CatalogItem,
  type OpeningPatch,
} from '../../lib/edit';
import { CloseIcon, CopyIcon, EditIcon, PlusIcon, RotateIcon, TrashIcon, UndoIcon } from './Icons';
import { NameStatus, useNameCheck } from './UploadDialog';

/** A text/number input that commits on Enter or blur, so typing doesn't rebuild the scene. */
function Field({
  label,
  value,
  onCommit,
  type = 'number',
  step,
  min,
}: {
  label: string;
  value: string;
  onCommit: (value: string) => void;
  type?: 'number' | 'text';
  step?: number;
  min?: number;
}) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  const invalid = type === 'number' && (draft.trim() === '' || !Number.isFinite(Number(draft)));
  const commit = () => {
    if (draft === value) return;
    if (invalid || (type === 'text' && !draft.trim())) return setDraft(value);
    onCommit(draft.trim());
  };
  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') e.currentTarget.blur();
    if (e.key === 'Escape') {
      setDraft(value);
      e.currentTarget.blur();
    }
  };
  return (
    <label className="editor__field">
      <span>{label}</span>
      <input
        className="input input--sm"
        type={type}
        inputMode={type === 'number' ? 'decimal' : undefined}
        step={step}
        min={min}
        value={draft}
        aria-invalid={invalid}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={onKey}
      />
    </label>
  );
}

const fmt = (v: number | undefined, digits = 2) => String(+(v ?? 0).toFixed(digits));

export function AssetEditor({
  asset,
  onChange,
  onRotate,
  onAlign,
  onDuplicate,
  onDelete,
}: {
  asset: Asset;
  onChange: (patch: AssetPatch) => void;
  onRotate: (deltaDeg: number) => void;
  onAlign: () => void;
  onDuplicate: () => void;
  onDelete: () => void;
}) {
  const fp = asset.footprint;
  const num = (v: string) => Number(v);
  return (
    <div className="editor">
      <span className="eyebrow">Edit item</span>
      <Field label="Name" type="text" value={asset.name} onCommit={(name) => onChange({ name })} />
      <div className="editor__grid">
        <Field
          label="X (m)"
          step={0.1}
          value={fmt(asset.position[0])}
          onCommit={(v) => onChange({ position: [num(v), asset.position[1]] })}
        />
        <Field
          label="Y (m)"
          step={0.1}
          value={fmt(asset.position[1])}
          onCommit={(v) => onChange({ position: [asset.position[0], num(v)] })}
        />
        <Field
          label="Width (m)"
          step={0.1}
          min={0.05}
          value={fmt(fp.width_m ?? 0.6)}
          onCommit={(v) => onChange({ footprint: { width_m: Math.max(0.05, num(v)) } })}
        />
        <Field
          label="Depth (m)"
          step={0.1}
          min={0.05}
          value={fmt(fp.depth_m ?? 0.6)}
          onCommit={(v) => onChange({ footprint: { depth_m: Math.max(0.05, num(v)) } })}
        />
        <Field
          label="Rotation (°)"
          step={15}
          value={fmt(fp.rotation_deg ?? 0, 1)}
          onCommit={(v) => onChange({ footprint: { rotation_deg: ((num(v) % 360) + 360) % 360 } })}
        />
        <div className="editor__field">
          <span>Rotate</span>
          <div className="editor__buttons">
            <button className="icon-btn icon-btn--sm" onClick={() => onRotate(-90)} aria-label="Rotate 90° one way" title="Rotate −90° (Shift+R)">
              <RotateIcon width={16} height={16} style={{ transform: 'scaleX(-1)' }} />
            </button>
            <button className="icon-btn icon-btn--sm" onClick={() => onRotate(90)} aria-label="Rotate 90° the other way" title="Rotate +90° (R)">
              <RotateIcon width={16} height={16} />
            </button>
            <button className="btn btn--ghost btn--sm editor__align" onClick={onAlign} title="Line its sides up with the nearest wall">
              Align
            </button>
          </div>
        </div>
      </div>
      <p className="editor__hint">
        Drag the item in the 3D view: it stays square to the walls, walls stop it, and pulling
        well past a wall moves it into the next room. Arrow keys nudge it, Shift for 1 m steps.
      </p>
      <div className="details__actions">
        <button className="btn btn--ghost btn--sm" onClick={onDuplicate}>
          <CopyIcon width={16} height={16} /> Duplicate
        </button>
        <button className="btn btn--danger btn--sm" onClick={onDelete} title="Delete (Del)">
          <TrashIcon width={16} height={16} /> Delete
        </button>
      </div>
    </div>
  );
}

export function AddItem({
  model,
  space,
  onAdd,
}: {
  model: BuildingModel;
  space: Space;
  onAdd: (item: CatalogItem) => void;
}) {
  const items = useMemo(() => catalog(model), [model]);
  const groups = useMemo(() => {
    const byCategory = new Map<string, CatalogItem[]>();
    for (const i of items) byCategory.set(i.category, [...(byCategory.get(i.category) ?? []), i]);
    return [...byCategory];
  }, [items]);
  // Default to something the room already has, else the first item.
  const inRoom = model.assets.find((a) => a.space_id === space.id)?.type;
  const [type, setType] = useState(inRoom ?? items[0]?.type ?? '');
  useEffect(() => setType(inRoom ?? items[0]?.type ?? ''), [space.id]);
  const item = items.find((i) => i.type === type);
  return (
    <div className="editor">
      <span className="eyebrow">Add item to this room</span>
      <div className="editor__add">
        <div className="select">
          <select value={type} onChange={(e) => setType(e.target.value)} aria-label="Item type">
            {groups.map(([category, list]) => (
              <optgroup key={category} label={humanize(category)}>
                {list.map((i) => (
                  <option key={i.type} value={i.type}>
                    {humanize(i.type)} ({i.width_m} × {i.depth_m} m)
                  </option>
                ))}
              </optgroup>
            ))}
          </select>
        </div>
        <button className="btn btn--primary btn--sm" disabled={!item} onClick={() => item && onAdd(item)}>
          <PlusIcon width={16} height={16} /> Add
        </button>
      </div>
      <p className="editor__hint">It's placed in the middle of the room; drag it where it belongs.</p>
    </div>
  );
}

export function OpeningEditor({
  opening,
  onChange,
  onDelete,
}: {
  opening: Opening;
  onChange: (patch: OpeningPatch) => void;
  onDelete: () => void;
}) {
  const entrance = opening.kind === 'entrance';
  return (
    <div className="editor">
      <span className="eyebrow">Edit {entrance ? 'entrance' : 'door'}</span>
      {!entrance && (
        <div className="segmented segmented--small" role="group" aria-label="Kind">
          {(['door', 'opening'] as const).map((kind) => (
            <button
              key={kind}
              className="segmented__item"
              aria-pressed={opening.kind === kind}
              onClick={() => onChange({ kind })}
            >
              {kind === 'door' ? 'Door' : 'Opening (no leaf)'}
            </button>
          ))}
        </div>
      )}
      <div className="editor__grid">
        <Field
          label="Width (m)"
          step={0.1}
          min={0.3}
          value={fmt(opening.width_m)}
          onCommit={(v) => onChange({ width_m: Number(v) })}
        />
        <label className="editor__field editor__check">
          <span>Step-free</span>
          <input
            type="checkbox"
            checked={opening.step_free}
            onChange={(e) => onChange({ step_free: e.target.checked })}
          />
        </label>
      </div>
      <p className="editor__hint">
        Drag it along the walls: it snaps to the nearest wall and connects the spaces on both
        sides. On an outer wall it becomes an entrance.
      </p>
      <div className="details__actions">
        <button className="btn btn--danger btn--sm" onClick={onDelete} title="Delete (Del)">
          <TrashIcon width={16} height={16} /> Delete
        </button>
      </div>
    </div>
  );
}

export function AddDoor({
  model,
  space,
  onAdd,
}: {
  model: BuildingModel;
  space: Space;
  onAdd: (to: string) => void;
}) {
  const options = useMemo(() => neighbours(model, space.id), [model, space.id]);
  const [to, setTo] = useState(options[0]?.id ?? '');
  useEffect(() => setTo(options[0]?.id ?? ''), [space.id, options.length]);
  if (!options.length) return null;
  return (
    <div className="editor">
      <span className="eyebrow">Add a door from this room</span>
      <div className="editor__add">
        <div className="select">
          <select value={to} onChange={(e) => setTo(e.target.value)} aria-label="Door to">
            {options.map((n) => (
              <option key={n.id} value={n.id}>
                {n.id === OUTSIDE ? n.name : `To ${n.name}`}
              </option>
            ))}
          </select>
        </div>
        <button className="btn btn--primary btn--sm" disabled={!to} onClick={() => onAdd(to)}>
          <PlusIcon width={16} height={16} /> Add
        </button>
      </div>
      <p className="editor__hint">It's placed in the middle of the shared wall; drag it along the wall.</p>
    </div>
  );
}

export function LockedNote() {
  return (
    <div className="editor">
      <p className="editor__hint">
        Stairs and lifts can't be edited. Add a door to them from the room next door.
      </p>
    </div>
  );
}

export type SaveTarget = { kind: 'update' } | { kind: 'copy'; name: string };

/** Where to save the layout: over the current location, or as a new one (the original stays). */
export function SaveLayoutDialog({
  open,
  dataset,
  datasets,
  changes,
  saving,
  error,
  onSave,
  onClose,
}: {
  open: boolean;
  dataset: string;
  datasets: string[];
  changes: number;
  saving: boolean;
  error: string | null;
  onSave: (target: SaveTarget) => void;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [kind, setKind] = useState<SaveTarget['kind']>('update');
  const [name, setName] = useState('');
  const trimmed = name.trim();
  const check = useNameCheck(kind === 'copy' ? trimmed : '', datasets);

  useEffect(() => {
    const d = dialog.current;
    if (!d) return;
    if (open && !d.open) {
      setKind('update');
      setName(`${dataset}-edited`);
      d.showModal();
    } else if (!open && d.open) d.close();
  }, [open, dataset]);

  const canSave =
    !saving && (kind === 'update' || check.state === 'available' || check.state === 'unknown');

  function submit(e: SubmitEvent<HTMLFormElement>) {
    e.preventDefault();
    if (canSave) onSave(kind === 'update' ? { kind } : { kind, name: trimmed });
  }

  return (
    <dialog
      ref={dialog}
      className="dialog"
      onClose={onClose}
      onClick={(e) => e.target === dialog.current && !saving && onClose()}
      aria-labelledby="save-layout-title"
    >
      <form className="dialog__body" onSubmit={submit}>
        <header className="dialog__head">
          <div>
            <span className="eyebrow">
              {changes} change{changes === 1 ? '' : 's'}
            </span>
            <h2 id="save-layout-title">Save layout</h2>
          </div>
          <button type="button" className="icon-btn" onClick={onClose} aria-label="Close" disabled={saving}>
            <CloseIcon />
          </button>
        </header>

        <div className="save-choices" role="radiogroup" aria-label="Where to save">
          <label className={`save-choice${kind === 'update' ? ' save-choice--on' : ''}`}>
            <input type="radio" name="save-kind" checked={kind === 'update'} onChange={() => setKind('update')} />
            <span>
              <b>Update “{dataset}”</b>
              <small>
                Replaces its building.json. Routes and the agent use the new layout right away. Doors
                closed in the current scenario open again.
              </small>
            </span>
          </label>
          <label className={`save-choice${kind === 'copy' ? ' save-choice--on' : ''}`}>
            <input type="radio" name="save-kind" checked={kind === 'copy'} onChange={() => setKind('copy')} />
            <span>
              <b>Save as a new location</b>
              <small>The original stays as it is; the copy opens after saving.</small>
            </span>
          </label>
        </div>

        {kind === 'copy' && (
          <div className="step">
            <label className="step__head" htmlFor="save-layout-name">
              Name of the new location
            </label>
            <input
              id="save-layout-name"
              className="input"
              value={name}
              onChange={(e) => setName(e.target.value)}
              autoComplete="off"
              spellCheck={false}
              autoFocus
              aria-invalid={check.state === 'invalid' || check.state === 'taken'}
              aria-describedby="save-layout-name-status"
            />
            <NameStatus check={check} id="save-layout-name-status" />
          </div>
        )}

        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}

        <footer className="dialog__foot">
          <button type="button" className="btn btn--ghost" onClick={onClose} disabled={saving}>
            Cancel
          </button>
          <button type="submit" className="btn btn--accent" disabled={!canSave}>
            {saving ? 'Saving…' : kind === 'update' ? 'Save' : 'Save copy'}
          </button>
        </footer>
      </form>
    </dialog>
  );
}

/** Top bar group: "Edit layout", or while editing the change count with undo/discard/save. */
export function EditTools({
  editing,
  available,
  changes,
  saving,
  onStart,
  onUndo,
  onDiscard,
  onSave,
  onDone,
}: {
  editing: boolean;
  available: boolean;
  changes: number;
  saving: boolean;
  onStart: () => void;
  onUndo: () => void;
  onDiscard: () => void;
  onSave: () => void;
  onDone: () => void;
}) {
  if (!editing) {
    return (
      <button
        className="btn btn--ghost topbar__edit"
        onClick={onStart}
        disabled={!available}
        title="Add, move and delete furniture, machines and doors"
      >
        <EditIcon width={17} height={17} />
        <span className="topbar__upload-label">Edit layout</span>
      </button>
    );
  }
  return (
    <div className="edit-tools" role="group" aria-label="Layout editing">
      <span className="edit-tools__status">
        <EditIcon width={15} height={15} />
        {changes ? `${changes} unsaved change${changes === 1 ? '' : 's'}` : 'Editing layout'}
      </span>
      <button className="icon-btn icon-btn--sm" onClick={onUndo} disabled={!changes || saving} aria-label="Undo" title="Undo (Ctrl+Z)">
        <UndoIcon width={16} height={16} />
      </button>
      {changes > 0 ? (
        <>
          <button className="btn btn--ghost btn--sm" onClick={onDiscard} disabled={saving}>
            Discard
          </button>
          <button className="btn btn--accent btn--sm" onClick={onSave} disabled={saving}>
            {saving ? 'Saving…' : 'Save'}
          </button>
        </>
      ) : (
        <button className="btn btn--primary btn--sm" onClick={onDone}>
          Done
        </button>
      )}
    </div>
  );
}
