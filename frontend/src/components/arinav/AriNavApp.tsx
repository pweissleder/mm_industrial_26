import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  api,
  datasetFileUrl,
  type AssistantStatus,
  type ConfirmResponse,
  type HealthResponse,
  type RoomGraphData,
  type TurnResponse,
  type UploadResponse,
} from '../../lib/api';
import type { BuildingModel, Selection } from '../../lib/building';
import {
  elementLabel,
  endpointFromSelection,
  selectionFromFocus,
  type AppMode,
  type SimulatedLocation,
} from '../../lib/nav';
import ChatDock, { type Draft, type PickPurpose } from './ChatDock';
import DetailsCard from './DetailsCard';
import DevPanel from './DevPanel';
import {
  CodeIcon,
  CubeIcon,
  FitIcon,
  MinusIcon,
  OrbitIcon,
  PanelIcon,
  PanIcon,
  PinIcon,
  PlusIcon,
  SparkleIcon,
  TopIcon,
  UploadIcon,
} from './Icons';
import Logo, { LogoMark } from './Logo';
import { PipelineChip, PipelineDialog } from './PipelineStatus';
import type { PickHit } from './scene';
import Sidebar, { GENERATED_SOURCE, HQ_SOURCE } from './Sidebar';
import UploadDialog from './UploadDialog';
import { useNavigation, type Which } from './useNavigation';
import { usePipeline } from './usePipeline';
import Viewer3D, { type CameraCommand } from './Viewer3D';
import {
  DEFAULT_VIEW,
  type CameraAction,
  type NavMode,
  type ObjectInfo,
  type ViewSettings,
} from './view';

const LAST_DATASET = 'arinav:dataset';
const LAST_MODE = 'arinav:mode';
// Off by default: an HQ build calls Claude (paid) when the backend has an API key.
const AUTO_HQ = 'arinav:auto-hq';
const isGlb = (name: string) => /\.(glb|gltf)$/i.test(name);
const WIDE = '(min-width: 1024px)';
const wide = () => window.matchMedia(WIDE).matches;
// Sidebar width plus its gutters, matching --sidebar-w and --gutter in global.css.
const DOCKED_SIDEBAR_PX = 340 + 2 * 16;

/** What the next click in the 3D view sets, instead of only selecting. */
type Pick = { kind: 'endpoint'; which: Which } | { kind: PickPurpose; reply: boolean };

function useWide(): boolean {
  const [value, setValue] = useState(wide);
  useEffect(() => {
    const mq = window.matchMedia(WIDE);
    const update = () => setValue(mq.matches);
    mq.addEventListener('change', update);
    return () => mq.removeEventListener('change', update);
  }, []);
  return value;
}

function remember(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    // Storage can be unavailable (private mode); remembering is only a convenience.
  }
}

function recall(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

export default function AriNavApp() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [backendDown, setBackendDown] = useState(false);
  const [assistant, setAssistant] = useState<AssistantStatus | null>(null);
  const [mode, setMode] = useState<AppMode>(() =>
    recall(LAST_MODE) === 'developer' ? 'developer' : 'assistant',
  );
  const [datasets, setDatasets] = useState<string[]>([]);
  const [dataset, setDataset] = useState<string | null>(null);
  const [model, setModel] = useState<BuildingModel | null>(null);
  const [buildingId, setBuildingId] = useState<string | null>(null);
  const [runRequest, setRunRequest] = useState<{ dataset: string; nonce: number } | null>(null);
  const [autoHq, setAutoHq] = useState(() => recall(AUTO_HQ) === 'true');
  const changeAutoHq = useCallback((value: boolean) => {
    setAutoHq(value);
    remember(AUTO_HQ, String(value));
  }, []);
  const [pipelineOpen, setPipelineOpen] = useState(false);
  // Model URL last switched to automatically, so choosing the preview again sticks.
  const switchedTo = useRef<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [sources, setSources] = useState<string[]>([]);
  const [source, setSource] = useState(GENERATED_SOURCE);
  const [looseGlb, setLooseGlb] = useState<string | null>(null);
  const [settings, setSettings] = useState<ViewSettings>(DEFAULT_VIEW);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [info, setInfo] = useState<ObjectInfo>();
  const [camera, setCamera] = useState<CameraCommand | null>(null);
  const [navMode, setNavMode] = useState<NavMode>('orbit');
  const [sidebarOpen, setSidebarOpen] = useState(wide);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [graph, setGraph] = useState<RoomGraphData | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [pick, setPick] = useState<Pick | null>(null);
  const [exactPoint, setExactPoint] = useState(false);
  const isWide = useWide();
  const nav = useNavigation(buildingId);
  const developer = mode === 'developer';

  const notify = useCallback((text: string) => {
    setToast(text);
    window.setTimeout(() => setToast((t) => (t === text ? null : t)), 4000);
  }, []);

  const { reset: resetNav } = nav;
  const openDataset = useCallback(
    async (name: string, startPipeline = false) => {
      setDataset(name);
      setBuildingId(null);
      switchedTo.current = null;
      setLoading(true);
      setSelection(null);
      setLooseGlb(null);
      setSource(GENERATED_SOURCE);
      setPick(null);
      resetNav();
      try {
        const [ingestion, files] = await Promise.all([
          api.ingest(name),
          api.datasetFiles(name).catch(() => []),
        ]);
        setModel(await api.buildingModel(ingestion.building_id));
        setBuildingId(ingestion.building_id);
        if (startPipeline) setRunRequest({ dataset: name, nonce: Date.now() });
        setSources(files.map((f) => f.name).filter(isGlb));
        setSettings((s) => ({ ...s, floor: 'all' }));
        remember(LAST_DATASET, name);
        return ingestion.building_id;
      } catch {
        setModel(null);
        notify(`Could not load “${name}”.`);
        return null;
      } finally {
        setLoading(false);
      }
    },
    [notify, resetNav],
  );

  const refreshDatasets = useCallback(() => api.datasets().then(setDatasets), []);

  // The browser preview shows at once; the pipeline's high-quality version replaces it
  // when ready (unless an uploaded GLB is being looked at) and stays switchable.
  const pipeline = usePipeline(dataset, buildingId, runRequest, { auto: autoHq });

  // Bumped when the agent edits the building, to refetch the nav graph.
  const [edits, setEdits] = useState(0);
  const reloadModel = useCallback(() => {
    if (!buildingId) return;
    api
      .buildingModel(buildingId)
      .then(setModel)
      .catch(() => notify('Could not reload the building after the change.'));
    setEdits((n) => n + 1);
  }, [buildingId, notify]);

  // One scenario: when its revision moves (assistant Confirm, developer panel, another tab or the
  // team agent), refetch the served model so its per-element `closed` flags match.
  const scenarioRevision = nav.state?.revision;
  const seenRevision = useRef<number | undefined>(undefined);
  useEffect(() => {
    const previous = seenRevision.current;
    seenRevision.current = scenarioRevision;
    if (previous !== undefined && scenarioRevision !== undefined && previous !== scenarioRevision) {
      reloadModel();
    }
  }, [scenarioRevision, reloadModel]);

  const graphOn = settings.layers.graph;
  useEffect(() => {
    setGraph(null);
    if (!graphOn || !buildingId) return;
    let live = true;
    api
      .buildingGraph(buildingId)
      .then((g) => live && setGraph(g))
      .catch(() => live && notify('Could not load the nav graph.'));
    return () => {
      live = false;
    };
  }, [graphOn, buildingId, edits]);
  const { state: run } = pipeline;
  const hq = run.phase === 'ready' ? run.hq : null;
  const wasBuilding = useRef(false);
  useEffect(() => {
    if (run.phase === 'running' || run.phase === 'review') wasBuilding.current = true;
    // The scene rebuilds on a switch, so a selection would point at objects that are gone.
    if (!hq) {
      if (source === HQ_SOURCE) {
        setSource(GENERATED_SOURCE);
        setSelection(null);
      }
      return;
    }
    if (switchedTo.current === hq.modelUrl) return;
    switchedTo.current = hq.modelUrl;
    const switching = source === GENERATED_SOURCE;
    if (switching) {
      setSource(HQ_SOURCE);
      setSelection(null);
    }
    if (wasBuilding.current) {
      notify(
        switching
          ? 'High-quality model ready — now showing it.'
          : 'High-quality model ready — pick it under 3D source.',
      );
    }
    wasBuilding.current = false;
  }, [run.phase, hq, source, notify]);

  // A review request opens the processing dialog once; later it waits behind the chip.
  const shownReview = useRef<string | null>(null);
  const reviewId = run.phase === 'review' ? run.run.pending_review?.review_id : undefined;
  useEffect(() => {
    if (reviewId && shownReview.current !== reviewId) {
      shownReview.current = reviewId;
      setPipelineOpen(true);
    }
  }, [reviewId]);

  const showingHq = source === HQ_SOURCE && !!hq;
  // The HQ model is baked, so only closures (painted red) follow the agent's edits there.
  const hqBuilding = hq?.building;
  const modelClosed = model?.closed;
  const hqModel = useMemo(
    () => hqBuilding && { ...hqBuilding, closed: modelClosed },
    [hqBuilding, modelClosed],
  );
  const viewModel = showingHq && hqModel ? hqModel : model;

  useEffect(() => {
    api
      .health()
      .then(setHealth)
      .catch(() => setBackendDown(true));
    // Without the assistant API the chat falls back to the plain agent (/api/chat).
    api
      .assistantStatus()
      .then(setAssistant)
      .catch(() => setAssistant(null));
    api
      .datasets()
      .then((list) => {
        setDatasets(list);
        const last = recall(LAST_DATASET);
        const first = last && list.includes(last) ? last : list[0];
        if (first) void openDataset(first);
        else setLoading(false);
      })
      .catch(() => setLoading(false));
  }, [openDataset]);

  const changeMode = (next: AppMode) => {
    setMode(next);
    remember(LAST_MODE, next);
    setPick(null);
    if (next === 'developer') setSidebarOpen(true);
  };

  /** Make a room the (simulated) user location; optionally answer the assistant with it. */
  const setLocationFrom = useCallback(
    async (sel: Selection, reply: boolean) => {
      const spaceId =
        sel.kind === 'space'
          ? sel.id
          : sel.kind === 'asset'
            ? viewModel?.assets.find((a) => a.id === sel.id)?.space_id
            : undefined;
      if (!spaceId || !buildingId) {
        notify('Pick a room to set the simulated location.');
        return;
      }
      const label = elementLabel(spaceId, viewModel);
      let location: SimulatedLocation = { endpoint: { kind: 'space', id: spaceId }, label };
      try {
        const resolved = await api.resolveLocation({
          building_id: buildingId,
          source: 'simulated',
          space_id: spaceId,
        });
        if (resolved.status === 'resolved' && resolved.endpoint) {
          location = { endpoint: resolved.endpoint, label };
        }
      } catch {
        // The room itself is a valid start; resolving only refines it.
      }
      nav.setSimulated(location);
      if (reply) setDraft({ text: `I'm in ${label}.`, nonce: Date.now(), send: true });
    },
    [buildingId, viewModel, nav, notify],
  );

  const handleSelect = useCallback(
    (sel: Selection | null, objectInfo?: ObjectInfo, hit?: PickHit) => {
      if (pick && sel) {
        setPick(null);
        if (pick.kind === 'endpoint') {
          const spaceHit = hit && { position: hit.position };
          const endpoint = endpointFromSelection(sel, spaceHit, exactPoint);
          if (endpoint) nav.setEndpoint(pick.which, endpoint);
          else notify('Pick a room, asset or door.');
        } else if (pick.kind === 'location') {
          void setLocationFrom(sel, pick.reply);
        } else {
          const name = elementLabel(sel.id, viewModel);
          setDraft({ text: `I mean ${name}.`, nonce: Date.now(), send: true });
        }
      }
      setSelection(sel);
      setInfo(objectInfo);
    },
    [pick, exactPoint, nav, notify, setLocationFrom, viewModel],
  );

  useEffect(() => {
    if (!pick) return;
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setPick(null);
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [pick]);

  // Bring a floor into focus so the item isn't hidden under the one above.
  const focusFloorOf = useCallback(
    (sel: Selection) => {
      const floorId =
        sel.kind === 'space'
          ? viewModel?.spaces.find((s) => s.id === sel.id)?.floor_id
          : sel.kind === 'asset'
            ? viewModel?.assets.find((a) => a.id === sel.id)?.floor_id
            : sel.kind === 'opening'
              ? viewModel?.openings.find((o) => o.id === sel.id)?.floor_id
              : undefined;
      const level = viewModel?.floors.find((f) => f.id === floorId)?.level;
      const top = Math.max(...(viewModel?.floors.map((f) => f.level) ?? [0]));
      if (level !== undefined && !(settings.floor === 'all' && level === top)) {
        setSettings((s) => ({ ...s, floor: level }));
      }
    },
    [viewModel, settings.floor],
  );

  const selectFromList = (sel: Selection) => {
    focusFloorOf(sel);
    handleSelect(sel);
    if (!isWide) setSidebarOpen(false);
  };

  /** Switch to the building a turn refers to (ids map to datasets via /api/buildings). */
  const switchBuilding = useCallback(
    async (id: string) => {
      const known = await api.buildings().catch(() => []);
      const name = known.find((b) => b.id === id)?.dataset ?? (datasets.includes(id) ? id : null);
      if (!name) {
        notify(`Building ${id} is not available here.`);
        return false;
      }
      return (await openDataset(name)) === id || name === id;
    },
    [datasets, openDataset, notify],
  );

  const applyTurn = useCallback(
    async (turn: TurnResponse) => {
      const ui = turn.ui ?? {};
      if (ui.building_id && ui.building_id !== buildingId) await switchBuilding(ui.building_id);
      nav.applyTurn(turn);
      const route = ui.route;
      if (route?.path?.length) {
        // Show every floor the route uses: a focused floor would hide the ones above it.
        const levels = new Set(
          route.path.map((p) => viewModel?.floors.find((f) => f.id === p.floor_id)?.level),
        );
        if (levels.size > 1 && settings.floor !== 'all') setSettings((s) => ({ ...s, floor: 'all' }));
      }
      if (ui.focus && !route) {
        const sel = selectionFromFocus(ui.focus);
        focusFloorOf(sel);
        setSelection(sel);
        setInfo(undefined);
      }
    },
    [buildingId, switchBuilding, nav, viewModel, settings.floor, focusFloorOf],
  );

  const handleConfirmed = useCallback(
    (result: ConfirmResponse) => {
      if (result.state) nav.applyState(result.state);
      else void nav.refresh();
    },
    [nav],
  );

  const runCamera = (action: CameraAction) => setCamera({ action, nonce: Date.now() });

  async function handleUploaded(result: UploadResponse) {
    setUploadOpen(false);
    const count = result.files.length;
    notify(`Uploaded ${count} file${count === 1 ? '' : 's'} to ${result.dataset}.`);
    await refreshDatasets().catch(() => {});
    const glb = result.files.find((f) => isGlb(f.name));
    if (result.ingestible) {
      await openDataset(result.dataset, true);
      if (glb) setSource(glb.name);
    } else if (glb) {
      // A model without building.json: show it on its own.
      setDataset(null);
      setModel(null);
      setSources([]);
      setSelection(null);
      setLooseGlb(datasetFileUrl(result.dataset, glb.name));
    }
  }

  const glbUrl =
    source && source !== HQ_SOURCE && dataset ? datasetFileUrl(dataset, source) : looseGlb;
  const empty = !loading && !model && !glbUrl;
  const quality = glbUrl
    ? null
    : showingHq
      ? { tone: 'hq', label: 'HQ', title: 'High-quality model from the ingestion pipeline' }
      : model
        ? { tone: 'low', label: 'Low', title: 'Quick preview generated in the browser from building.json' }
        : null;

  const chatSelection = useMemo(
    () =>
      selection && selection.kind !== 'object'
        ? { name: elementLabel(selection.id, viewModel), room: selection.kind === 'space' }
        : null,
    [selection, viewModel],
  );
  const selectedEndpoint = selection ? endpointFromSelection(selection) : null;
  // Scenario closures (the one source); the served model's flags follow them via reloadModel.
  const closed = nav.state?.closed;

  const pickHint = !pick
    ? null
    : pick.kind === 'endpoint'
      ? `Click a room, asset or door to set the ${pick.which}.`
      : pick.kind === 'location'
        ? 'Click a room to set your simulated location.'
        : 'Click the place you mean.';

  const detailActions =
    selection && selection.kind !== 'object' ? (
      developer ? (
        selectedEndpoint && (
          <>
            <button
              className="btn btn--ghost btn--sm"
              onClick={() => nav.setEndpoint('start', selectedEndpoint)}
            >
              Set as start
            </button>
            <button
              className="btn btn--ghost btn--sm"
              onClick={() => nav.setEndpoint('target', selectedEndpoint)}
            >
              Set as target
            </button>
          </>
        )
      ) : assistant && selection.kind === 'space' ? (
        <button
          className="btn btn--ghost btn--sm"
          onClick={() => void setLocationFrom(selection, false)}
        >
          <PinIcon width={16} height={16} /> I am here (simulated)
        </button>
      ) : null
    ) : null;

  return (
    <div
      className={`app${sidebarOpen ? ' app--sidebar' : ''}${selection ? ' app--selection' : ''}${pick ? ' app--picking' : ''}`}
    >
      <Viewer3D
        model={viewModel}
        hqUrl={showingHq ? hq.modelUrl : null}
        glbUrl={glbUrl}
        graph={glbUrl ? null : graph}
        settings={settings}
        selection={selection}
        camera={camera}
        navMode={navMode}
        insetLeft={isWide && sidebarOpen ? DOCKED_SIDEBAR_PX : 0}
        onSelect={handleSelect}
        route={nav.routeView}
        highlight={nav.highlight}
        diagnosis={nav.diagnosis}
        closed={closed}
      />

      <header className="topbar card">
        <button
          className="icon-btn"
          onClick={() => setSidebarOpen((o) => !o)}
          aria-label={sidebarOpen ? 'Hide panel' : 'Show panel'}
          aria-expanded={sidebarOpen}
        >
          <PanelIcon />
        </button>
        <Logo />
        {model && (
          <span className="topbar__building" title={model.building.name}>
            {model.building.name}
          </span>
        )}
        {quality && (
          <span className={`quality quality--${quality.tone}`} title={quality.title}>
            {quality.label}
          </span>
        )}
        <span className="topbar__spacer" />
        <div className="segmented segmented--small mode-toggle" role="group" aria-label="Mode">
          <button
            className="segmented__item"
            aria-pressed={!developer}
            onClick={() => changeMode('assistant')}
            title="Assistant mode: ask, talk and follow routes"
          >
            <SparkleIcon width={15} height={15} />
            <span className="mode-toggle__label">Assistant</span>
          </button>
          <button
            className="segmented__item"
            aria-pressed={developer}
            onClick={() => changeMode('developer')}
            title="Developer mode: manual routing, scenarios, examples and tool calls"
          >
            <CodeIcon width={15} height={15} />
            <span className="mode-toggle__label">Developer</span>
          </button>
        </div>
        <PipelineChip state={run} onOpen={() => setPipelineOpen(true)} />
        <button className="btn btn--accent topbar__upload" onClick={() => setUploadOpen(true)}>
          <UploadIcon width={17} height={17} />
          <span className="topbar__upload-label">Upload assets</span>
        </button>
      </header>

      <div className="scrim" onClick={() => setSidebarOpen(false)} aria-hidden="true" />
      <div className="sidebar-slot" inert={!sidebarOpen}>
        <Sidebar
          datasets={datasets}
          dataset={dataset}
          onDataset={(d) => void openDataset(d)}
          loading={loading}
          model={viewModel}
          summary={showingHq ? hq.location.metadata?.summary : undefined}
          hqAvailable={!!hq}
          sources={sources}
          source={source}
          onSource={(s) => {
            setSource(s);
            setSelection(null);
          }}
          settings={settings}
          onSettings={setSettings}
          selection={selection}
          onSelect={selectFromList}
          onCamera={runCamera}
          onClose={() => setSidebarOpen(false)}
          developer={
            developer ? (
              <DevPanel
                buildingId={buildingId}
                model={viewModel}
                nav={nav}
                selection={selection}
                picking={pick?.kind === 'endpoint' ? pick.which : null}
                onPick={(which) => setPick(which ? { kind: 'endpoint', which } : null)}
                exactPoint={exactPoint}
                onExactPoint={setExactPoint}
                autoHq={autoHq}
                onAutoHq={changeAutoHq}
                onSelect={(sel) => {
                  focusFloorOf(sel);
                  handleSelect(sel);
                }}
              />
            ) : undefined
          }
        />
      </div>

      <nav className="hud card" aria-label="Camera">
        <button
          className="icon-btn"
          aria-pressed={navMode === 'orbit'}
          onClick={() => setNavMode('orbit')}
          aria-label="Orbit: drag to rotate"
          title="Orbit — drag to rotate (right-drag or two fingers pan)"
        >
          <OrbitIcon />
        </button>
        <button
          className="icon-btn"
          aria-pressed={navMode === 'pan'}
          onClick={() => setNavMode('pan')}
          aria-label="Pan: drag to move"
          title="Pan — drag to move (right-drag rotates)"
        >
          <PanIcon />
        </button>
        <span className="hud__sep" />
        <button className="icon-btn" onClick={() => runCamera('zoomIn')} aria-label="Zoom in">
          <PlusIcon />
        </button>
        <button className="icon-btn" onClick={() => runCamera('zoomOut')} aria-label="Zoom out">
          <MinusIcon />
        </button>
        <span className="hud__sep" />
        <button className="icon-btn" onClick={() => runCamera('iso')} aria-label="3D view">
          <CubeIcon />
        </button>
        <button className="icon-btn" onClick={() => runCamera('top')} aria-label="Top view">
          <TopIcon />
        </button>
        <button className="icon-btn" onClick={() => runCamera('fit')} aria-label="Fit to view">
          <FitIcon />
        </button>
      </nav>

      {pickHint && (
        <div className="pick-banner card" role="status">
          <PinIcon width={16} height={16} />
          <span>{pickHint}</span>
          <button className="btn btn--ghost btn--sm" onClick={() => setPick(null)}>
            Cancel
          </button>
        </div>
      )}

      {selection && (
        <DetailsCard
          model={viewModel}
          spaceMeta={showingHq ? hq.location.metadata?.spaces : undefined}
          selection={selection}
          info={info}
          onClose={() => handleSelect(null)}
          onAsk={(text) => setDraft({ text, nonce: Date.now() })}
          onFocusFloor={(level) => setSettings((s) => ({ ...s, floor: level }))}
          closed={!!closed?.includes(selection.id)}
          extraActions={detailActions}
        />
      )}

      {empty && (
        <section className="welcome card">
          <LogoMark size={44} />
          <h1>
            Find your way through <span className="text-accent">any building.</span>
          </h1>
          <p>
            AriNav follows Ariadne’s thread: upload a <code>building.json</code> or a 3D model, then
            ask the agent where things are and how to get there.
          </p>
          <button className="btn btn--accent" onClick={() => setUploadOpen(true)}>
            <UploadIcon width={17} height={17} /> Upload a building
          </button>
          {backendDown && <p className="form-error">The backend is not reachable on /api.</p>}
        </section>
      )}

      <ChatDock
        health={health}
        backendDown={backendDown}
        draft={draft}
        assistant={assistant}
        developer={developer}
        ui={nav.ui}
        selection={chatSelection}
        simulated={nav.simulated}
        onTurn={(turn) => void applyTurn(turn)}
        onConfirmed={handleConfirmed}
        onPick={(purpose, reply) => setPick({ kind: purpose, reply })}
        onUseSelectionAsLocation={() => selection && void setLocationFrom(selection, true)}
        onClearSimulated={() => nav.setSimulated(null)}
        buildingId={buildingId}
        model={viewModel}
        onSelect={selectFromList}
        onBuildingChanged={reloadModel}
      />

      <UploadDialog
        open={uploadOpen}
        datasets={datasets}
        onClose={() => setUploadOpen(false)}
        onUploaded={(r) => void handleUploaded(r)}
      />

      <PipelineDialog open={pipelineOpen} pipeline={pipeline} onClose={() => setPipelineOpen(false)} />

      {toast && (
        <div className="toast" role="status">
          {toast}
        </div>
      )}
    </div>
  );
}
