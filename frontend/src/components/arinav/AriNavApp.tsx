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
import { floorLabel, type BuildingModel, type Selection } from '../../lib/building';
import {
  elementLabel,
  endpointFromSelection,
  selectionFromFocus,
  type AppMode,
  type SimulatedLocation,
} from '../../lib/nav';
import { GuidanceVoice, type GuidanceVoiceState } from '../../lib/guidanceVoice';
import type { Names } from '../../lib/simulation';
import { createSpeechPort } from '../../lib/voice';
import ChatDock, { type Draft, type PickPurpose } from './ChatDock';
import DetailsCard from './DetailsCard';
import DevPanel from './DevPanel';
import GuidancePanel from './GuidancePanel';
import {
  CodeIcon,
  CubeIcon,
  FitIcon,
  FrontIcon,
  MoreIcon,
  NavigateIcon,
  MinusIcon,
  OrbitIcon,
  PanelIcon,
  PanIcon,
  PinIcon,
  PlusIcon,
  TopIcon,
  UploadIcon,
  UserIcon,
} from './Icons';
import Logo, { LogoMark } from './Logo';
import { HqPill, PipelineDialog } from './PipelineStatus';
import type { PickHit } from './scene';
import Sidebar, { GENERATED_SOURCE, HQ_SOURCE } from './Sidebar';
import UploadDialog from './UploadDialog';
import { useGuidance } from './useGuidance';
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
// Navigation-panel parts dropped when it has to make room, and a folded details card's height.
const GUIDANCE_EXTRAS = '.guidance__settings, .guidance__debug-wrap, .guidance__hint';
const FOLDED_CARD_PX = 86;
// Below this an open details card folds instead of scrolling.
const MIN_OPEN_CARD_PX = 240;
// Short camera bar until measured: zoom in, zoom out, fit and "more".
const COMPACT_HUD_PX = 168;
// Below this the sidebar starts (and folds) closed, leaving the 3D view room.
const ROOMY = '(min-width: 1101px)';
const roomy = () => window.matchMedia(ROOMY).matches;
// Sidebar width plus its gutters, matching --sidebar-w and --gutter in global.css.
const DOCKED_SIDEBAR_PX = 340 + 2 * 16;

const PROFILES: {
  id: AppMode;
  label: string;
  title: string;
  Icon: typeof UserIcon;
}[] = [
  {
    id: 'navigator',
    label: 'Navigator',
    title: 'Navigator: just ask and follow the route',
    Icon: NavigateIcon,
  },
  {
    id: 'user',
    label: 'User',
    title: 'User: explore the building, ask and set your location',
    Icon: UserIcon,
  },
  {
    id: 'developer',
    label: 'Developer',
    title: 'Developer: manual routing, scenarios, examples and tool calls',
    Icon: CodeIcon,
  },
];

/** What the next click in the 3D view sets, instead of only selecting. */
type Pick = { kind: 'endpoint'; which: Which } | { kind: PickPurpose; reply: boolean };

function useMedia(query: string): boolean {
  const [value, setValue] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const mq = window.matchMedia(query);
    const update = () => setValue(mq.matches);
    mq.addEventListener('change', update);
    return () => mq.removeEventListener('change', update);
  }, [query]);
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
  const [mode, setMode] = useState<AppMode>(() => {
    const stored = recall(LAST_MODE);
    // A legacy 'assistant' maps to the user profile.
    return stored === 'developer' || stored === 'navigator' ? stored : 'user';
  });
  const [datasets, setDatasets] = useState<string[]>([]);
  // Uploaded plan-only datasets that still need their reconstruct stage (no building.json yet).
  const [pending, setPending] = useState<string[]>([]);
  // No building.json yet, so no browser preview: it appears once reconstruction has passed.
  const [lowUnavailable, setLowUnavailable] = useState(false);
  const [dataset, setDataset] = useState<string | null>(null);
  const [model, setModel] = useState<BuildingModel | null>(null);
  const [buildingId, setBuildingId] = useState<string | null>(null);
  const [runRequest, setRunRequest] = useState<{
    dataset: string;
    nonce: number;
  } | null>(null);
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
  // Navigator keeps the map clear: the sidebar starts closed there, and below 1100 px everywhere.
  const [sidebarOpen, setSidebarOpen] = useState(() => roomy() && mode !== 'navigator');
  const [uploadOpen, setUploadOpen] = useState(false);
  const [graph, setGraph] = useState<RoomGraphData | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [pick, setPick] = useState<Pick | null>(null);
  const [exactPoint, setExactPoint] = useState(false);
  const isWide = useMedia(WIDE);
  const isRoomy = useMedia(ROOMY);
  const seenRoomy = useRef(isRoomy);
  useEffect(() => {
    if (seenRoomy.current === isRoomy) return;
    seenRoomy.current = isRoomy;
    setSidebarOpen(isRoomy && mode !== 'navigator');
    // Only crossing the breakpoint folds or unfolds; the mode is read, not tracked.
  }, [isRoomy]);
  const appRef = useRef<HTMLDivElement>(null);
  const nav = useNavigation(buildingId);
  const developer = mode === 'developer';
  const navigatorMode = mode === 'navigator';
  const userMode = mode === 'user';

  const notify = useCallback((text: string) => {
    setToast(text);
    window.setTimeout(() => setToast((t) => (t === text ? null : t)), 4000);
  }, []);

  const { reset: resetNav } = nav;
  const openDataset = useCallback(
    async (name: string, startPipeline = false) => {
      setLowUnavailable(false);
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

  const reconstructOpened = useRef(false);
  // A plan-only dataset: the pipeline reconstructs building.json first, so run it at once and
  // open the Low model when that stage has passed.
  const openPending = useCallback(
    (name: string) => {
      setDataset(name);
      setBuildingId(null);
      setModel(null);
      setSelection(null);
      setLooseGlb(null);
      setSource(GENERATED_SOURCE);
      setPick(null);
      resetNav();
      setLowUnavailable(true);
      reconstructOpened.current = false;
      setRunRequest({ dataset: name, nonce: Date.now() });
      remember(LAST_DATASET, name);
    },
    [resetNav],
  );
  const refreshDatasets = useCallback(
    () =>
      api.datasetIndex().then(({ datasets: list, needs_reconstruction }) => {
        setDatasets(list);
        setPending(needs_reconstruction);
      }),
    [],
  );

  // The browser preview shows at once; the pipeline's high-quality version replaces it
  // when ready (unless an uploaded GLB is being looked at) and stays switchable.
  const pipeline = usePipeline(dataset, buildingId, runRequest, {
    auto: autoHq,
  });

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
          ? 'High-quality model finished — now showing it.'
          : 'High-quality model finished — pick it under 3D source.',
      );
    }
    wasBuilding.current = false;
  }, [run.phase, hq, source, notify]);

  // Reconstruction done: building.json exists now, so load the Low model (once).
  // Only this dataset's run counts: right after opening it, `run` can still hold the previous
  // dataset's finished run, whose reconstruct stage may have passed too.
  const reconstructed =
    lowUnavailable &&
    'run' in run &&
    run.run?.dataset === dataset &&
    run.run.stages.reconstruct?.status === 'passed';
  useEffect(() => {
    if (!reconstructed || !dataset || reconstructOpened.current) return;
    reconstructOpened.current = true;
    void openDataset(dataset, false);
    void refreshDatasets().catch(() => {}); // no longer pending
  }, [reconstructed, dataset, openDataset, refreshDatasets]);

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
  // Floor rail entries, top floor first under "All", as in the sidebar's floor picker.
  const floorChoices = useMemo(() => {
    const floors = [...(viewModel?.floors ?? [])].sort((a, b) => b.level - a.level);
    type Choice = {
      key: string;
      level: ViewSettings['floor'];
      label: string;
      title: string;
    };
    const all: Choice = {
      key: 'all',
      level: 'all',
      label: 'All',
      title: 'All floors',
    };
    const choices = floors.map((f): Choice => ({ key: f.id, level: f.level, label: floorLabel(f), title: f.name }));
    return floors.length > 1 ? [all, ...choices] : [];
  }, [viewModel]);

  // ---- Phase B: virtual navigation along the backend route, with spoken hints -------------
  const speechPort = useMemo(() => createSpeechPort(), []);
  const [voiceState, setVoiceState] = useState<GuidanceVoiceState | null>(null);
  const guideVoice = useMemo(
    () => new GuidanceVoice(speechPort, { speed: 1.1, onChange: setVoiceState }),
    [speechPort],
  );
  useEffect(
    () => () => {
      guideVoice.cancel();
      speechPort.dispose();
    },
    [guideVoice, speechPort],
  );
  const voiceAvailable = !!assistant?.voice;
  useEffect(() => {
    // Without voice on the server, hints stay silent instead of failing each time.
    if (assistant && !assistant.voice) guideVoice.setMuted(true);
  }, [assistant, guideVoice]);
  const names = useMemo<Names>(() => {
    const spaces = new Map(viewModel?.spaces.map((x) => [x.id, x.name]) ?? []);
    const floors = new Map(viewModel?.floors.map((f) => [f.id, f.name]) ?? []);
    const t = nav.route?.target;
    return {
      space: (id) => (id ? (spaces.get(id) ?? null) : null),
      floor: (id) => (id ? (floors.get(id) ?? null) : null),
      element: (id) => {
        const label = elementLabel(id, viewModel);
        return label === id ? null : label;
      },
      target: t
        ? t.kind === 'point'
          ? `the chosen point in ${spaces.get(t.space_id) ?? 'the room'}`
          : elementLabel(t.id ?? t.space_id, viewModel)
        : null,
    };
  }, [viewModel, nav.route]);
  const guidance = useGuidance({
    buildingId,
    route: nav.route,
    names,
    scenarioRevision: nav.state?.revision,
    settings: nav.settings,
    onRoute: nav.showRoute,
    voice: guideVoice,
  });
  const [guidanceHidden, setGuidanceHidden] = useState(false);
  useEffect(() => setGuidanceHidden(false), [nav.route]);
  // While navigating, focus the floor the person is on (higher floors hidden, lower ghosted).
  const walkerFloor = guidance.navigating ? guidance.snapshot.progress?.floor_id : undefined;
  useEffect(() => {
    if (!walkerFloor) return;
    const level = viewModel?.floors.find((f) => f.id === walkerFloor)?.level;
    if (level !== undefined) setSettings((s) => (s.floor === level ? s : { ...s, floor: level }));
  }, [walkerFloor, viewModel]);
  // Others may close doors meanwhile (other tabs, the team agent): poll the shared scenario
  // while navigating, so the route is re-checked within seconds.
  const navRefresh = nav.refresh;
  useEffect(() => {
    if (!guidance.navigating) return;
    const timer = window.setInterval(() => void navRefresh(), 2000);
    return () => window.clearInterval(timer);
  }, [guidance.navigating, navRefresh]);
  const wasNavigating = useRef(false);
  useEffect(() => {
    if (wasNavigating.current && !guidance.navigating) setSettings((s) => ({ ...s, floor: 'all' }));
    wasNavigating.current = guidance.navigating;
  }, [guidance.navigating]);

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
      .datasetIndex()
      .then(({ datasets: list, needs_reconstruction }) => {
        setDatasets(list);
        setPending(needs_reconstruction);
        const last = recall(LAST_DATASET);
        const known = last && (list.includes(last) || needs_reconstruction.includes(last));
        const first = known ? last : list[0];
        if (first && needs_reconstruction.includes(first)) {
          openPending(first);
          setLoading(false);
        } else if (first) void openDataset(first);
        else setLoading(false);
      })
      .catch(() => setLoading(false));
  }, [openDataset, openPending]);

  // User profile: the details card folds to its title row 5 s after a selection; hovering or
  // focusing it pauses the timer, and a new selection (or the chevron) unfolds it again.
  // In every profile it folds while a route is shown, so the navigation panel below has room.
  const [cardCollapsed, setCardCollapsed] = useState(false);
  const [cardHeld, setCardHeld] = useState(false);
  const guidanceShown =
    !guidanceHidden &&
    (guidance.snapshot.route?.status === 'ok' || guidance.snapshot.status === 'blocked');
  useEffect(() => {
    setCardCollapsed(guidance.navigating);
    setCardHeld(false);
    // Only a new selection resets the fold; the navigation state is read, not tracked.
  }, [selection]);
  useEffect(() => {
    if (guidanceShown || guidance.navigating) setCardCollapsed(true);
  }, [guidanceShown, guidance.navigating]);

  // Phones: the camera bar shows zoom and fit only, plus a button for the rest.
  // Phones, and desktops where the details/navigation would run into it, show a short camera bar.
  const isPhone = useMedia('(max-width: 640px)');
  const [hudExpanded, setHudExpanded] = useState(false);
  const [hudCrowded, setHudCrowded] = useState(false);
  const hudCompact = (isPhone || hudCrowded) && !hudExpanded;
  const hudExpandedRef = useRef(hudExpanded);
  hudExpandedRef.current = hudExpanded;

  // The details card and navigation panel share the right margin with the camera bar and end
  // above it. When they would run into it, the camera bar shortens first, then the card scrolls
  // or (short of room) folds, then the navigation panel drops its settings. Re-checked on resize and when their content
  // changes, not on a manual unfold, so the user can still open the card (it scrolls then).
  const stackRef = useRef<HTMLDivElement>(null);
  const railRef = useRef<HTMLDivElement>(null);
  const [guidanceCompact, setGuidanceCompact] = useState(false);
  const hudHeights = useRef({ full: 0, compact: COMPACT_HUD_PX });
  const fitStack = useCallback(() => {
    const app = appRef.current;
    const stack = stackRef.current;
    const rail = railRef.current;
    if (!app || !stack || !rail || getComputedStyle(stack).display === 'contents') return;
    const railTop = (rail.firstElementChild ?? rail).getBoundingClientRect().top;
    app.style.setProperty('--rail-top', `${railTop - app.getBoundingClientRect().top}px`);
    requestAnimationFrame(() => {
      // The room above a full and above a short camera bar, from the bar's measured heights.
      const hud = rail.querySelector<HTMLElement>('.hud');
      const hudNow = hud?.offsetHeight ?? 0;
      if (hud) hudHeights.current[hud.classList.contains('hud--compact') ? 'compact' : 'full'] = hudNow;
      const { full: hudFull, compact: hudShort } = hudHeights.current;
      const room = stack.clientHeight + hudNow - (hudFull || hudNow);
      const roomShort = hudExpandedRef.current ? room : stack.clientHeight + hudNow - hudShort;
      const card = stack.querySelector<HTMLElement>('.details');
      const guide = stack.querySelector<HTMLElement>('.guidance');
      // Natural heights, not the boxes mid-animation: the hidden parts' content is still there.
      const extras = guide ? [...guide.querySelectorAll<HTMLElement>(GUIDANCE_EXTRAS)] : [];
      const guideH = guide
        ? guide.scrollHeight +
          extras.reduce((sum, el) => sum + el.scrollHeight - el.offsetHeight, 0) +
          (guide.classList.contains('guidance--compact') ? extras.length * 8 : 0)
        : 0;
      const gap = card && guide ? 8 : 0;
      const cardOpen = card && !card.classList.contains('details--collapsed');
      const body = card?.querySelector<HTMLElement>('.details__body');
      const inner = card?.querySelector<HTMLElement>('.details__body-inner');
      const cardFull =
        card && body && inner ? card.scrollHeight - body.offsetHeight + inner.scrollHeight : 0;
      const need = (cardOpen ? cardFull : (card?.offsetHeight ?? 0)) + gap + guideH;
      if (need <= room) {
        // A card folded to make room keeps the short bar; it only grows back once all fits.
        if (cardOpen || !card) setHudCrowded(false);
        setGuidanceCompact(false);
        return;
      }
      setHudCrowded(true);
      // With the short bar the open card may still fit, or scroll in a useful height.
      if (need <= roomShort || (cardOpen && roomShort - gap - guideH >= MIN_OPEN_CARD_PX)) {
        setGuidanceCompact(false);
        return;
      }
      if (cardOpen) setCardCollapsed(true);
      const folded = card ? FOLDED_CARD_PX : 0;
      setGuidanceCompact(!!guide && folded + gap + guideH > roomShort);
    });
  }, []);
  useEffect(() => {
    const app = appRef.current;
    if (!app || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(() => fitStack());
    observer.observe(app);
    if (railRef.current) observer.observe(railRef.current);
    return () => observer.disconnect();
  }, [fitStack]);
  useEffect(() => {
    fitStack();
  }, [fitStack, selection, guidanceShown, guidance.navigating, hudCompact, floorChoices.length]);
  useEffect(() => {
    if (!userMode || !selection || cardHeld || cardCollapsed) return;
    const timer = window.setTimeout(() => setCardCollapsed(true), 5000);
    return () => window.clearTimeout(timer);
  }, [userMode, selection, cardHeld, cardCollapsed]);

  // The chat dock grows with suggestions and context chips; overlays above it follow --chat-h.
  useEffect(() => {
    const app = appRef.current;
    const chat = app?.querySelector<HTMLElement>('.chat');
    if (!app || !chat) return;
    const observer = new ResizeObserver(() =>
      app.style.setProperty('--chat-h', `${chat.offsetHeight}px`),
    );
    observer.observe(chat);
    return () => observer.disconnect();
  }, []);

  const changeMode = (next: AppMode) => {
    setMode(next);
    remember(LAST_MODE, next);
    setPick(null);
    if (next === 'developer') setSidebarOpen(true);
    if (next === 'navigator') setSidebarOpen(false);
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
      let location: SimulatedLocation = {
        endpoint: { kind: 'space', id: spaceId },
        label,
      };
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
      } else if (!route && (ui.highlight?.length ?? 0) > 1) {
        // Several matches: drop the old selection and step back to the overview to show them all.
        const floorOf = new Map<string, string>();
        for (const x of [...(viewModel?.spaces ?? []), ...(viewModel?.assets ?? [])]) {
          floorOf.set(x.id, x.floor_id);
        }
        const floors = new Set(ui.highlight!.map((id) => floorOf.get(String(id))));
        if (floors.size > 1) setSettings((s) => (s.floor === 'all' ? s : { ...s, floor: 'all' }));
        setSelection(null);
        setInfo(undefined);
        setCamera({ action: 'fit', nonce: Date.now() });
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
    } else if (result.reconstructable) {
      openPending(result.dataset);
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
  const allDatasets = useMemo(
    () => [...datasets, ...pending.filter((d) => !datasets.includes(d))],
    [datasets, pending],
  );

  // The assistant also sees what the user is inspecting on the map (D2), never as their position.
  const chatUi = useMemo(
    () => ({
      ...nav.ui,
      inspected:
        selection && selection.kind !== 'object' ? { kind: selection.kind, id: selection.id } : null,
    }),
    [nav.ui, selection],
  );
  const chatSelection = useMemo(
    () =>
      selection && selection.kind !== 'object'
        ? {
            name: elementLabel(selection.id, viewModel),
            room: selection.kind === 'space',
          }
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

  const showCard =
    !!selection && !(navigatorMode && selection.kind === 'space');
  const routeHandlers =
    selection && selection.kind !== 'object' && !navigatorMode
      ? developer
        ? {
            onFromHere: selectedEndpoint
              ? () => nav.setEndpoint('start', selectedEndpoint)
              : undefined,
            onToHere: selectedEndpoint
              ? () => nav.setEndpoint('target', selectedEndpoint)
              : undefined,
            startSet: !!nav.start,
            targetSet: !!nav.target,
          }
        : {
            onFromHere: () => void setLocationFrom(selection, false),
            onToHere: () =>
              setDraft({
                text: `How do I get to ${elementLabel(selection.id, viewModel)}?`,
                nonce: Date.now(),
                send: true,
              }),
            startSet: !!nav.simulated,
            targetSet: !!nav.route?.target,
          }
      : {};

  return (
    <div
      ref={appRef}
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
        walker={guidance.pose}
        guidanceCamera={guidance.camera}
        onPreviewDone={guidance.previewDone}
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
        {(model || lowUnavailable) && !glbUrl && !navigatorMode && (
          <HqPill
            state={run}
            showingHq={showingHq}
            lowUnavailable={lowUnavailable}
            onOpen={() => setPipelineOpen(true)}
          />
        )}
        <span className="topbar__spacer" />
        <div className="segmented segmented--small mode-toggle" role="group" aria-label="Profile">
          {PROFILES.map(({ id, label, title, Icon }) => (
            <button
              key={id}
              className="segmented__item"
              aria-pressed={mode === id}
              aria-label={label}
              onClick={() => changeMode(id)}
              title={title}
            >
              <Icon width={15} height={15} />
              <span className="mode-toggle__label">{label}</span>
            </button>
          ))}
        </div>
        {!navigatorMode && (
          <button
            className="btn btn--accent topbar__upload"
            onClick={() => setUploadOpen(true)}
            aria-label="Upload assets"
            title="Upload assets"
          >
            <UploadIcon width={17} height={17} />
            <span className="topbar__upload-label">Upload assets</span>
          </button>
        )}
      </header>

      <div className="scrim" onClick={() => setSidebarOpen(false)} aria-hidden="true" />
      <div className="sidebar-slot" inert={!sidebarOpen}>
        <Sidebar
          datasets={allDatasets}
          pending={pending}
          dataset={dataset}
          onDataset={(d) => (pending.includes(d) ? openPending(d) : void openDataset(d))}
          lowUnavailable={lowUnavailable}
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

      {/* Right edge: camera bar above the floor rail, pinned to the bottom (not pushed by the chat). */}
      <div className="right-rail" ref={railRef}>
        <nav className={`hud card${hudCompact ? ' hud--compact' : ''}`} aria-label="Camera">
          {!hudCompact && (
            <>
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
            </>
          )}
          <button className="icon-btn" onClick={() => runCamera('zoomIn')} aria-label="Zoom in">
            <PlusIcon />
          </button>
          <button className="icon-btn" onClick={() => runCamera('zoomOut')} aria-label="Zoom out">
            <MinusIcon />
          </button>
          {!hudCompact && (
            <>
              <span className="hud__sep" />
              <button className="icon-btn" onClick={() => runCamera('iso')} aria-label="3D view">
                <CubeIcon />
              </button>
              <button className="icon-btn" onClick={() => runCamera('top')} aria-label="Top view">
                <TopIcon />
              </button>
              <button className="icon-btn" onClick={() => runCamera('front')} aria-label="Front view">
                <FrontIcon />
              </button>
            </>
          )}
          <button className="icon-btn" onClick={() => runCamera('fit')} aria-label="Fit to view">
            <FitIcon />
          </button>
          {(isPhone || hudCrowded) && (
            <button
              className="icon-btn hud__more"
              aria-expanded={hudExpanded}
              onClick={() => setHudExpanded((o) => !o)}
              aria-label={hudExpanded ? 'Fewer view controls' : 'More view controls'}
            >
              <MoreIcon />
            </button>
          )}
        </nav>

        {/* Quick floor switching at every width (the sidebar no longer has a floor picker). */}
        {!glbUrl && floorChoices.length > 0 && (
          <div className="floor-rail card" role="radiogroup" aria-label="Floor">
            {floorChoices.map((f) => (
              <button
                key={f.key}
                role="radio"
                aria-checked={settings.floor === f.level}
                className="floor-rail__item"
                title={f.title}
                onClick={() => setSettings((s) => ({ ...s, floor: f.level }))}
              >
                {f.label}
              </button>
            ))}
          </div>
        )}
      </div>

      {pickHint && (
        <div className="pick-banner card" role="status">
          <PinIcon width={16} height={16} />
          <span>{pickHint}</span>
          <button className="btn btn--ghost btn--sm" onClick={() => setPick(null)}>
            Cancel
          </button>
        </div>
      )}

      {/* Top right: the selection's details above the navigation panel, never overlapping. */}
      <div className="top-stack" ref={stackRef}>
        {selection && showCard && (
          <DetailsCard
            model={viewModel}
            spaceMeta={showingHq ? hq.location.metadata?.spaces : undefined}
            selection={selection}
            info={info}
            onClose={() => handleSelect(null)}
            onAsk={(text) => setDraft({ text, nonce: Date.now(), send: true })}
            closed={!!closed?.includes(selection.id)}
            canToggle={!navigatorMode}
            onHold={userMode ? setCardHeld : undefined}
            collapsed={cardCollapsed}
            onToggleCollapsed={() => setCardCollapsed((c) => !c)}
            {...routeHandlers}
          />
        )}
        {guidanceShown && (
          <GuidancePanel
            guidance={guidance}
            compact={guidanceCompact}
            developer={developer}
            destination={names.target}
            floorName={names.floor}
            scenarioRevision={nav.state?.revision}
            voice={voiceState ?? guideVoice.state}
            voiceAvailable={voiceAvailable}
            onMute={() => {
              speechPort.unlock();
              guideVoice.setMuted(!guideVoice.state.muted);
            }}
            onRepeat={() => guideVoice.repeat()}
            onVoiceSpeed={(v) => guideVoice.setSpeed(v)}
            onUnlockAudio={() => speechPort.unlock()}
            onClose={() => setGuidanceHidden(true)}
          />
        )}
      </div>

      {empty && (
        <section className="welcome card">
          <LogoMark size={44} />
          <h1>
            Find your way through <span className="text-accent">any building.</span>
          </h1>
          <p>
            {lowUnavailable && run.phase === 'not_built' ? (
              <>
                This dataset has floor plans but no <code>building.json</code> yet. Build it from the
                tag above: the pipeline reconstructs the building first, then the preview appears.
              </>
            ) : lowUnavailable ? (
              <>
                Reconstructing the building from its floor plans… The quick preview needs a{' '}
                <code>building.json</code>, so it appears once this step is done.
              </>
            ) : (
              <>
                AriNav follows Ariadne’s thread: upload a <code>building.json</code>, floor plans or a 3D
                model, then ask the agent where things are and how to get there.
              </>
            )}
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
        profile={mode}
        ui={chatUi}
        selection={chatSelection}
        simulated={nav.simulated}
        onTurn={(turn) => void applyTurn(turn)}
        onConfirmed={handleConfirmed}
        onPick={(purpose, reply) => setPick({ kind: purpose, reply })}
        onUseSelectionAsLocation={() => selection && void setLocationFrom(selection, true)}
        onClearSimulated={() => nav.setSimulated(null)}
        buildingId={buildingId}
        model={viewModel}
        suggestions={showingHq ? hq.location.metadata?.suggestions : undefined}
        onSelect={selectFromList}
        onBuildingChanged={reloadModel}
      />

      <UploadDialog
        open={uploadOpen}
        datasets={allDatasets}
        onClose={() => setUploadOpen(false)}
        onUploaded={(r) => void handleUploaded(r)}
      />

      <PipelineDialog
        open={pipelineOpen}
        pipeline={pipeline}
        lowUnavailable={lowUnavailable}
        onClose={() => setPipelineOpen(false)}
      />

      {toast && (
        <div className="toast" role="status">
          {toast}
        </div>
      )}
    </div>
  );
}
