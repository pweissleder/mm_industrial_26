import {
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from 'react';
import {
  ASSET_COLORS,
  floorLabel,
  humanize,
  polygonArea,
  spaceColor,
  type BuildingModel,
  type Selection,
} from '../../lib/building';
import {
  ChevronIcon,
  CloseIcon,
  CodeIcon,
  CubeIcon,
  FitIcon,
  FrontIcon,
  LayersIcon,
  PinIcon,
  SearchIcon,
  TopIcon,
} from './Icons';
import { LAYERS, type CameraAction, type ViewSettings } from './view';

export const GENERATED_SOURCE = '';
/** The ingestion pipeline's model.glb; dataset file names can't start with ':'. */
export const HQ_SOURCE = ':hq';

interface Props {
  datasets: string[];
  dataset: string | null;
  onDataset: (dataset: string) => void;
  loading: boolean;
  model: BuildingModel | null;
  /** The pipeline's building summary, when its result is shown. */
  summary?: string;
  hqAvailable: boolean;
  sources: string[];
  source: string;
  onSource: (source: string) => void;
  settings: ViewSettings;
  onSettings: (settings: ViewSettings) => void;
  selection: Selection | null;
  onSelect: (selection: Selection) => void;
  onCamera: (action: CameraAction) => void;
  onClose: () => void;
  /** Developer mode: the developer controls, shown in their own tab. */
  developer?: ReactNode;
}

type Tab = 'location' | 'view' | 'developer';

export default function Sidebar(props: Props) {
  const [tab, setTab] = useState<Tab>('location');
  const devMode = !!props.developer;
  // Entering developer mode opens its tab; leaving it falls back to the location tab.
  useEffect(() => {
    setTab((t) => (devMode ? 'developer' : t === 'developer' ? 'location' : t));
  }, [devMode]);
  return (
    <aside className="sidebar card" aria-label="Location and view">
      <div className="sidebar__head">
        <div className="segmented" role="tablist">
          <button
            role="tab"
            aria-selected={tab === 'location'}
            className="segmented__item"
            onClick={() => setTab('location')}
          >
            <PinIcon width={16} height={16} /> Location
          </button>
          <button
            role="tab"
            aria-selected={tab === 'view'}
            className="segmented__item"
            onClick={() => setTab('view')}
          >
            <LayersIcon width={16} height={16} /> View
          </button>
          {devMode && (
            <button
              role="tab"
              aria-selected={tab === 'developer'}
              className="segmented__item"
              onClick={() => setTab('developer')}
            >
              <CodeIcon width={16} height={16} /> Developer
            </button>
          )}
        </div>
        <button className="icon-btn sidebar__close" onClick={props.onClose} aria-label="Close panel">
          <CloseIcon />
        </button>
      </div>
      <div className="sidebar__body">
        {tab === 'developer' && devMode ? (
          props.developer
        ) : tab === 'view' ? (
          <ViewTab {...props} />
        ) : (
          <LocationTab {...props} />
        )}
      </div>
    </aside>
  );
}

function LocationTab({
  datasets,
  dataset,
  onDataset,
  loading,
  model,
  summary,
  hqAvailable,
  sources,
  source,
  onSource,
  settings,
  onSettings,
  selection,
  onSelect,
}: Props) {
  const [query, setQuery] = useState('');
  const floors = useMemo(
    () => [...(model?.floors ?? [])].sort((a, b) => b.level - a.level),
    [model],
  );
  const floorOf = useMemo(() => new Map(model?.floors.map((f) => [f.id, f]) ?? []), [model]);
  const spaceById = useMemo(() => new Map(model?.spaces.map((s) => [s.id, s]) ?? []), [model]);

  const results = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!model || !q) return null;
    const match = (...fields: string[]) => fields.some((f) => f.toLowerCase().includes(q));
    return {
      spaces: model.spaces.filter((s) => match(s.name, s.ref, s.room_type)).slice(0, 25),
      assets: model.assets.filter((a) => match(a.name, a.type, a.category)).slice(0, 25),
    };
  }, [model, query]);

  const rooms = useMemo(() => {
    if (!model) return [];
    const focus = settings.floor;
    return model.spaces
      .filter((s) => focus === 'all' || floorOf.get(s.floor_id)?.level === focus)
      .sort((a, b) => a.ref.localeCompare(b.ref, undefined, { numeric: true }));
  }, [model, settings.floor, floorOf]);

  const focusFloor = floors.find((f) => f.level === settings.floor);
  const setFloor = (floor: ViewSettings['floor']) => onSettings({ ...settings, floor });

  return (
    <>
      <section className="section">
        <label className="section__title" htmlFor="building-select">
          Building
        </label>
        <div className="select">
          <select
            id="building-select"
            value={dataset ?? ''}
            onChange={(e) => onDataset(e.target.value)}
            disabled={!datasets.length}
          >
            {!datasets.length && <option value="">No buildings yet — upload one</option>}
            {datasets.map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </select>
        </div>
        {model && (
          <div className="building-meta">
            <strong>{model.building.name}</strong>
            <div className="stats">
              <span>{model.floors.length} floors</span>
              <span>{model.spaces.length} rooms</span>
              <span>{model.assets.length} assets</span>
            </div>
            {summary && <Summary text={summary} />}
          </div>
        )}
        {loading && <p className="muted">Loading building…</p>}
        {(sources.length > 0 || hqAvailable) && (
          <>
            <span className="section__title" id="source-label">
              3D source
            </span>
            <div className="segmented segmented--small" role="group" aria-labelledby="source-label">
              <button
                className="segmented__item"
                aria-pressed={source === HQ_SOURCE}
                disabled={!hqAvailable}
                title={hqAvailable ? 'Model from the ingestion pipeline' : 'Not processed yet'}
                onClick={() => onSource(HQ_SOURCE)}
              >
                High quality
              </button>
              <button
                className="segmented__item"
                aria-pressed={source === GENERATED_SOURCE}
                title="Quick preview generated in the browser"
                onClick={() => onSource(GENERATED_SOURCE)}
              >
                Preview
              </button>
            </div>
            {sources.length > 0 && (
              <div className="floor-picker" role="group" aria-label="Uploaded models">
                {sources.map((s) => (
                  <button
                    key={s}
                    className="chip"
                    aria-pressed={source === s}
                    onClick={() => onSource(s)}
                  >
                    {s}
                  </button>
                ))}
              </div>
            )}
          </>
        )}
      </section>

      {floors.length > 0 && (
        <section className="section">
          <span className="section__title">Floor</span>
          <div className="floor-picker" role="radiogroup" aria-label="Floor">
            <button
              role="radio"
              aria-checked={settings.floor === 'all'}
              className="chip"
              onClick={() => setFloor('all')}
            >
              All
            </button>
            {floors.map((f) => (
              <button
                key={f.id}
                role="radio"
                aria-checked={settings.floor === f.level}
                className="chip"
                title={f.name}
                onClick={() => setFloor(f.level)}
              >
                {floorLabel(f)}
              </button>
            ))}
          </div>
        </section>
      )}

      {model && (
        <section className="section section--grow">
          <div className="search">
            <SearchIcon />
            <input
              type="search"
              placeholder="Search rooms, machines, assets…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              aria-label="Search locations"
            />
          </div>

          {results ? (
            <div className="results">
              <ResultGroup title="Rooms" empty={!results.spaces.length}>
                {results.spaces.map((s) => (
                  <LocationRow
                    key={s.id}
                    active={selection?.kind === 'space' && selection.id === s.id}
                    swatch={spaceColor(s)}
                    title={s.name}
                    meta={`${humanize(s.room_type)} · ${floorOf.get(s.floor_id)?.name ?? ''}`}
                    onClick={() => onSelect({ kind: 'space', id: s.id })}
                  />
                ))}
              </ResultGroup>
              <ResultGroup title="Assets" empty={!results.assets.length}>
                {results.assets.map((a) => (
                  <LocationRow
                    key={a.id}
                    active={selection?.kind === 'asset' && selection.id === a.id}
                    swatch={ASSET_COLORS[a.category] ?? '#7b7f93'}
                    round
                    title={a.name}
                    meta={`${humanize(a.type)} · ${spaceById.get(a.space_id)?.name ?? ''}`}
                    onClick={() => onSelect({ kind: 'asset', id: a.id })}
                  />
                ))}
              </ResultGroup>
            </div>
          ) : (
            <div className="results">
              <ResultGroup
                title={focusFloor ? `Rooms · ${focusFloor.name}` : 'All rooms'}
                empty={!rooms.length}
              >
                {rooms.map((s) => (
                  <LocationRow
                    key={s.id}
                    active={selection?.kind === 'space' && selection.id === s.id}
                    swatch={spaceColor(s)}
                    title={s.name}
                    meta={`${s.ref} · ${Math.abs(polygonArea(s.polygon)).toFixed(0)} m²`}
                    onClick={() => onSelect({ kind: 'space', id: s.id })}
                  />
                ))}
              </ResultGroup>
            </div>
          )}
        </section>
      )}
    </>
  );
}

/** Clamped to a few lines; when that cuts the text off, a click shows all of it. */
function Summary({ text }: { text: string }) {
  const ref = useRef<HTMLParagraphElement>(null);
  const [expanded, setExpanded] = useState(false);
  const [clipped, setClipped] = useState(false);

  useLayoutEffect(() => {
    setExpanded(false);
    const el = ref.current;
    if (!el) return;
    const measure = () => setClipped(el.scrollHeight > el.clientHeight + 1);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, [text]);

  const toggle = () => setExpanded((e) => !e);
  return (
    <>
      <p
        ref={ref}
        className={`building-meta__summary${expanded ? ' building-meta__summary--open' : ''}${
          clipped || expanded ? ' building-meta__summary--clickable' : ''
        }`}
        onClick={clipped || expanded ? toggle : undefined}
      >
        {text}
      </p>
      {(clipped || expanded) && (
        <button type="button" className="building-meta__more" aria-expanded={expanded} onClick={toggle}>
          {expanded ? 'Show less' : 'Show more'}
        </button>
      )}
    </>
  );
}

function ResultGroup({
  title,
  empty,
  children,
}: {
  title: string;
  empty: boolean;
  children: ReactNode;
}) {
  return (
    <div className="result-group">
      <span className="section__title">{title}</span>
      {empty ? <p className="muted">Nothing found</p> : <ul className="list">{children}</ul>}
    </div>
  );
}

function LocationRow(props: {
  title: string;
  meta: string;
  swatch: string;
  round?: boolean;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <li>
      <button className="list__row" aria-current={props.active || undefined} onClick={props.onClick}>
        <span
          className={`swatch${props.round ? ' swatch--round' : ''}`}
          style={{ background: props.swatch }}
        />
        <span className="list__text">
          <span className="list__title">{props.title}</span>
          <span className="list__meta">{props.meta}</span>
        </span>
      </button>
    </li>
  );
}

function ViewTab({ settings, onSettings, onCamera }: Props) {
  const set = <K extends keyof ViewSettings>(key: K, value: ViewSettings[K]) =>
    onSettings({ ...settings, [key]: value });

  return (
    <>
      <section className="section">
        <span className="section__title">Camera</span>
        <div className="camera-grid">
          <button className="tile" onClick={() => onCamera('iso')}>
            <CubeIcon /> 3D
          </button>
          <button className="tile" onClick={() => onCamera('top')}>
            <TopIcon /> Top
          </button>
          <button className="tile" onClick={() => onCamera('front')}>
            <FrontIcon /> Front
          </button>
          <button className="tile" onClick={() => onCamera('fit')}>
            <FitIcon /> Fit
          </button>
        </div>
      </section>

      <CollapsibleSection title="Layers">
        <div className="toggle-list">
          {LAYERS.map((l) => (
            <Switch
              key={l.id}
              label={l.label}
              checked={settings.layers[l.id]}
              onChange={(v) => set('layers', { ...settings.layers, [l.id]: v })}
            />
          ))}
        </div>
      </CollapsibleSection>

      <CollapsibleSection title="Detail settings">
        <Slider
          label="Explode floors"
          value={settings.explode}
          min={0}
          max={1}
          step={0.05}
          format={(v) => `${Math.round(v * 100)} %`}
          onChange={(v) => set('explode', v)}
        />
        <Slider
          label="Wall height"
          value={settings.wallHeight}
          min={0.2}
          max={8}
          step={0.1}
          format={(v) => `${v.toFixed(1)} m`}
          onChange={(v) => set('wallHeight', v)}
        />
        <div className="field">
          <span className="field__label">Room colours</span>
          <div className="segmented segmented--small">
            <button
              className="segmented__item"
              aria-pressed={settings.colorBy === 'room'}
              onClick={() => set('colorBy', 'room')}
            >
              By type
            </button>
            <button
              className="segmented__item"
              aria-pressed={settings.colorBy === 'neutral'}
              onClick={() => set('colorBy', 'neutral')}
            >
              Neutral
            </button>
          </div>
        </div>
        <div className="toggle-list">
          <Switch label="Shadows" checked={settings.shadows} onChange={(v) => set('shadows', v)} />
          <Switch label="Ground grid" checked={settings.grid} onChange={(v) => set('grid', v)} />
        </div>
      </CollapsibleSection>

      <section className="section">
        <span className="section__title">Legend</span>
        <ul className="legend">
          {Object.entries(ASSET_COLORS).map(([category, color]) => (
            <li key={category}>
              <span className="swatch swatch--round" style={{ background: color }} />
              {humanize(category)}
            </li>
          ))}
          <li>
            <span className="swatch swatch--round" style={{ background: '#fb7928' }} />
            Stairs & lifts
          </li>
        </ul>
      </section>
    </>
  );
}

/** A settings section that starts folded; the header toggles it. */
function CollapsibleSection({ title, children }: { title: string; children: ReactNode }) {
  return (
    <details className="section section--collapsible">
      <summary className="section__title section__toggle">
        {title}
        <ChevronIcon width={16} height={16} className="section__chevron" aria-hidden="true" />
      </summary>
      <div className="section__content">{children}</div>
    </details>
  );
}

export function Switch(props: { label: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="switch">
      <span>{props.label}</span>
      <input
        type="checkbox"
        role="switch"
        checked={props.checked}
        onChange={(e) => props.onChange(e.target.checked)}
      />
      <span className="switch__track" aria-hidden="true" />
    </label>
  );
}

export function Slider(props: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  format: (v: number) => string;
  onChange: (v: number) => void;
}) {
  const pct = ((props.value - props.min) / (props.max - props.min)) * 100;
  return (
    <label className="field">
      <span className="field__label">
        {props.label}
        <span className="field__value">{props.format(props.value)}</span>
      </span>
      <input
        type="range"
        className="range"
        min={props.min}
        max={props.max}
        step={props.step}
        value={props.value}
        style={{ '--pct': `${pct}%` } as CSSProperties}
        onChange={(e) => props.onChange(Number(e.target.value))}
      />
    </label>
  );
}
