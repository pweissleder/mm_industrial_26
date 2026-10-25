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
import type { ReactNode } from 'react';
import type { SpaceMeta } from '../../lib/api';
import { ChevronIcon, CloseIcon, PinIcon, RouteIcon } from './Icons';
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
  /** Closed in the current navigation scenario. */
  closed?: boolean;
  /** Activate/Deactivate (user and developer profiles). */
  canToggle?: boolean;
  /** Route section: "From here" / "To here" (user and developer profiles). */
  onFromHere?: () => void;
  onToHere?: () => void;
  /** Button texts (user profiles: "I'm here" / "Take me there"). */
  fromLabel?: string;
  toLabel?: string;
  /** A start / destination is already chosen: that button turns white, the open one stays blue. */
  startSet?: boolean;
  targetSet?: boolean;
  /** Pointer/focus over the card, so the user profile can keep it open. */
  onHold?: (held: boolean) => void;
  /** Folded to its title row (user profile, after a few seconds). */
  collapsed?: boolean;
  /** Fold/unfold control; without it the card can't be folded. */
  onToggleCollapsed?: () => void;
  /** Extra controls under the details, e.g. the layout editor. */
  children?: ReactNode;
}

export default function DetailsCard({
  model,
  spaceMeta,
  selection,
  info,
  onClose,
  onAsk,
  closed = false,
  canToggle = false,
  onFromHere,
  onToHere,
  fromLabel = 'From here',
  toLabel = 'To here',
  startSet = false,
  targetSet = false,
  onHold,
  collapsed = false,
  onToggleCollapsed,
  children,
}: Props) {
  const d = describe(model, selection, info, spaceMeta);
  if (!d) return null;
  // Deactivating goes through the agent (its set_closed tool), so the chat explains what changes.
  const closable = selection.kind === 'space' || selection.kind === 'opening' || selection.kind === 'connector';
  const isClosed = !!model?.closed?.includes(selection.id);
  const routable = selection.kind !== 'object' && (onFromHere || onToHere);
  return (
    <section
      className={`details card${collapsed ? ' details--collapsed' : ''}`}
      aria-label="Selection details"
      onMouseEnter={() => onHold?.(true)}
      onMouseLeave={() => onHold?.(false)}
      onFocus={() => onHold?.(true)}
      onBlur={() => onHold?.(false)}
    >
      <header className="details__head">
        <span className="swatch swatch--lg" style={{ background: d.swatch }} />
        <div className="details__titles">
          <span className="eyebrow">{d.kind}</span>
          <h2 className="details__title">{d.title}</h2>
        </div>
        {onToggleCollapsed && (
          <button
            className="icon-btn details__fold"
            onClick={onToggleCollapsed}
            aria-expanded={!collapsed}
            aria-label={collapsed ? 'Show details' : 'Collapse details'}
          >
            <ChevronIcon />
          </button>
        )}
        <button className="icon-btn" onClick={onClose} aria-label="Clear selection">
          <CloseIcon />
        </button>
      </header>
      {/* Always rendered, so folding can slide the body shut instead of cutting it off. */}
      <div className="details__body" inert={collapsed}>
        <div className="details__body-inner">
          {closed && (
            <p className="details__closed" role="status">
              Deactivated in the current scenario
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
          {children}
          {routable && (
            <div className="details__group" role="group" aria-label="Route">
              <span className="details__group-title">Route</span>
              <div className="details__group-buttons">
                {onFromHere && (
                  <button
                    className={`btn btn--sm ${startSet ? 'btn--ghost' : 'btn--primary'}`}
                    onClick={onFromHere}
                  >
                    <PinIcon width={16} height={16} /> {fromLabel}
                  </button>
                )}
                {onToHere && (
                  <button
                    className={`btn btn--sm ${targetSet ? 'btn--ghost' : 'btn--primary'}`}
                    onClick={onToHere}
                  >
                    <RouteIcon width={16} height={16} /> {toLabel}
                  </button>
                )}
              </div>
            </div>
          )}
          {canToggle && closable && (
            <div className="details__actions">
              <button
                className="btn btn--ghost btn--sm"
                onClick={() => onAsk(`${isClosed ? 'Reopen' : 'Close'} ${d.title} (${selection.id}).`)}
              >
                {isClosed ? 'Activate' : 'Deactivate'}
              </button>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
