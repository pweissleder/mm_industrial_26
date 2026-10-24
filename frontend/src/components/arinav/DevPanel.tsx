// Developer controls: manual routing, scenario changes, benchmark examples and the last
// assistant turn's tool calls. Everything goes through the same /api/nav service.
import { useEffect, useMemo, useState, type ReactNode } from 'react';
import {
  api,
  type ClearanceProfile,
  type NavExample,
  type NavSearchResult,
  type SearchKind,
} from '../../lib/api';
import type { BuildingModel, Selection } from '../../lib/building';
import {
  elementLabel,
  endpointFromSearch,
  endpointFromSelection,
  endpointLabel,
  formatMetres,
  selectionForId,
  type EndpointChoice,
} from '../../lib/nav';
import { RouteBadge } from './ChatReply';
import { CloseIcon, PinIcon, SearchIcon } from './Icons';
import { Slider, Switch } from './Sidebar';
import type { Navigation, Which } from './useNavigation';

interface Props {
  buildingId: string | null;
  model: BuildingModel | null;
  nav: Navigation;
  selection: Selection | null;
  picking: Which | null;
  onPick: (which: Which | null) => void;
  exactPoint: boolean;
  onExactPoint: (value: boolean) => void;
  onSelect: (selection: Selection) => void;
  /** Start the HQ pipeline on its own for unprocessed buildings (costs Claude usage). */
  autoHq: boolean;
  onAutoHq: (value: boolean) => void;
}

const SEARCH_KINDS: { id: SearchKind | ''; label: string }[] = [
  { id: '', label: 'All' },
  { id: 'room', label: 'Rooms' },
  { id: 'asset', label: 'Assets' },
  { id: 'opening', label: 'Doors' },
  { id: 'exit', label: 'Exits' },
];

const json = (v: unknown) => JSON.stringify(v, null, 2);

export default function DevPanel(props: Props) {
  const { nav, model, buildingId } = props;
  if (!buildingId) return <p className="muted">Open a building to use the developer tools.</p>;
  return (
    <>
      {nav.unavailable && (
        <p className="form-error" role="alert">
          Navigation API unavailable: {nav.unavailable}
        </p>
      )}
      <EndpointsSection {...props} />
      <OptionsSection nav={nav} exactPoint={props.exactPoint} onExactPoint={props.onExactPoint} />
      <section className="section">
        <div className="dev-actions">
          <button
            className="btn btn--primary btn--sm"
            disabled={nav.busy}
            onClick={() => void nav.computeRoute()}
          >
            Compute route
          </button>
          <button className="btn btn--ghost btn--sm" disabled={nav.busy} onClick={() => void nav.nearestExit()}>
            Nearest exit
          </button>
          <button className="btn btn--ghost btn--sm" disabled={!nav.routeView && !nav.routeFailure} onClick={nav.clearRoute}>
            Clear
          </button>
        </div>
        {nav.message && (
          <p className="muted" role="status">
            {nav.message}
          </p>
        )}
      </section>
      <RouteFacts nav={nav} model={model} onSelect={props.onSelect} />
      <ScenarioSection nav={nav} model={model} selection={props.selection} />
      <ExamplesSection nav={nav} buildingId={buildingId} />
      <section className="section">
        <span className="section__title">HQ model</span>
        <div className="toggle-list">
          <Switch label="Auto-build HQ for new buildings" checked={props.autoHq} onChange={props.onAutoHq} />
        </div>
        <p className="field__hint">
          {props.autoHq
            ? 'Opening or uploading an unprocessed building starts the ingestion pipeline. With an API key on the server it calls Claude (about $0.30 to $1.50 per building).'
            : 'Off: new buildings stay in the free browser preview (LQ). Start a build from the “LQ only” chip in the top bar. Buildings already processed still load their HQ model.'}
        </p>
      </section>
      <section className="section">
        <span className="section__title">Last assistant turn</span>
        {nav.lastTurn ? (
          <details className="tool-calls">
            <summary>Tool calls ({nav.lastTurn.tools?.length ?? 0})</summary>
            <pre>{json(nav.lastTurn.tools ?? [])}</pre>
          </details>
        ) : (
          <p className="muted">No assistant turn yet.</p>
        )}
      </section>
    </>
  );
}

function EndpointRow(props: {
  which: Which;
  choice: EndpointChoice | null;
  model: BuildingModel | null;
  picking: boolean;
  onPick: () => void;
  onUseSelection: (() => void) | null;
  onClear: () => void;
}) {
  const title = props.which === 'start' ? 'Start' : 'Target';
  return (
    <div className="endpoint">
      <span className="endpoint__title">{title}</span>
      <span className="endpoint__value">
        {props.choice ? endpointLabel(props.choice.endpoint, props.model) : 'Not set'}
        {props.choice?.source === 'assistant' && <span className="tag">from assistant</span>}
      </span>
      <div className="endpoint__actions">
        <button
          className="chip"
          aria-pressed={props.picking}
          onClick={props.onPick}
          title="Click a room, asset or door in the 3D view"
        >
          <PinIcon width={14} height={14} /> {props.picking ? 'Picking…' : 'Pick on map'}
        </button>
        {props.onUseSelection && (
          <button className="chip" onClick={props.onUseSelection}>
            Use selection
          </button>
        )}
        {props.choice && (
          <button className="icon-btn icon-btn--sm" onClick={props.onClear} aria-label={`Clear ${title.toLowerCase()}`}>
            <CloseIcon width={14} height={14} />
          </button>
        )}
      </div>
    </div>
  );
}

function EndpointsSection({ nav, model, buildingId, selection, picking, onPick }: Props) {
  const [q, setQ] = useState('');
  const [kind, setKind] = useState<SearchKind | ''>('');
  const [results, setResults] = useState<NavSearchResult[]>([]);
  const [searchError, setSearchError] = useState<string | null>(null);

  useEffect(() => {
    const text = q.trim();
    if (!buildingId || !text) {
      setResults([]);
      setSearchError(null);
      return;
    }
    let live = true;
    const timer = window.setTimeout(() => {
      api
        .navSearch(buildingId, text, { ...(kind && { kind }), limit: 20 })
        .then((r) => {
          if (!live) return;
          setResults(r);
          setSearchError(null);
        })
        .catch(() => live && setSearchError('Search is not available.'));
    }, 250);
    return () => {
      live = false;
      window.clearTimeout(timer);
    };
  }, [buildingId, q, kind]);

  const fromSelection = selection ? endpointFromSelection(selection) : null;
  const row = (which: Which, choice: EndpointChoice | null) => (
    <EndpointRow
      which={which}
      choice={choice}
      model={model}
      picking={picking === which}
      onPick={() => onPick(picking === which ? null : which)}
      onUseSelection={fromSelection ? () => nav.setEndpoint(which, fromSelection) : null}
      onClear={() => nav.setEndpoint(which, null)}
    />
  );

  return (
    <section className="section">
      <span className="section__title">Route endpoints</span>
      {row('start', nav.start)}
      {row('target', nav.target)}
      <div className="search">
        <SearchIcon />
        <input
          type="search"
          placeholder="Search rooms, assets, doors, exits…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          aria-label="Search endpoints"
        />
      </div>
      <div className="segmented segmented--small" role="group" aria-label="Search kind">
        {SEARCH_KINDS.map((k) => (
          <button
            key={k.id || 'all'}
            className="segmented__item"
            aria-pressed={kind === k.id}
            onClick={() => setKind(k.id)}
          >
            {k.label}
          </button>
        ))}
      </div>
      {searchError && <p className="muted">{searchError}</p>}
      {results.length > 0 && (
        <ul className="list dev-results">
          {results.map((r) => {
            const endpoint = endpointFromSearch(r);
            return (
              <li key={`${r.kind}:${r.id}`} className="dev-result">
                <span className="list__text">
                  <span className="list__title">
                    {r.name}
                    {r.closed && <span className="tag tag--bad">closed</span>}
                  </span>
                  <span className="list__meta">
                    {r.is_exit ? 'exit' : r.kind} · {r.type} · L{r.floor_level}
                    {r.space_name ? ` · ${r.space_name}` : ''}
                  </span>
                </span>
                {endpoint && (
                  <span className="dev-result__actions">
                    <button className="chip" onClick={() => nav.setEndpoint('start', endpoint)}>
                      Start
                    </button>
                    <button className="chip" onClick={() => nav.setEndpoint('target', endpoint)}>
                      Target
                    </button>
                  </span>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

function OptionsSection({
  nav,
  exactPoint,
  onExactPoint,
}: {
  nav: Navigation;
  exactPoint: boolean;
  onExactPoint: (v: boolean) => void;
}) {
  const s = nav.settings;
  const support = nav.state?.support;
  const precisionOff = support ? !support.precision : false;
  return (
    <section className="section">
      <span className="section__title">Routing options</span>
      <div className="segmented segmented--small" role="group" aria-label="Route mode">
        <button
          className="segmented__item"
          aria-pressed={s.mode === 'official'}
          onClick={() => nav.updateSettings({ mode: 'official' })}
        >
          Official
        </button>
        <button
          className="segmented__item"
          aria-pressed={s.mode === 'precision'}
          disabled={precisionOff}
          title={precisionOff ? (support?.precision_reason ?? 'Not supported for this building') : undefined}
          onClick={() => nav.updateSettings({ mode: 'precision' })}
        >
          Precision
        </button>
      </div>
      {s.mode === 'precision' && (
        <>
          <label className="field">
            <span className="field__label">Clearance profile</span>
            <span className="select">
              <select
                value={s.profile}
                onChange={(e) => nav.updateSettings({ profile: e.target.value as ClearanceProfile })}
              >
                <option value="stationary">Stationary objects</option>
                <option value="all">All objects</option>
                <option value="none">No objects</option>
              </select>
            </span>
          </label>
          <Slider
            label="Width"
            value={s.width_m}
            min={0.3}
            max={2}
            step={0.05}
            format={(v) => `${v.toFixed(2)} m`}
            onChange={(v) => nav.updateSettings({ width_m: v })}
          />
          <Slider
            label="Safety margin"
            value={s.safety_m}
            min={0}
            max={0.5}
            step={0.01}
            format={(v) => `${v.toFixed(2)} m`}
            onChange={(v) => nav.updateSettings({ safety_m: v })}
          />
          <Slider
            label="Snap distance"
            value={s.snap_m}
            min={0}
            max={2}
            step={0.25}
            format={(v) => `${v.toFixed(2)} m`}
            onChange={(v) => nav.updateSettings({ snap_m: v })}
          />
        </>
      )}
      <div className="toggle-list">
        <Switch label="Step-free" checked={s.step_free} onChange={(v) => nav.updateSettings({ step_free: v })} />
        {s.mode === 'precision' && (
          <Switch
            label="Allow schematic floor change"
            checked={s.allow_schematic_vertical}
            onChange={(v) => nav.updateSettings({ allow_schematic_vertical: v })}
          />
        )}
        <Switch label="Map pick uses exact point" checked={exactPoint} onChange={onExactPoint} />
      </div>
    </section>
  );
}

function Fact({ k, children }: { k: string; children: ReactNode }) {
  return (
    <div className="details__row">
      <dt>{k}</dt>
      <dd>{children}</dd>
    </div>
  );
}

function RouteFacts({
  nav,
  model,
  onSelect,
}: {
  nav: Navigation;
  model: BuildingModel | null;
  onSelect: (s: Selection) => void;
}) {
  const route = nav.route;
  const failure = nav.routeFailure;
  if (!route && !failure && !nav.nearest) return null;
  const diag = failure?.diagnosis;
  return (
    <section className="section" aria-label="Route result">
      <span className="section__title">Route result</span>
      {route && (
        <>
          <RouteBadge route={route} />
          <dl className="details__rows dev-facts">
            <Fact k="Status">{route.status}</Fact>
            <Fact k="Checked">{route.checked}</Fact>
            <Fact k="Length">{formatMetres(route.length_m)}</Fact>
            {route.walk_m != null && <Fact k="Walk">{formatMetres(route.walk_m)}</Fact>}
            {route.vertical_cost_m != null && <Fact k="Vertical cost">{formatMetres(route.vertical_cost_m)}</Fact>}
            <Fact k="Revision">{route.revision}</Fact>
            <Fact k="Waypoints">{route.waypoints?.length ?? 0}</Fact>
            {route.elapsed_ms != null && <Fact k="Elapsed">{route.elapsed_ms.toFixed(1)} ms</Fact>}
          </dl>
          {route.warnings?.length > 0 && (
            <ul className="dev-notes dev-notes--warn">
              {route.warnings.map((w, i) => (
                <li key={i}>{w}</li>
              ))}
            </ul>
          )}
          {(route.assumptions?.length ?? 0) > 0 && (
            <ul className="dev-notes">
              {route.assumptions?.map((a, i) => (
                <li key={i}>{a}</li>
              ))}
            </ul>
          )}
          {route.directions?.length > 0 && (
            <ol className="dev-directions">
              {route.directions.map((d, i) => (
                <li key={i}>{d}</li>
              ))}
            </ol>
          )}
        </>
      )}
      {failure && (
        <div className="dev-failure" role="alert">
          <strong>{failure.message}</strong>
          {diag?.category && <span className="muted">Category: {diag.category}</span>}
          {diag?.clearance_m != null && (
            <span className="muted">Required clearance: {Number(diag.clearance_m).toFixed(3)} m</span>
          )}
          {(diag?.elements?.length ?? 0) > 0 && (
            <ul className="dev-notes">
              {diag?.elements?.map((el) => (
                <li key={el.id}>
                  <button className="link" onClick={() => onSelect(selectionForId(el.id, model))}>
                    {elementLabel(el.id, model)}
                  </button>
                  {el.reason ? ` · ${el.reason}` : ''}
                </li>
              ))}
            </ul>
          )}
          {diag && (
            <details className="tool-calls">
              <summary>Diagnosis</summary>
              <pre>{json(diag)}</pre>
            </details>
          )}
        </div>
      )}
      {nav.nearest && (
        <>
          <span className="section__title">Nearest exits</span>
          {nav.nearest.results.length ? (
            <ol className="dev-notes">
              {nav.nearest.results.map((r, i) => (
                <li key={i}>
                  {r.name} · L{r.floor_level} · {formatMetres(r.walk_cost_m)}
                </li>
              ))}
            </ol>
          ) : (
            <p className="muted">No reachable exit.</p>
          )}
        </>
      )}
    </section>
  );
}

function ScenarioSection({
  nav,
  model,
  selection,
}: {
  nav: Navigation;
  model: BuildingModel | null;
  selection: Selection | null;
}) {
  const state = nav.state;
  const element =
    selection && ['space', 'opening', 'connector'].includes(selection.kind) ? selection.id : null;
  const isClosed = !!element && !!state?.closed.includes(element);
  return (
    <section className="section">
      <span className="section__title">Scenario</span>
      <p className="muted">
        {state
          ? `Revision ${state.revision} · ${state.closed.length} closed`
          : 'Navigation state not loaded.'}
      </p>
      <p className="dev-selected">
        {element ? (
          <>
            Selected: <strong>{elementLabel(element, model)}</strong>
            {isClosed && <span className="tag tag--bad">closed</span>}
          </>
        ) : (
          'Select a room, door or stairs/lift in the 3D view.'
        )}
      </p>
      <div className="dev-actions">
        <button
          className="btn btn--danger btn--sm"
          disabled={!element || isClosed || nav.busy || !state}
          onClick={() => element && void nav.scenario('close', element)}
        >
          Close
        </button>
        <button
          className="btn btn--ghost btn--sm"
          disabled={!element || !isClosed || nav.busy || !state}
          onClick={() => element && void nav.scenario('open', element)}
        >
          Open
        </button>
        <button
          className="btn btn--ghost btn--sm"
          disabled={!element || nav.busy}
          onClick={() => element && void nav.previewClosure([element])}
        >
          Preview closure
        </button>
        <button
          className="btn btn--ghost btn--sm"
          disabled={!state?.closed.length || nav.busy}
          onClick={() => void nav.scenario('reset')}
        >
          Reset all
        </button>
      </div>
      {state && state.closed.length > 0 && (
        <ul className="dev-notes">
          {state.closed.map((id) => (
            <li key={id}>{elementLabel(id, model)}</li>
          ))}
        </ul>
      )}
      {nav.preview && (
        <details className="tool-calls" open>
          <summary>Closure preview (nothing changed)</summary>
          <pre>{json(nav.preview)}</pre>
        </details>
      )}
    </section>
  );
}

function ExamplesSection({ nav, buildingId }: { nav: Navigation; buildingId: string }) {
  const [split, setSplit] = useState<'dev' | 'train'>('dev');
  const [examples, setExamples] = useState<NavExample[] | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState('');
  const [filter, setFilter] = useState('');

  useEffect(() => {
    setExamples(null);
    setSelectedId('');
  }, [split]);

  const load = () => {
    setStatus('Loading…');
    api
      .navExamples(split)
      .then((rows) => {
        setExamples(rows);
        setStatus(rows.length ? null : 'No examples configured on this server.');
      })
      .catch(() => {
        setExamples([]);
        setStatus('Examples are not available.');
      });
  };

  const shown = useMemo(() => {
    const q = filter.trim().toLowerCase();
    const rows = (examples ?? []).filter(
      (e) => !q || `${e.id} ${e.question ?? ''} ${e.kind ?? e.operation ?? ''}`.toLowerCase().includes(q),
    );
    return rows.slice(0, 200);
  }, [examples, filter]);
  const example = examples?.find((e) => e.id === selectedId) ?? null;
  const operation = example ? (example.operation ?? example.kind) : undefined;
  const otherBuilding = example?.building_id && example.building_id !== buildingId;

  return (
    <section className="section">
      <span className="section__title">Train / dev examples</span>
      <div className="dev-actions">
        <div className="segmented segmented--small" role="group" aria-label="Example split">
          {(['dev', 'train'] as const).map((s) => (
            <button key={s} className="segmented__item" aria-pressed={split === s} onClick={() => setSplit(s)}>
              {s}
            </button>
          ))}
        </div>
        <button className="btn btn--ghost btn--sm" onClick={load}>
          {examples ? 'Reload' : 'Load examples'}
        </button>
      </div>
      {status && <p className="muted">{status}</p>}
      {examples && examples.length > 0 && (
        <>
          <input
            className="input"
            type="search"
            placeholder="Filter examples…"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            aria-label="Filter examples"
          />
          <span className="select">
            <select
              value={selectedId}
              onChange={(e) => setSelectedId(e.target.value)}
              aria-label="Example"
            >
              <option value="">Choose an example ({shown.length})</option>
              {shown.map((e) => (
                <option key={e.id} value={e.id}>
                  {e.id} · {e.kind ?? e.operation ?? ''} · {(e.question ?? '').slice(0, 60)}
                </option>
              ))}
            </select>
          </span>
        </>
      )}
      {example && (
        <div className="dev-example">
          {example.question && <p>{example.question}</p>}
          {otherBuilding && (
            <p className="muted">Belongs to building {example.building_id}; open it first.</p>
          )}
          <button
            className="btn btn--primary btn--sm"
            disabled={!operation || !!otherBuilding || nav.busy}
            onClick={() =>
              operation && void nav.runOperation(operation, example.inputs ?? example.arguments ?? {})
            }
          >
            Run operation
          </button>
          {example.answer !== undefined && (
            <details className="tool-calls">
              <summary>Expected answer</summary>
              <pre>{json(example.answer)}</pre>
            </details>
          )}
        </div>
      )}
      {nav.operation && (
        <div className="dev-example">
          <strong>{nav.operation.formatted_answer ?? 'Operation result'}</strong>
          <details className="tool-calls">
            <summary>Result</summary>
            <pre>{json(nav.operation)}</pre>
          </details>
        </div>
      )}
    </section>
  );
}
