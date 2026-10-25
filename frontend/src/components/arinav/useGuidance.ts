// Live guidance in AriNav (Phase B): owns the deterministic GuidanceSim, drives it with
// requestAnimationFrame, publishes the walker pose per frame (to the 3D view, without re-rendering
// the app) and a throttled snapshot (to the panels), and reroutes when the shared scenario changes.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, ApiError, type Endpoint, type RouteRequest, type RouteResponse } from '../../lib/api';
import type { NavSettings } from '../../lib/nav';
import {
  buildInstructions,
  GuidanceSim,
  readable,
  projectOnRoute,
  type Instruction,
  type Names,
  type SimEvent,
  type SimSnapshot,
} from '../../lib/simulation';
import type { MemoryRecorder } from '../../lib/memory';
import type { WalkerPose } from './scene';
import type { GuidanceCameraCommand } from './Viewer3D';

/** Per-frame walker pose, read by the 3D view outside React rendering. */
export interface PoseStore {
  get(): WalkerPose | null;
  subscribe(listener: () => void): () => void;
}

export interface GuidanceVoicePort {
  say(key: string, text: string, priority?: number): void;
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
  /** Manual walking: the person's pose, or null to give the walker back to the clock. */
  setExternal: (pose: ExternalPose | null) => void;
}

const SNAPSHOT_MS = 100;
/** Manual walking: farther than this from the route replans (m), at most every OFF_ROUTE_REPLAN_MS. */
export const OFF_ROUTE_M = 2.5;
const OFF_ROUTE_REPLAN_MS = 2500;
/** Manual walking: this close to the route's end point arrives (m). */
export const ARRIVE_M = 1.0;

/** A manually moved person's pose (building frame; heading = view direction, deg CCW from +X). */
export interface ExternalPose {
  floor_id: string;
  space_id: string;
  position: [number, number];
  heading_deg: number;
}
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
  /** Opt-in activity memory (showcase): fed with actual walking only, never previews. */
  memory?: MemoryRecorder | null;
}): Guidance {
  const { buildingId, route, names, scenarioRevision, settings, onRoute, voice, memory } = args;
  const sim = useRef(new GuidanceSim());
  const pose = useMemo(createPoseStore, []);
  const [snapshot, setSnapshot] = useState<SimSnapshot>(() => sim.current.snapshot());
  const [camera, setCamera] = useState<GuidanceCameraCommand | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const token = useRef(0); // bumped by every newer state; async answers for older ones are dropped
  const ownRoute = useRef<RouteResponse | null>(null); // the route guidance itself installed
  // A manually moved person (useImmersive); while set, the clock does not move the walker.
  const external = useRef<ExternalPose | null>(null);
  const revisionAtLoad = useRef<number | undefined>(undefined);
  const latest = useRef({ names, settings, onRoute, voice, buildingId, memory });
  latest.current = { names, settings, onRoute, voice, buildingId, memory };
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
      latest.current.memory?.observe([], snap);
      const p = snap.progress;
      const ext = external.current;
      pose.set(
        ext
          ? { floorId: ext.floor_id, position: ext.position, headingDeg: ext.heading_deg, distanceM: snap.distance_m }
          : p && snap.status !== 'idle'
            ? { floorId: p.floor_id, position: p.position, headingDeg: p.heading_deg, distanceM: p.distance_m }
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
      if (events.length) latest.current.memory?.observe(events, sim.current.snapshot(), latest.current.names);
      for (const e of events) {
        if (e.type === 'instruction') {
          const k = e.instruction.kind;
          const priority = k === 'arrive' ? 3 : k === 'stairs' || k === 'lift' || k === 'exit' ? 2 : 0;
          latest.current.voice?.say(e.key, e.instruction.text, priority);
        }
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

  // A different building ends everything (not the first mount: a route may already be loaded).
  const shownBuilding = useRef(buildingId);
  useEffect(() => {
    if (shownBuilding.current === buildingId) return;
    shownBuilding.current = buildingId;
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
      if (!manualClock.current && !external.current) {
        handle(sim.current.tick(dt));
        publish();
      }
      frame = requestAnimationFrame(step);
    };
    frame = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frame);
  }, [status, handle, publish]);

  // Recompute from a point (the walker or the manually moved person), keep the followed route's
  // constraints (only ever tightened by the UI), continue only with a valid route.
  const reroute = useCallback(
    (from: { space_id: string | null; position: [number, number] }, message: string) => {
      const snap = sim.current.snapshot();
      const id = latest.current.buildingId;
      const target = snap.route ? endpointOf(snap.route.target) : null;
      if (!id || !target || !snap.route) return;
      const wasActive = snap.status === 'active';
      const route = snap.route;
      const mine = ++token.current;
      latest.current.voice?.cancel();
      sim.current.hold(message);
      publish(true);
      const ui = latest.current.settings;
      const c = route.constraints;
      const s = {
        ...ui,
        step_free: !!(c?.step_free || ui.step_free),
        profile: c?.profile ?? ui.profile,
        width_m: Math.max(c?.width_m ?? 0, ui.width_m),
        safety_m: Math.max(c?.safety_m ?? 0, ui.safety_m),
      };
      const request: RouteRequest = {
        mode: route.mode,
        start: { kind: 'point', space_id: from.space_id ?? route.start?.space_id ?? '', position: from.position },
        target,
        step_free: s.step_free,
        // Keep the person where they are: only the first leg is re-walked from this exact point.
        exact_start: true,
        ...(route.mode === 'precision' && {
          profile: s.profile,
          width_m: s.width_m,
          safety_m: s.safety_m,
          snap_m: Math.max(s.snap_m, 0.5),
          allow_schematic_vertical: s.allow_schematic_vertical,
        }),
      };
      void api
        .navRoute(id, request)
        .then((next) => {
          if (mine !== token.current) return; // a newer building/route/scenario state won
          if (next.status !== 'ok') {
            const why = next.unreachable_reason
              ? readable(next.unreachable_reason, latest.current.names)
              : 'not possible under the current closures';
            handle(sim.current.block(`No route from here: ${why}.`));
            return;
          }
          ownRoute.current = next;
          const ins = buildInstructions(next, latest.current.names);
          handle(sim.current.load(next, ins, true));
          if (!wasActive && !external.current) handle(sim.current.pause());
          revisionAtLoad.current = next.revision;
          latest.current.onRoute(next);
          publish(true);
        })
        .catch((e: unknown) => {
          if (mine !== token.current) return;
          const text =
            e instanceof ApiError ? e.message : 'The route could not be checked. Navigation stopped here.';
          handle(sim.current.block(`No route from here: ${readable(text, latest.current.names)}`));
        });
    },
    [handle, publish],
  );

  // The shared scenario moved (Confirm, developer panel, another tab, the team agent): halt and
  // recompute from where the person is.
  useEffect(() => {
    const snap = sim.current.snapshot();
    const moving = snap.status === 'active' || snap.status === 'paused' || snap.status === 'recomputing';
    if (!moving || scenarioRevision === undefined || !snap.route || !snap.progress) return;
    if (revisionAtLoad.current === scenarioRevision) return;
    const ext = external.current;
    const from = ext
      ? { space_id: ext.space_id, position: ext.position }
      : { space_id: snap.progress.space_id, position: snap.progress.position };
    reroute(from, 'The building changed · checking your route…');
  }, [scenarioRevision, reroute]);

  // Manual walking: the person (not the clock) moves. Their pose is shown as is; progress along
  // the route comes from projecting it; more than OFF_ROUTE_M away replans from where they stand.
  const lastOffRoute = useRef(0);
  const setExternal = useCallback(
    (next: ExternalPose | null) => {
      external.current = next;
      publish(!next); // pose per frame via the store; panels stay throttled
      if (!next) return;
      const snap = sim.current.snapshot();
      if (!snap.route || snap.route.status !== 'ok' || snap.status === 'recomputing') return;
      if (snap.status === 'arrived' || snap.status === 'blocked') return;
      const end = snap.route.waypoints?.at(-1);
      const atEnd =
        !!end &&
        end.floor_id === next.floor_id &&
        Math.hypot(end.position[0] - next.position[0], end.position[1] - next.position[1]) <= ARRIVE_M;
      if (atEnd) {
        handle(sim.current.track(snap.total_m));
        return;
      }
      const proj = projectOnRoute(snap.route, next.floor_id, next.position);
      if (proj && proj.offset_m <= OFF_ROUTE_M) {
        // Progress may not jump far ahead where a route loops back close to itself.
        handle(sim.current.track(Math.min(proj.distance_m, snap.distance_m + 4)));
        return;
      }
      const now = performance.now();
      if (now - lastOffRoute.current < OFF_ROUTE_REPLAN_MS) return;
      lastOffRoute.current = now;
      reroute({ space_id: next.space_id, position: next.position }, 'Off the route · finding a way from here…');
    },
    [handle, publish, reroute],
  );

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
    setExternal,
  };
}
