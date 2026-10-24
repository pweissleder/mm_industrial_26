import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, datasetFileUrl, type RoomGraphData, type HealthResponse, type UploadResponse } from '../../lib/api';
import type { BuildingModel, Selection } from '../../lib/building';
import ChatDock, { type Draft } from './ChatDock';
import DetailsCard from './DetailsCard';
import {
  CubeIcon,
  FitIcon,
  MinusIcon,
  OrbitIcon,
  PanelIcon,
  PanIcon,
  PlusIcon,
  TopIcon,
  UploadIcon,
} from './Icons';
import Logo, { LogoMark } from './Logo';
import { PipelineChip, PipelineDialog } from './PipelineStatus';
import Sidebar, { GENERATED_SOURCE, HQ_SOURCE } from './Sidebar';
import UploadDialog from './UploadDialog';
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
const isGlb = (name: string) => /\.(glb|gltf)$/i.test(name);
const WIDE = '(min-width: 1024px)';
const wide = () => window.matchMedia(WIDE).matches;
// Sidebar width plus its gutters, matching --sidebar-w and --gutter in global.css.
const DOCKED_SIDEBAR_PX = 340 + 2 * 16;

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

function remember(dataset: string) {
  try {
    localStorage.setItem(LAST_DATASET, dataset);
  } catch {
    // Storage can be unavailable (private mode); remembering is only a convenience.
  }
}

function recall(): string | null {
  try {
    return localStorage.getItem(LAST_DATASET);
  } catch {
    return null;
  }
}

export default function AriNavApp() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [backendDown, setBackendDown] = useState(false);
  const [datasets, setDatasets] = useState<string[]>([]);
  const [dataset, setDataset] = useState<string | null>(null);
  const [model, setModel] = useState<BuildingModel | null>(null);
  const [buildingId, setBuildingId] = useState<string | null>(null);
  const [runRequest, setRunRequest] = useState<{ dataset: string; nonce: number } | null>(null);
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
  const isWide = useWide();

  const notify = useCallback((text: string) => {
    setToast(text);
    window.setTimeout(() => setToast((t) => (t === text ? null : t)), 4000);
  }, []);

  const openDataset = useCallback(
    async (name: string, startPipeline = false) => {
      setDataset(name);
      setBuildingId(null);
      switchedTo.current = null;
      setLoading(true);
      setSelection(null);
      setLooseGlb(null);
      setSource(GENERATED_SOURCE);
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
        remember(name);
      } catch {
        setModel(null);
        notify(`Could not load “${name}”.`);
      } finally {
        setLoading(false);
      }
    },
    [notify],
  );

  const refreshDatasets = useCallback(() => api.datasets().then(setDatasets), []);

  // The browser preview shows at once; the pipeline's high-quality version replaces it
  // when ready (unless an uploaded GLB is being looked at) and stays switchable.
  const pipeline = usePipeline(dataset, buildingId, runRequest);

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
  const closed = model?.closed;
  const hqModel = useMemo(() => hqBuilding && { ...hqBuilding, closed }, [hqBuilding, closed]);
  const viewModel = showingHq && hqModel ? hqModel : model;

  useEffect(() => {
    api
      .health()
      .then(setHealth)
      .catch(() => setBackendDown(true));
    api
      .datasets()
      .then((list) => {
        setDatasets(list);
        const last = recall();
        const first = last && list.includes(last) ? last : list[0];
        if (first) void openDataset(first);
        else setLoading(false);
      })
      .catch(() => setLoading(false));
  }, [openDataset]);

  const handleSelect = useCallback((sel: Selection | null, objectInfo?: ObjectInfo) => {
    setSelection(sel);
    setInfo(objectInfo);
  }, []);

  // Picking from the list: bring the item's floor into focus so it isn't hidden under the one above.
  const selectFromList = (sel: Selection) => {
    const floorId =
      sel.kind === 'space'
        ? viewModel?.spaces.find((s) => s.id === sel.id)?.floor_id
        : viewModel?.assets.find((a) => a.id === sel.id)?.floor_id;
    const level = viewModel?.floors.find((f) => f.id === floorId)?.level;
    const top = Math.max(...(viewModel?.floors.map((f) => f.level) ?? [0]));
    if (level !== undefined && !(settings.floor === 'all' && level === top)) {
      setSettings((s) => ({ ...s, floor: level }));
    }
    handleSelect(sel);
    if (!isWide) setSidebarOpen(false);
  };

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

  return (
    <div
      className={`app${sidebarOpen ? ' app--sidebar' : ''}${selection ? ' app--selection' : ''}`}
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

      {selection && (
        <DetailsCard
          model={viewModel}
          spaceMeta={showingHq ? hq.location.metadata?.spaces : undefined}
          selection={selection}
          info={info}
          onClose={() => handleSelect(null)}
          onAsk={(text) => setDraft({ text, nonce: Date.now() })}
          onFocusFloor={(level) => setSettings((s) => ({ ...s, floor: level }))}
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
