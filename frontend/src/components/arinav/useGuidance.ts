// Live guidance in AriNav (Phase B): owns the deterministic GuidanceSim, drives it with
// requestAnimationFrame, publishes the walker pose per frame (to the 3D view, without re-rendering
// the app) and a throttled snapshot (to the panels), and reroutes when the shared scenario changes.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, ApiError, type Endpoint, type RouteRequest, type RouteResponse } from '../../lib/api';
import type { NavSettings } from '../../lib/nav';
import {
  buildInstructions,
  GuidanceSim,
  remainingElements,
  type Instruction,
  type Names,
  type SimEvent,
  type SimSnapshot,
} from '../../lib/simulation';
import type { WalkerPose } from './scene';
import type { GuidanceCameraCommand } from './Viewer3D';

/** Per-frame walker pose, read by the 3D view outside React rendering. */
export interface PoseStore {
  get(): WalkerPose | null;
  subscribe(listener: () => void): () => void;
}

export interface GuidanceVoicePort {
  say(key: string, text: string): void;
  cancel(): void;
}

export interface Guidance {
  snapshot: SimSnapshot;
  pose: PoseStore;
  camera: GuidanceCameraCommand | null;
  previewing: boolean;
  /** active, paused or recomputing: a navigation is in progress. */
  navigating: boolean;
  instruction: Instruction | null;
  toNextM: number | null;
  canStart: boolean;
  start: () => void;
  pause: () => void;
  resume: () => void;
  stop: () => void;
  setSpeed: (factor: number) => void;
  preview: () => void;
  skipPreview: () => void;
  previewDone: () => void;
  overview: () => void;
  recenter: () => void;
}

const SNAPSHOT_MS = 100;
const PREVIEW_MS = 7000;

const endpointOf = (end: RouteResponse['target']): Endpoint | null => {
  if (!end) return null;
  if (end.kind === 'point') return { kind: 'point', space_id: end.space_id, position: end.position };
  if ((end.kind === 'space' || end.kind === 'asset' || end.kind === 'opening') && end.id) {
    return { kind: end.kind, id: end.id } as Endpoint;
  }
  return null;
};

function createPoseStore(): PoseStore & { set(pose: WalkerPose | null): void } {
  let pose: WalkerPose | null = null;
  const listeners = new Set<() => void>();
  return {
    get: () => pose,
    set(next) {
      pose = next;
      listeners.forEach((l) => l());
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
}

export function useGuidance(args: {
  buildingId: string | null;
  /** The route shown in AriNav (manual or assistant); guidance follows a copy of it. */
  route: RouteResponse | null;
  names: Names;
  scenarioRevision: number | undefined;
  settings: NavSettings;
  /** Show a recomputed route in the map/panels (same RouteResponse the backend returned). */
  onRoute: (route: RouteResponse) => void;
  voice?: GuidanceVoicePort | null;
}): Guidance {
  const { buildingId, route, names, scenarioRevision, settings, onRoute, voice } = args;
  const sim = useRef(new GuidanceSim());
  const pose = useMemo(createPoseStore, []);
  const [snapshot, setSnapshot] = useState<SimSnapshot>(() => sim.current.snapshot());
  const [camera, setCamera] = useState<GuidanceCameraCommand | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const token = useRef(0); // bumped by every newer state; async answers for older ones are dropped
  const ownRoute = useRef<RouteResponse | null>(null); // the route guidance itself installed
  const revisionAtLoad = useRef<number | undefined>(undefined);
  const latest = useRef({ names, settings, onRoute, voice, buildingId });
  latest.current = { names, settings, onRoute, voice, buildingId };
  const lastSnap = useRef(0);
  // Tests (dev builds only): a manual clock instead of animation frames.
  const manualClock = useRef(false);

  const command = useCallback(
    (action: GuidanceCameraCommand['action'], extra: Partial<GuidanceCameraCommand> = {}) =>
      setCamera({ action, nonce: Date.now() + Math.random(), ...extra }),
    [],
  );

  const publish = useCallback(
    (force = false) => {
      const snap = sim.current.snapshot();
      const p = snap.progress;
      pose.set(
        p && snap.status !== 'idle'
          ? { floorId: p.floor_id, position: p.position, headingDeg: p.heading_deg }
          : null,
      );
      const now = performance.now();
      if (force || now - lastSnap.current >= SNAPSHOT_MS) {
        lastSnap.current = now;
        setSnapshot(snap);
      }
    },
    [pose],
  );

  const handle = useCallback(
    (events: SimEvent[]) => {
      for (const e of events) {
        if (e.type === 'instruction') latest.current.voice?.say(e.key, e.instruction.text);
      }
      if (events.length) publish(true);
    },
    [publish],
  );

  const load = useCallback(
    (next: RouteResponse | null, keepActive: boolean) => {
      latest.current.voice?.cancel();
      if (!next) {
        sim.current = new GuidanceSim();
        publish(true);
        return;
      }
      const ins = buildInstructions(next, latest.current.names);
      handle(sim.current.load(next, ins, keepActive));
      revisionAtLoad.current = next.revision;
      publish(true);
    },
    [handle, publish],
  );

  // A new route from the user or the assistant replaces the one being followed (stop first).
  useEffect(() => {
    if (route && route === ownRoute.current) return;
    if (!route) {
      // AriNav drops stale routes on a scenario change; guidance keeps its own until rerouted.
      const st = sim.current.snapshot().status;
      if (st === 'active' || st === 'paused' || st === 'recomputing') return;
    }
    token.current += 1;
    ownRoute.current = route;
    load(route, false);
  }, [route, load]);

  // A different building ends everything.
  useEffect(() => {
    token.current += 1;
    ownRoute.current = null;
    load(null, false);
  }, [buildingId, load]);

  // The animation loop runs only while walking or previewing.
  const status = snapshot.status;
  useEffect(() => {
    if (status !== 'active') return;
    let frame = 0;
    let last = performance.now();
    const step = (now: number) => {
      const dt = Math.min(0.25, (now - last) / 1000);
      last = now;
      if (!manualClock.current) {
        handle(sim.current.tick(dt));
        publish();
      }
      frame = requestAnimationFrame(step);
    };
    frame = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frame);
  }, [status, handle, publish]);

  // The shared scenario moved (Confirm, developer panel, another tab, the team agent): halt,
  // recompute from where the walker is, continue only with a valid route.
  useEffect(() => {
    const snap = sim.current.snapshot();
    const moving = snap.status === 'active' || snap.status === 'paused' || snap.status === 'recomputing';
    if (!moving || scenarioRevision === undefined || !snap.route || !snap.progress) return;
    if (revisionAtLoad.current === scenarioRevision) return;
    const id = latest.current.buildingId;
    const target = endpointOf(snap.route.target);
    if (!id || !target) return;
    const wasActive = snap.status === 'active';
    const mine = ++token.current;
    latest.current.voice?.cancel();
    sim.current.hold('The building changed · checking your route…');
    publish(true);
    const p = snap.progress;
    const s = latest.current.settings;
    const request: RouteRequest = {
      mode: snap.route.mode,
      start: { kind: 'point', space_id: p.space_id ?? snap.route.start?.space_id ?? '', position: p.position },
      target,
      step_free: s.step_free,
      ...(snap.route.mode === 'precision' && {
        profile: s.profile,
        width_m: s.width_m,
        safety_m: s.safety_m,
        snap_m: Math.max(s.snap_m, 0.5),
        allow_schematic_vertical: s.allow_schematic_vertical,
      }),
    };
    const affected = remainingElements(snap.route, snap.distance_m);
    void api
      .navRoute(id, request)
      .then((next) => {
        if (mine !== token.current) return; // a newer building/route/scenario state won
        if (next.status !== 'ok') {
          handle(sim.current.block(next.unreachable_reason || 'No route from here under the current closures.'));
          return;
        }
        ownRoute.current = next;
        const ins = buildInstructions(next, latest.current.names);
        handle(sim.current.load(next, ins, true));
        if (!wasActive) handle(sim.current.pause());
        revisionAtLoad.current = next.revision;
        latest.current.onRoute(next);
        void affected;
        publish(true);
      })
      .catch((e: unknown) => {
        if (mine !== token.current) return;
        const message =
          e instanceof ApiError ? e.message : 'The route could not be checked. Navigation stopped here.';
        handle(sim.current.block(`No route from here: ${message}`));
      });
  }, [scenarioRevision, handle, publish]);

  const start = useCallback(() => {
    if (previewing) command('cancel_preview');
    handle(sim.current.start());
    command('follow');
  }, [previewing, command, handle]);
  const pause = useCallback(() => handle(sim.current.pause()), [handle]);
  const resume = useCallback(() => handle(sim.current.resume()), [handle]);
  const stop = useCallback(() => {
    latest.current.voice?.cancel();
    handle(sim.current.stop());
    command('free');
  }, [handle, command]);
  const setSpeed = useCallback(
    (factor: number) => {
      sim.current.setSpeed(factor);
      publish(true);
    },
    [publish],
  );
  const preview = useCallback(() => {
    const r = sim.current.snapshot().route;
    if (!r || r.status !== 'ok') return;
    setPreviewing(true);
    command('preview', { route: r, durationMs: PREVIEW_MS });
  }, [command]);
  const skipPreview = useCallback(() => command('cancel_preview'), [command]);
  const previewDone = useCallback(() => setPreviewing(false), []);
  const overview = useCallback(() => command('overview'), [command]);
  const recenter = useCallback(() => command('follow'), [command]);

  useEffect(() => {
    if (!import.meta.env.DEV) return;
    const handleObj = {
      snapshot: () => sim.current.snapshot(),
      pose: () => pose.get(),
      manualClock: (on: boolean) => {
        manualClock.current = on;
      },
      advance: (seconds: number, step = 0.1) => {
        for (let t = 0; t < seconds - 1e-9; t += step) handle(sim.current.tick(Math.min(step, seconds - t)));
        publish(true);
        return sim.current.snapshot();
      },
    };
    (window as unknown as { __arinavGuidance?: typeof handleObj }).__arinavGuidance = handleObj;
    return () => {
      delete (window as unknown as { __arinavGuidance?: typeof handleObj }).__arinavGuidance;
    };
  }, [handle, publish, pose]);

  const ins = snapshot.instructions;
  const upcoming = snapshot.status === 'arrived' ? ins.at(-1) : ins[snapshot.next];
  return {
    snapshot,
    pose,
    camera,
    previewing,
    navigating: status === 'active' || status === 'paused' || status === 'recomputing',
    instruction: upcoming ?? null,
    toNextM: upcoming ? Math.max(0, upcoming.at_m - snapshot.distance_m) : null,
    canStart: !!snapshot.route && snapshot.route.status === 'ok',
    start,
    pause,
    resume,
    stop,
    setSpeed,
    preview,
    skipPreview,
    previewDone,
    overview,
    recenter,
  };
}
