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
import { ChatIcon, CloseIcon, LayersIcon } from './Icons';
import type { ObjectInfo } from './view';

interface Details {
  kind: string;
  title: string;
  swatch: string;
  rows: [string, string][];
  floor?: Floor;
}

function describe(model: BuildingModel | null, sel: Selection, info?: ObjectInfo): Details | null {
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
    return {
      kind: humanize(s.type),
      title: s.name,
      swatch: spaceColor(s),
      floor,
      rows: [
        ['Type', humanize(s.room_type)],
        ['Reference', s.ref],
        ['Floor', floor?.name ?? '–'],
        ['Area', `${Math.abs(polygonArea(s.polygon)).toFixed(1)} m²`],
        ['Assets', String(assets)],
        ['Openings', String(doors)],
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
  selection: Selection;
  info?: ObjectInfo;
  onClose: () => void;
  onAsk: (question: string) => void;
  onFocusFloor: (level: number) => void;
}

export default function DetailsCard({ model, selection, info, onClose, onAsk, onFocusFloor }: Props) {
  const d = describe(model, selection, info);
  if (!d) return null;
  const floor = d.floor;
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
        {floor && (
          <button className="btn btn--ghost btn--sm" onClick={() => onFocusFloor(floor.level)}>
            <LayersIcon width={16} height={16} /> Show floor
          </button>
        )}
      </div>
    </section>
  );
}
