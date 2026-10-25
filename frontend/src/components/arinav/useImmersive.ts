// Immersive navigation (night task §5/§6): POV vs. overview camera, autowalk vs. manual driver,
// keyboard/mouse/touch input, device orientation, and the relative direction arrow.
// Three separate quantities: route geometry (backend), user pose (walker or manual person) and
// camera pose (POV look / overview). The camera never moves the person; only one driver moves it.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { BuildingModel } from '../../lib/building';
import type { Features } from '../../lib/features';
import { progressAt } from '../../lib/guidance';
import { projectOnRoute } from '../../lib/simulation';
import { arrowRotation, deviceHeading, headingTo, smoothAngle, wrap360 } from '../../lib/spatial';
import { buildWorld, connectorsHere, locate, move, ride, type WalkState } from '../../lib/walkable';
import type { ExternalPose, Guidance } from './useGuidance';

export type CameraView = 'overview' | 'pov';
export type Driver = 'autowalk' | 'manual';
export type OrientationStatus = 'off' | 'unsupported' | 'denied' | 'waiting' | 'active';

/** What the 3D view needs per frame for the POV camera (read outside React rendering). */
export interface LookState {
  view: CameraView;
  /** Absolute view heading (deg CCW from +X), or null = follow the walker's heading + offset. */
  yaw: number | null;
  yawOffset: number;
  pitch: number;
}
export interface LookStore {
  get(): LookState;
  subscribe(listener: () => void): () => void;
}

const WALK_MPS = 1.4;
const TURN_DPS = 100;
const LOOK_DEG_PER_PX = 0.25;
const MAX_PITCH = 60;

const isTyping = (el: EventTarget | null) =>
  el instanceof HTMLInputElement ||
  el instanceof HTMLTextAreaElement ||
  el instanceof HTMLSelectElement ||
  (el instanceof HTMLElement && el.isContentEditable);

function createLookStore(initial: LookState): LookStore & { set(next: LookState): void } {
  let state = initial;
  const listeners = new Set<() => void>();
  return {
    get: () => state,
    set(next) {
      state = next;
      listeners.forEach((l) => l());
    },
    subscribe(l) {
      listeners.add(l);
      return () => listeners.delete(l);
    },
  };
}

export interface Immersive {
  view: CameraView;
  driver: Driver;
  look: LookStore;
  setView: (view: CameraView) => void;
  /** Start manual walking from the current pose; false (with a reason) if there is none. */
  startManual: () => string | null;
  stopManual: () => void;
  /** Mouse/touch drag in POV. */
  dragLook: (dx: number, dy: number) => void;
  /** Joystick vector (-1..1): x = strafe right, y = forward. */
  setStick: (x: number, y: number) => void;
  resetLook: () => void;
  orientation: OrientationStatus;
  enableOrientation: () => Promise<void>;
  calibrate: () => void;
  disableOrientation: () => void;
  /** Screen rotation (deg, clockwise) of the "go this way" arrow relative to the view. */
  arrowDeg: number | null;
  /** Stairs/lifts usable from where the manual person stands. */
  rides: { id: string; name: string; kind: string; floors: { id: string; name: string }[]; usable: boolean; reason: string | null }[];
  rideTo: (connectorId: string, floorId: string) => void;
  manualPose: (ExternalPose & { blocked?: boolean }) | null;
  /** Current view heading (deg) or null if unknown (for spatial questions). */
  viewHeading: () => number | null;
}

export function useImmersive(args: {
  features: Features;
  model: BuildingModel | null;
  closed: readonly string[];
  stepFree: boolean;
  guidance: Guidance;
  /** A known location to start manual walking from when no walker is shown. */
  location: { space_id: string; position: [number, number] | null } | null;
}): Immersive {
  const { features, model, closed, stepFree, guidance, location } = args;
  const [view, setViewState] = useState<CameraView>('overview');
  const [driver, setDriver] = useState<Driver>('autowalk');
  const [manualPose, setManualPose] = useState<(ExternalPose & { blocked?: boolean }) | null>(null);
  const [orientation, setOrientation] = useState<OrientationStatus>('off');
  const [arrowDeg, setArrowDeg] = useState<number | null>(null);
  const look = useMemo(() => createLookStore({ view: 'overview', yaw: null, yawOffset: 0, pitch: -8 }), []);
  const world = useMemo(() => (model ? buildWorld(model, closed) : null), [model, closed]);
  const keys = useRef(new Set<string>());
  const stick = useRef<[number, number]>([0, 0]);
  const pose = useRef<(WalkState & { heading: number }) | null>(null);
  const deviceCal = useRef<{ alpha0: number; heading: number; screenAngle0: number } | null>(null);
  const lastAlpha = useRef<number | null>(null);
  const smoothed = useRef<number | null>(null);
  const latest = useRef({ guidance, stepFree, world });
  latest.current = { guidance, stepFree, world };

  const updateLook = useCallback((patch: Partial<LookState>) => look.set({ ...look.get(), ...patch }), [look]);

  const setView = useCallback(
    (next: CameraView) => {
      if (next === 'pov' && !features.pov) return;
      setViewState(next);
      updateLook({ view: next });
    },
    [features.pov, updateLook],
  );

  const viewHeading = useCallback((): number | null => {
    const l = look.get();
    if (l.yaw !== null) return wrap360(l.yaw);
    const p = latest.current.guidance.pose.get();
    return p ? wrap360(p.headingDeg + l.yawOffset) : null;
  }, [look]);

  const lastUiPose = useRef(0);
  const publishManual = useCallback((force = false) => {
    const p = pose.current;
    if (!p) return;
    const ext: ExternalPose = { floor_id: p.floor_id, space_id: p.space_id, position: p.position, heading_deg: p.heading };
    latest.current.guidance.setExternal(ext); // the 3D view reads this per frame via the pose store
    const now = performance.now();
    if (force || now - lastUiPose.current > 100) {
      lastUiPose.current = now; // panels: 10 Hz is enough
      setManualPose(ext);
    }
  }, []);

  const startManual = useCallback((): string | null => {
    if (!features.game) return 'Manual walking is switched off in this profile.';
    const w = latest.current.world;
    if (!w) return 'Open a building first.';
    const walker = latest.current.guidance.pose.get();
    let start: WalkState | null = null;
    let heading = viewHeading() ?? 0;
    if (walker) {
      const space = locate(w, walker.floorId, walker.position);
      if (space) start = { floor_id: walker.floorId, space_id: space, position: walker.position };
      heading = walker.headingDeg;
    } else if (location) {
      const s = w.spaces.get(location.space_id);
      if (s) {
        const position: [number, number] = location.position ?? [
          s.poly.reduce((a, q) => a + q[0], 0) / s.poly.length,
          s.poly.reduce((a, q) => a + q[1], 0) / s.poly.length,
        ];
        start = { floor_id: s.floor, space_id: s.id, position };
      }
    }
    if (!start) return 'Set your location first (or start a route), then walk.';
    latest.current.guidance.pause();
    pose.current = { ...start, heading };
    setDriver('manual');
    updateLook({ yaw: heading, yawOffset: 0 });
    publishManual(true);
    return null;
  }, [features.game, location, viewHeading, updateLook, publishManual]);

  const stopManual = useCallback(() => {
    pose.current = null;
    keys.current.clear();
    stick.current = [0, 0];
    setDriver('autowalk');
    setManualPose(null);
    latest.current.guidance.setExternal(null);
    updateLook({ yaw: deviceCal.current ? look.get().yaw : null, yawOffset: 0 });
  }, [look, updateLook]);

  const dragLook = useCallback(
    (dx: number, dy: number) => {
      if (deviceCal.current) return; // the phone owns the heading while orientation is on
      const l = look.get();
      const pitch = Math.max(-MAX_PITCH, Math.min(MAX_PITCH, l.pitch - dy * LOOK_DEG_PER_PX));
      // Dragging right turns right (clockwise) = heading decreases.
      if (l.yaw !== null) updateLook({ yaw: wrap360(l.yaw - dx * LOOK_DEG_PER_PX), pitch });
      else updateLook({ yawOffset: l.yawOffset - dx * LOOK_DEG_PER_PX, pitch });
      if (pose.current && l.yaw !== null) pose.current.heading = wrap360(l.yaw - dx * LOOK_DEG_PER_PX);
    },
    [look, updateLook],
  );

  const resetLook = useCallback(() => {
    if (pose.current) updateLook({ pitch: -8 });
    else updateLook({ yaw: deviceCal.current ? look.get().yaw : null, yawOffset: 0, pitch: -8 });
  }, [look, updateLook]);

  const setStick = useCallback((x: number, y: number) => {
    stick.current = [Math.max(-1, Math.min(1, x)), Math.max(-1, Math.min(1, y))];
  }, []);

  // Keyboard: WASD/arrows only while walking manually or in POV, never while typing; ESC frees the
  // pointer lock (the browser does that itself) and stops manual movement keys.
  useEffect(() => {
    if (!features.game) return;
    const down = (e: KeyboardEvent) => {
      if (isTyping(e.target) || e.ctrlKey || e.metaKey || e.altKey) return;
      const k = e.key.length === 1 ? e.key.toLowerCase() : e.key;
      if (!['w', 'a', 's', 'd', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Shift'].includes(k)) {
        if (k === 'Escape') keys.current.clear();
        return;
      }
      if (!pose.current) return;
      keys.current.add(k);
      e.preventDefault();
    };
    const up = (e: KeyboardEvent) => {
      const k = e.key.length === 1 ? e.key.toLowerCase() : e.key;
      keys.current.delete(k);
    };
    const blur = () => keys.current.clear();
    window.addEventListener('keydown', down);
    window.addEventListener('keyup', up);
    window.addEventListener('blur', blur);
    return () => {
      window.removeEventListener('keydown', down);
      window.removeEventListener('keyup', up);
      window.removeEventListener('blur', blur);
    };
  }, [features.game]);

  // Manual movement loop: frame-rate independent (dt), collision-checked in sub-steps.
  useEffect(() => {
    if (driver !== 'manual') return;
    let frame = 0;
    let last = performance.now();
    const step = (now: number) => {
      const dt = Math.min(0.1, (now - last) / 1000);
      last = now;
      const p = pose.current;
      const w = latest.current.world;
      if (p && w) {
        const k = keys.current;
        const turn = (k.has('ArrowLeft') ? 1 : 0) - (k.has('ArrowRight') ? 1 : 0);
        if (turn) {
          p.heading = wrap360(p.heading + turn * TURN_DPS * dt);
          updateLook({ yaw: p.heading });
        }
        if (look.get().yaw !== null) p.heading = look.get().yaw as number;
        const fwd = (k.has('w') || k.has('ArrowUp') ? 1 : 0) - (k.has('s') || k.has('ArrowDown') ? 1 : 0) + stick.current[1];
        const right = (k.has('d') ? 1 : 0) - (k.has('a') ? 1 : 0) + stick.current[0];
        if (fwd || right) {
          const speed = WALK_MPS * (k.has('Shift') ? 2 : 1) * dt;
          const h = (p.heading * Math.PI) / 180;
          const f = Math.max(-1, Math.min(1, fwd));
          const r = Math.max(-1, Math.min(1, right));
          const delta: [number, number] = [
            (Math.cos(h) * f + Math.sin(h) * r) * speed,
            (Math.sin(h) * f - Math.cos(h) * r) * speed,
          ];
          const result = move(w, p, delta);
          pose.current = { ...result.state, heading: p.heading };
          publishManual();
        } else if (turn) publishManual();
      }
      frame = requestAnimationFrame(step);
    };
    frame = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frame);
  }, [driver, look, updateLook, publishManual]);

  // A different building ends manual walking.
  useEffect(() => {
    if (pose.current) stopManual();
  }, [model?.building.id]); // eslint-disable-line react-hooks/exhaustive-deps

  // Device orientation: permission only from a user gesture; rotation, never position.
  const onOrientation = useCallback(
    (e: DeviceOrientationEvent) => {
      if (e.alpha === null || e.alpha === undefined) return;
      lastAlpha.current = e.alpha;
      const angle = typeof screen !== 'undefined' && screen.orientation ? screen.orientation.angle : 0;
      if (!deviceCal.current) {
        deviceCal.current = { alpha0: e.alpha, heading: viewHeading() ?? 90, screenAngle0: angle };
        setOrientation('active');
      }
      const heading = deviceHeading(e.alpha, deviceCal.current, angle);
      smoothed.current = smoothAngle(smoothed.current, heading, 0.3);
      updateLook({ yaw: smoothed.current, yawOffset: 0 });
      if (pose.current) pose.current.heading = smoothed.current;
    },
    [viewHeading, updateLook],
  );

  const enableOrientation = useCallback(async () => {
    if (!features.orientation) return;
    const Ctor = (typeof window !== 'undefined' ? window.DeviceOrientationEvent : undefined) as
      | (typeof DeviceOrientationEvent & { requestPermission?: () => Promise<'granted' | 'denied'> })
      | undefined;
    if (!Ctor) {
      setOrientation('unsupported');
      return;
    }
    if (typeof Ctor.requestPermission === 'function') {
      try {
        if ((await Ctor.requestPermission()) !== 'granted') {
          setOrientation('denied');
          return;
        }
      } catch {
        setOrientation('denied');
        return;
      }
    }
    deviceCal.current = null;
    smoothed.current = null;
    setOrientation('waiting'); // becomes 'active' with the first real event, never before
    window.addEventListener('deviceorientation', onOrientation);
  }, [features.orientation, onOrientation]);

  const calibrate = useCallback(() => {
    // "I now face the direction shown": re-anchor the device to the current walking direction.
    const p = latest.current.guidance.pose.get();
    const target = pose.current?.heading ?? p?.headingDeg ?? 90;
    if (lastAlpha.current !== null) {
      const angle = typeof screen !== 'undefined' && screen.orientation ? screen.orientation.angle : 0;
      deviceCal.current = { alpha0: lastAlpha.current, heading: target, screenAngle0: angle };
      smoothed.current = target;
      updateLook({ yaw: target, yawOffset: 0 });
    }
  }, [updateLook]);

  const disableOrientation = useCallback(() => {
    window.removeEventListener('deviceorientation', onOrientation);
    deviceCal.current = null;
    smoothed.current = null;
    setOrientation('off');
    updateLook({ yaw: pose.current ? pose.current.heading : null, yawOffset: 0 });
  }, [onOrientation, updateLook]);

  useEffect(() => () => window.removeEventListener('deviceorientation', onOrientation), [onOrientation]);

  // Arrow: from the person towards a point a few metres ahead on the route, relative to the view.
  useEffect(() => {
    const timer = window.setInterval(() => {
      const g = latest.current.guidance;
      const snap = g.snapshot;
      const p = g.pose.get();
      const heading = viewHeading();
      if (!snap.route || snap.route.status !== 'ok' || !p || heading === null) {
        setArrowDeg(null);
        return;
      }
      const proj = projectOnRoute(snap.route, p.floorId, p.position);
      const base = proj && proj.offset_m <= 2.5 ? proj.distance_m : snap.distance_m;
      const ahead = progressAt(snap.route, base + 3);
      if (!ahead) return setArrowDeg(null);
      const point = ahead.floor_id === p.floorId ? ahead.position : snap.route.waypoints?.find((w) => w.distance_m >= base && w.floor_id === p.floorId)?.position;
      if (!point || Math.hypot(point[0] - p.position[0], point[1] - p.position[1]) < 0.2) return setArrowDeg(null);
      setArrowDeg(arrowRotation(heading, headingTo(p.position, point)));
    }, 150);
    return () => window.clearInterval(timer);
  }, [viewHeading]);

  const rides = useMemo(() => {
    if (!world || !manualPose || !model) return [];
    const floors = new Map(model.floors.map((f) => [f.id, f.name]));
    return connectorsHere(world, { floor_id: manualPose.floor_id, space_id: manualPose.space_id, position: manualPose.position }, stepFree).map((c) => ({
      id: c.id,
      name: c.name,
      kind: c.kind,
      usable: c.usable,
      reason: c.reason,
      floors: c.stops.filter((s) => s.floor !== manualPose.floor_id).map((s) => ({ id: s.floor, name: floors.get(s.floor) ?? s.floor })),
    }));
  }, [world, manualPose, model, stepFree]);

  const rideTo = useCallback(
    (connectorId: string, floorId: string) => {
      const p = pose.current;
      const w = latest.current.world;
      if (!p || !w) return;
      const next = ride(w, p, connectorId, floorId, latest.current.stepFree);
      if (!next) return;
      pose.current = { ...next, heading: p.heading };
      publishManual(true);
    },
    [publishManual],
  );

  return {
    view,
    driver,
    look,
    setView,
    startManual,
    stopManual,
    dragLook,
    setStick,
    resetLook,
    orientation,
    enableOrientation,
    calibrate,
    disableOrientation,
    arrowDeg,
    rides,
    rideTo,
    manualPose,
    viewHeading,
  };
}
