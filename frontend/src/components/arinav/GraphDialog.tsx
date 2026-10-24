import { useEffect, useMemo, useRef, useState } from 'react';
import { api, type RoomGraphData } from '../../lib/api';
import { centroid, spaceColor, type BuildingModel, type Selection } from '../../lib/building';
import { CloseIcon } from './Icons';

const PANEL_W = 400;
const GAP = 70;
const OUTSIDE = 'outside';

interface Props {
  open: boolean;
  buildingId: string | null;
  model: BuildingModel | null;
  onClose: () => void;
  onSelect: (selection: Selection) => void;
}

export interface GraphLayout {
  width: number;
  height: number;
  panels: { id: string; name: string; x: number; y: number; w: number; h: number }[];
  pos: Map<string, [number, number]>;
  /** Spaces with no path to "outside" — usually a missing door or connector. */
  cutOff: Set<string>;
}

/** One panel per floor in a grid; nodes sit at their room centroid, "outside" left of the grid. */
export function layoutGraph(model: BuildingModel, graph: RoomGraphData): GraphLayout {
  const points = model.spaces.flatMap((s) => s.polygon);
  const xs = points.map((p) => p[0]);
  const ys = points.map((p) => p[1]);
  const [minX, maxX, minY, maxY] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
  const scale = PANEL_W / Math.max(maxX - minX, 1);
  const panelH = Math.max((maxY - minY) * scale, 1);

  const floors = [...model.floors].sort((a, b) => a.level - b.level);
  const cols = Math.ceil(Math.sqrt(floors.length));
  const left = GAP + 20;
  const panels = floors.map((f, i) => ({
    id: f.id,
    name: f.name,
    x: left + (i % cols) * (PANEL_W + GAP),
    y: 30 + Math.floor(i / cols) * (panelH + GAP),
    w: PANEL_W,
    h: panelH,
  }));
  const panelOf = new Map(panels.map((p) => [p.id, p]));
  const rows = Math.ceil(floors.length / cols);
  const height = 30 + rows * (panelH + GAP) - GAP + 20;

  const pos = new Map<string, [number, number]>([[OUTSIDE, [20, height / 2]]]);
  for (const s of model.spaces) {
    const panel = panelOf.get(s.floor_id);
    if (!panel) continue;
    const [cx, cy] = centroid(s.polygon);
    // Plan y grows north, SVG y grows down.
    pos.set(s.id, [panel.x + (cx - minX) * scale, panel.y + (maxY - cy) * scale]);
  }

  const adjacent = new Map<string, string[]>();
  for (const e of graph.edges) {
    adjacent.set(e.source, [...(adjacent.get(e.source) ?? []), e.target]);
    adjacent.set(e.target, [...(adjacent.get(e.target) ?? []), e.source]);
  }
  const seen = new Set([OUTSIDE]);
  const queue = [OUTSIDE];
  while (queue.length) {
    for (const n of adjacent.get(queue.pop()!) ?? []) {
      if (seen.has(n)) continue;
      seen.add(n);
      queue.push(n);
    }
  }
  const cutOff = new Set(graph.nodes.map((n) => n.id).filter((id) => !seen.has(id)));

  return { width: left + cols * (PANEL_W + GAP) - GAP + 20, height, panels, pos, cutOff };
}

export default function GraphDialog({ open, buildingId, model, onClose, onSelect }: Props) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [graph, setGraph] = useState<RoomGraphData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [hover, setHover] = useState<string | null>(null);

  useEffect(() => {
    const d = dialog.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    else if (!open && d.open) d.close();
  }, [open]);

  useEffect(() => {
    if (!open || !buildingId) return;
    setGraph(null);
    setError(null);
    api
      .buildingGraph(buildingId)
      .then(setGraph)
      .catch((e: Error) => setError(e.message));
  }, [open, buildingId]);

  const layout = useMemo(() => (model && graph ? layoutGraph(model, graph) : null), [model, graph]);
  const spaceById = useMemo(() => new Map(model?.spaces.map((s) => [s.id, s]) ?? []), [model]);
  const nodeById = useMemo(() => new Map(graph?.nodes.map((n) => [n.id, n]) ?? []), [graph]);
  const active = (e: RoomGraphData['edges'][number]) => !hover || e.source === hover || e.target === hover;

  return (
    <dialog
      ref={dialog}
      className="dialog dialog--graph"
      onClose={onClose}
      onClick={(e) => e.target === dialog.current && onClose()}
    >
      <div className="dialog__body">
        <header className="dialog__head">
          <div>
            <span className="eyebrow">Developer</span>
            <h2>Room graph</h2>
          </div>
          <button type="button" className="icon-btn" onClick={onClose} aria-label="Close">
            <CloseIcon />
          </button>
        </header>
        {graph && layout && (
          <div className="stats">
            <span>{graph.nodes.length} nodes</span>
            <span>{graph.edges.length} edges</span>
            <span className={layout.cutOff.size ? 'graph-bad' : undefined}>
              {layout.cutOff.size} cut off from outside
            </span>
            <span className="graph-key graph-key--door">door</span>
            <span className="graph-key graph-key--connector">stairs / lift</span>
            <span className="graph-key graph-key--steps">not step-free</span>
          </div>
        )}
        {error && <p className="form-error">{error}</p>}
        {!graph && !error && <p className="muted">Building graph…</p>}
        {graph && layout && (
          <svg
            className="graph"
            viewBox={`0 0 ${layout.width} ${layout.height}`}
            role="img"
            aria-label="Room connectivity graph by floor"
          >
            {layout.panels.map((p) => (
              <g key={p.id}>
                <rect x={p.x - 10} y={p.y - 10} width={p.w + 20} height={p.h + 20} rx={10} className="graph__panel" />
                <text x={p.x - 6} y={p.y - 16} className="graph__floor">
                  {p.name}
                </text>
              </g>
            ))}
            {graph.edges.map((e) => {
              const [a, b] = [layout.pos.get(e.source), layout.pos.get(e.target)];
              if (!a || !b) return null;
              const connector = e.floors !== undefined;
              return (
                <line
                  key={`${e.source}-${e.target}-${e.key}`}
                  x1={a[0]}
                  y1={a[1]}
                  x2={b[0]}
                  y2={b[1]}
                  className={`graph__edge${connector ? ' graph__edge--connector' : ''}${e.step_free ? '' : ' graph__edge--steps'}`}
                  opacity={active(e) ? 1 : 0.12}
                >
                  <title>
                    {`${e.kind} ${e.key}\n${nodeById.get(e.source)?.name} ↔ ${nodeById.get(e.target)?.name}` +
                      (e.width_m !== undefined ? `\nwidth ${e.width_m} m` : '') +
                      (e.step_free ? '' : '\nnot step-free')}
                  </title>
                </line>
              );
            })}
            {graph.nodes.map((n) => {
              const p = layout.pos.get(n.id);
              if (!p) return null;
              const space = spaceById.get(n.id);
              const r = n.kind === OUTSIDE ? 9 : Math.min(3 + Math.sqrt(n.area_m2 ?? 0) * 0.35, 12);
              return (
                <g
                  key={n.id}
                  className="graph__node"
                  onMouseEnter={() => setHover(n.id)}
                  onMouseLeave={() => setHover(null)}
                  onClick={() => {
                    if (!space) return;
                    onSelect({ kind: 'space', id: space.id });
                    onClose();
                  }}
                >
                  <circle
                    cx={p[0]}
                    cy={p[1]}
                    r={r}
                    fill={space ? spaceColor(space) : '#002478'}
                    className={layout.cutOff.has(n.id) ? 'graph__dot graph__dot--cut' : 'graph__dot'}
                  />
                  <text x={p[0]} y={p[1] + r + 7} className="graph__label">
                    {space?.ref || n.name}
                  </text>
                  <title>{`${n.name}\n${n.id}${n.area_m2 ? `\n${n.area_m2} m²` : ''}`}</title>
                </g>
              );
            })}
          </svg>
        )}
      </div>
    </dialog>
  );
}
