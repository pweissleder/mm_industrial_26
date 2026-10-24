// Navigation state shared by the assistant chat, the developer panel and the 3D view.
// The backend's NavigationService is the one authority; this keeps what the UI shows.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  api,
  ApiError,
  type Diagnosis,
  type Endpoint,
  type NavState,
  type NearestResponse,
  type OperationResult,
  type PreviewClosureResponse,
  type RouteResponse,
  type TurnResponse,
  type TurnUi,
  type UiState,
} from '../../lib/api';
import {
  buildUiState,
  DEFAULT_NAV,
  diagnosisIds,
  readRouteError,
  type EndpointChoice,
  type NavSettings,
  type SimulatedLocation,
} from '../../lib/nav';
import type { RouteView } from './Viewer3D';

const SIM_KEY = 'arinav:simulated-location:';

function loadSim(buildingId: string): SimulatedLocation | null {
  try {
    const raw = sessionStorage.getItem(SIM_KEY + buildingId);
    return raw ? (JSON.parse(raw) as SimulatedLocation) : null;
  } catch {
    return null;
  }
}

function saveSim(buildingId: string, value: SimulatedLocation | null) {
  try {
    if (value) sessionStorage.setItem(SIM_KEY + buildingId, JSON.stringify(value));
    else sessionStorage.removeItem(SIM_KEY + buildingId);
  } catch {
    // Storage can be unavailable; the location then lasts until reload.
  }
}

export interface RouteFailure {
  message: string;
  diagnosis: Diagnosis | null;
}

export type Which = 'start' | 'target';

export interface Navigation {
  state: NavState | null;
  /** Why the navigation API is not usable for this building, if it isn't. */
  unavailable: string | null;
  start: EndpointChoice | null;
  target: EndpointChoice | null;
  settings: NavSettings;
  route: RouteResponse | null;
  routeView: RouteView | null;
  routeFailure: RouteFailure | null;
  highlight: string[];
  diagnosis: string[];
  simulated: SimulatedLocation | null;
  lastTurn: TurnResponse | null;
  operation: OperationResult | null;
  preview: PreviewClosureResponse | null;
  nearest: NearestResponse | null;
  busy: boolean;
  message: string | null;

  ui: UiState;
  setEndpoint: (which: Which, endpoint: Endpoint | null) => void;
  updateSettings: (patch: Partial<NavSettings>) => void;
  computeRoute: (overrides?: { start?: Endpoint; target?: Endpoint }) => Promise<void>;
  clearRoute: () => void;
  scenario: (action: 'close' | 'open' | 'reset', elementId?: string) => Promise<void>;
  previewClosure: (elementIds: string[]) => Promise<void>;
  nearestExit: () => Promise<void>;
  runOperation: (operation: string, args: Record<string, unknown>) => Promise<void>;
  setSimulated: (value: SimulatedLocation | null) => void;
  applyState: (state: NavState | null) => void;
  /** Applies a turn's `ui` (except focus and building switch, which the app handles). */
  applyTurn: (turn: TurnResponse) => void;
  /** Forget everything tied to the previous building (call before switching). */
  reset: () => void;
  refresh: () => Promise<void>;
}

const describeError = (e: unknown) =>
  e instanceof ApiError ? e.message : 'The navigation service is not reachable.';

export function useNavigation(buildingId: string | null): Navigation {
  const [state, setState] = useState<NavState | null>(null);
  const [unavailable, setUnavailable] = useState<string | null>(null);
  const [start, setStart] = useState<EndpointChoice | null>(null);
  const [target, setTarget] = useState<EndpointChoice | null>(null);
  const [settings, setSettings] = useState<NavSettings>(DEFAULT_NAV);
  const [touched, setTouched] = useState<ReadonlySet<keyof NavSettings>>(new Set());
  const [routeView, setRouteView] = useState<RouteView | null>(null);
  const [routeFailure, setRouteFailure] = useState<RouteFailure | null>(null);
  const [highlight, setHighlight] = useState<string[]>([]);
  const [simulated, setSimulatedState] = useState<SimulatedLocation | null>(null);
  const [lastTurn, setLastTurn] = useState<TurnResponse | null>(null);
  const [operation, setOperation] = useState<OperationResult | null>(null);
  const [preview, setPreview] = useState<PreviewClosureResponse | null>(null);
  const [nearest, setNearest] = useState<NearestResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const token = useRef(0);

  const refresh = useCallback(async () => {
    if (!buildingId) return;
    const mine = ++token.current;
    try {
      const next = await api.navState(buildingId);
      if (mine !== token.current) return;
      setState(next);
      setUnavailable(null);
    } catch (e) {
      if (mine !== token.current) return;
      setState(null);
      setUnavailable(describeError(e));
    }
  }, [buildingId]);

  useEffect(() => {
    setState(null);
    setUnavailable(null);
    setSimulatedState(buildingId ? loadSim(buildingId) : null);
    void refresh();
  }, [buildingId, refresh]);

  // The scenario is shared (assistant, other tabs, developers): reload it when the window regains focus.
  useEffect(() => {
    const onFocus = () => void refresh();
    window.addEventListener('focus', onFocus);
    return () => window.removeEventListener('focus', onFocus);
  }, [refresh]);

  const reset = useCallback(() => {
    setStart(null);
    setTarget(null);
    setRouteView(null);
    setRouteFailure(null);
    setHighlight([]);
    setOperation(null);
    setPreview(null);
    setNearest(null);
    setMessage(null);
  }, []);

  const setEndpoint = useCallback((which: Which, endpoint: Endpoint | null) => {
    const choice = endpoint ? { endpoint, source: 'user' as const } : null;
    if (which === 'start') setStart(choice);
    else setTarget(choice);
  }, []);

  const updateSettings = useCallback((patch: Partial<NavSettings>) => {
    setSettings((s) => ({ ...s, ...patch }));
    setTouched((t) => new Set([...t, ...(Object.keys(patch) as (keyof NavSettings)[])]));
  }, []);

  const applyState = useCallback(
    (next: NavState | null) => {
      if (!next || next.building_id !== buildingId) return;
      // A different scenario makes the shown route stale: it may cross a closed door now.
      if (state && state.revision !== next.revision) {
        setRouteView(null);
        setMessage(`Scenario changed (revision ${next.revision}) · recompute routes.`);
      }
      setState(next);
    },
    [buildingId, state],
  );

  const run = useCallback(async (task: () => Promise<void>) => {
    setBusy(true);
    setMessage(null);
    try {
      await task();
    } finally {
      setBusy(false);
    }
  }, []);

  const computeRoute = useCallback(
    (overrides: { start?: Endpoint; target?: Endpoint } = {}) =>
      run(async () => {
        const from = overrides.start ?? start?.endpoint ?? simulated?.endpoint;
        const to = overrides.target ?? target?.endpoint;
        if (!buildingId || !from || !to) {
          setMessage('Choose a start and a target first.');
          return;
        }
        const precision = settings.mode === 'precision';
        try {
          const route = await api.navRoute(buildingId, {
            mode: settings.mode,
            start: from,
            target: to,
            step_free: settings.step_free,
            ...(precision && {
              profile: settings.profile,
              width_m: settings.width_m,
              safety_m: settings.safety_m,
              snap_m: settings.snap_m,
              allow_schematic_vertical: settings.allow_schematic_vertical,
            }),
          });
          // Kept even without a path, so the facts view can show why.
          setRouteView({ route, fit: route.path.length > 0, nonce: Date.now() });
          setRouteFailure(
            route.status === 'ok'
              ? null
              : {
                  message: route.unreachable_reason || `Route status: ${route.status}.`,
                  diagnosis: route.diagnosis ?? null,
                },
          );
        } catch (e) {
          setRouteView(null);
          const diagnosis =
            e instanceof ApiError ? ((e.details?.diagnosis as Diagnosis | undefined) ?? null) : null;
          setRouteFailure({ message: diagnosis?.summary || describeError(e), diagnosis });
        }
      }),
    [run, buildingId, start, target, simulated, settings],
  );

  const scenario = useCallback(
    (action: 'close' | 'open' | 'reset', elementId?: string) =>
      run(async () => {
        if (!buildingId || !state) {
          setMessage('Navigation state is not loaded.');
          return;
        }
        try {
          const next = await api.navScenario(buildingId, {
            action,
            ...(elementId && action !== 'reset' && { element_id: elementId }),
            expected_revision: state.revision,
          });
          applyState(next);
          setMessage(`Scenario revision ${next.revision} · ${next.closed.length} closed.`);
        } catch (e) {
          if (e instanceof ApiError && e.status === 409) {
            await refresh();
            setMessage('The scenario was changed elsewhere. State refreshed; please try again.');
          } else {
            setMessage(describeError(e));
          }
        }
      }),
    [run, buildingId, state, applyState, refresh],
  );

  const previewClosure = useCallback(
    (elementIds: string[]) =>
      run(async () => {
        if (!buildingId) return;
        try {
          const from = start?.endpoint ?? simulated?.endpoint;
          setPreview(
            await api.navPreviewClosure(buildingId, {
              element_ids: elementIds,
              ...(from && { from }),
              step_free: settings.step_free,
            }),
          );
        } catch (e) {
          setPreview(null);
          setMessage(describeError(e));
        }
      }),
    [run, buildingId, start, simulated, settings.step_free],
  );

  const nearestExit = useCallback(
    () =>
      run(async () => {
        const from = start?.endpoint ?? simulated?.endpoint;
        if (!buildingId || !from) {
          setMessage('Choose a start first.');
          return;
        }
        try {
          const result = await api.navNearest(buildingId, {
            from,
            category: 'exit',
            step_free: settings.step_free,
            limit: 5,
          });
          setNearest(result);
          const best = result.results[0];
          if (best) setTarget({ endpoint: best.endpoint, source: 'user' });
          else setMessage('No reachable exit from this start.');
        } catch (e) {
          setNearest(null);
          setMessage(describeError(e));
        }
      }),
    [run, buildingId, start, simulated, settings.step_free],
  );

  const runOperation = useCallback(
    (op: string, args: Record<string, unknown>) =>
      run(async () => {
        if (!buildingId) return;
        try {
          const result = await api.navOperation(buildingId, op, args);
          setOperation(result);
          if (Array.isArray(result.ids)) setHighlight(result.ids.map(String));
        } catch (e) {
          setOperation(null);
          setMessage(describeError(e));
        }
      }),
    [run, buildingId],
  );

  const setSimulated = useCallback(
    (value: SimulatedLocation | null) => {
      setSimulatedState(value);
      if (buildingId) saveSim(buildingId, value);
    },
    [buildingId],
  );

  const applyTurn = useCallback(
    (turn: TurnResponse) => {
      setLastTurn(turn);
      const ui: TurnUi = turn.ui ?? {};
      if (ui.settings) {
        const patch = Object.fromEntries(
          Object.entries(ui.settings).filter(([k, v]) => v !== undefined && v !== null && k in DEFAULT_NAV),
        ) as Partial<NavSettings>;
        setSettings((s) => ({ ...s, ...patch }));
      }
      if (ui.endpoints) {
        // Shown in the developer controls, but not sent back as the user's own choice.
        const { start: s, destination: d } = ui.endpoints;
        if (s) setStart({ endpoint: s, source: 'assistant' });
        if (d) setTarget({ endpoint: d, source: 'assistant' });
      }
      if (ui.route) {
        setRouteView({ route: ui.route, fit: true, nonce: Date.now() });
        setRouteFailure(
          ui.route.status === 'ok'
            ? null
            : {
                message: ui.route.unreachable_reason || `Route status: ${ui.route.status}.`,
                diagnosis: ui.route.diagnosis ?? null,
              },
        );
      } else if (ui.route_error) {
        setRouteView(null);
        setRouteFailure(readRouteError(ui.route_error));
      }
      if (ui.highlight) setHighlight(ui.highlight.map(String));
      if (ui.operation) {
        setOperation(ui.operation);
        if (!ui.highlight && Array.isArray(ui.operation.ids)) {
          setHighlight(ui.operation.ids.map(String));
        }
      }
      // The scenario may have changed (confirmed proposal, another tab or the developer panel).
      void refresh();
    },
    [refresh],
  );

  const ui = useMemo(
    () =>
      buildUiState({ buildingId, start, target, simulated, settings, touched }),
    [buildingId, start, target, simulated, settings, touched],
  );

  const diagnosis = useMemo(() => diagnosisIds(routeFailure?.diagnosis), [routeFailure]);

  return {
    state,
    unavailable,
    start,
    target,
    settings,
    route: routeView?.route ?? null,
    routeView,
    routeFailure,
    highlight,
    diagnosis,
    simulated,
    lastTurn,
    operation,
    preview,
    nearest,
    busy,
    message,
    ui,
    setEndpoint,
    updateSettings,
    computeRoute,
    clearRoute: () => {
      setRouteView(null);
      setRouteFailure(null);
    },
    scenario,
    previewClosure,
    nearestExit,
    runOperation,
    setSimulated,
    applyState,
    applyTurn,
    reset,
    refresh,
  };
}
