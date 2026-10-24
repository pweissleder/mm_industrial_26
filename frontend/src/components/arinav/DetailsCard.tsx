import type { ReactNode } from 'react';
import {
  assetColor,
  assetHeight,
  humanize,
  polygonArea,
  spaceColor,
  type BuildingModel,
  type Floor,
  type Selection,
} from '../../lib/building';
import type { SpaceMeta } from '../../lib/api';
import { ChatIcon, CloseIcon, LayersIcon } from './Icons';
import type { ObjectInfo } from './view';

interface Details {
  kind: string;
  title: string;
  swatch: string;
  rows: [string, string][];
  floor?: Floor;
  description?: string;
}

function describe(
  model: BuildingModel | null,
  sel: Selection,
  info?: ObjectInfo,
  spaceMeta?: Record<string, SpaceMeta>,
): Details | null {
  if (sel.kind === 'object') {
    return info ? { kind: '3D object', title: info.title, swatch: '#004fab', rows: info.props } : null;
  }
  if (!model) return null;
  const floorOf = (id: string) => model.floors.find((f) => f.id === id);
  const spaceName = (id: string) => model.spaces.find((s) => s.id === id)?.name ?? id;

  if (sel.kind === 'space') {
    const s = model.spaces.find((x) => x.id === sel.id);
    if (!s) return null;
    const floor = floorOf(s.floor_id);
    const assets = model.assets.filter((a) => a.space_id === s.id).length;
    const doors = model.openings.filter((o) => o.space_ids.includes(s.id)).length;
    const meta = spaceMeta?.[s.id];
    return {
      kind: humanize(s.type),
      title: s.name,
      swatch: spaceColor(s),
      floor,
      description: meta?.description || undefined,
      rows: [
        ['Type', humanize(s.room_type)],
        ['Reference', s.ref],
        ['Floor', floor?.name ?? '–'],
        ['Area', `${Math.abs(polygonArea(s.polygon)).toFixed(1)} m²`],
        ['Assets', String(assets)],
        ['Openings', String(doors)],
        ...(meta ? [['Step-free access', meta.step_free_access ? 'Yes' : 'No'] as [string, string]] : []),
      ],
    };
  }
  if (sel.kind === 'asset') {
    const a = model.assets.find((x) => x.id === sel.id);
    if (!a) return null;
    const floor = floorOf(a.floor_id);
    const fp = a.footprint;
    return {
      kind: humanize(a.category),
      title: a.name,
      swatch: assetColor(a),
      floor,
      rows: [
        ['Type', humanize(a.type)],
        ['Room', spaceName(a.space_id)],
        ['Floor', floor?.name ?? '–'],
        ['Footprint', `${fp.width_m ?? '?'} × ${fp.depth_m ?? '?'} m`],
        ['Height (est.)', `${assetHeight(a).toFixed(1)} m`],
        ['Position', a.position.map((v) => v.toFixed(1)).join(', ')],
      ],
    };
  }
  if (sel.kind === 'opening') {
    const o = model.openings.find((x) => x.id === sel.id);
    if (!o) return null;
    const floor = floorOf(o.floor_id);
    return {
      kind: 'Opening',
      title: `${humanize(o.kind)} ${o.id.split('-').pop()}`,
      swatch: '#55648a',
      floor,
      rows: [
        ['Connects', o.space_ids.map(spaceName).join(' ↔ ')],
        ['Width', `${o.width_m.toFixed(2)} m`],
        ['Step-free', o.step_free ? 'Yes' : 'No'],
        ['Floor', floor?.name ?? '–'],
      ],
    };
  }
  const vc = model.vertical_connectors.find((x) => x.id === sel.id);
  if (!vc) return null;
  return {
    kind: humanize(vc.kind),
    title: vc.name,
    swatch: '#fb7928',
    rows: [
      ['Floors', vc.floor_ids.map((id) => floorOf(id)?.name ?? id).join(', ')],
      ['Step-free', vc.step_free ? 'Yes' : 'No'],
    ],
  };
}

interface Props {
  model: BuildingModel | null;
  /** Room descriptions from the pipeline, when its result is shown. */
  spaceMeta?: Record<string, SpaceMeta>;
  selection: Selection;
  info?: ObjectInfo;
  onClose: () => void;
  onAsk: (question: string) => void;
  onFocusFloor: (level: number) => void;
  /** Closed in the current navigation scenario. */
  closed?: boolean;
  /** Mode-specific actions, e.g. set as simulated location or as route start. */
  extraActions?: ReactNode;
}

export default function DetailsCard({
  model,
  spaceMeta,
  selection,
  info,
  onClose,
  onAsk,
  onFocusFloor,
  closed = false,
  extraActions,
}: Props) {
  const d = describe(model, selection, info, spaceMeta);
  if (!d) return null;
  const floor = d.floor;
  // Closing goes through the agent (its set_closed tool), so the chat explains what changes.
  const closable = selection.kind === 'space' || selection.kind === 'opening' || selection.kind === 'connector';
  const isClosed = !!model?.closed?.includes(selection.id);
  return (
    <section className="details card" aria-label="Selection details">
      <header className="details__head">
        <span className="swatch swatch--lg" style={{ background: d.swatch }} />
        <div className="details__titles">
          <span className="eyebrow">{d.kind}</span>
          <h2 className="details__title">{d.title}</h2>
        </div>
        <button className="icon-btn" onClick={onClose} aria-label="Clear selection">
          <CloseIcon />
        </button>
      </header>
      {closed && (
        <p className="details__closed" role="status">
          Closed in the current scenario
        </p>
      )}
      {d.description && <p className="details__description">{d.description}</p>}
      <dl className="details__rows">
        {d.rows.map(([k, v]) => (
          <div key={k} className="details__row">
            <dt>{k}</dt>
            <dd>{v}</dd>
          </div>
        ))}
      </dl>
      <div className="details__actions">
        <button className="btn btn--primary btn--sm" onClick={() => onAsk(`How do I get to ${d.title}?`)}>
          <ChatIcon width={16} height={16} /> Ask AriNav
        </button>
        {closable && (
          <button
            className="btn btn--ghost btn--sm"
            onClick={() => onAsk(`${isClosed ? 'Reopen' : 'Close'} ${d.title} (${selection.id}).`)}
          >
            {isClosed ? 'Reopen' : 'Close'}
          </button>
        )}
        {floor && (
          <button className="btn btn--ghost btn--sm" onClick={() => onFocusFloor(floor.level)}>
            <LayersIcon width={16} height={16} /> Show floor
          </button>
        )}
        {extraActions}
      </div>
    </section>
  );
}
