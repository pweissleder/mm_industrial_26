// Immersive navigation (night task §5/§6): POV vs. overview camera, autowalk vs. manual driver,
// keyboard/mouse/touch input, device orientation, and the relative direction arrow.
// Three separate quantities: route geometry (backend), user pose (walker or manual person) and
// camera pose (POV look / overview). The camera never moves the person; only one driver moves it.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { BuildingModel } from '../../lib/building';
import type { Features } from '../../lib/features';
import { progressAt } from '../../lib/guidance';
import { projectOnRoute } from '../../lib/simulation';
import { angleDiff, arrowRotation, deviceLook, headingTo, smoothAngle, wrap360 } from '../../lib/spatial';
import { buildWorld, connectorsHere, locate, move, ride, type WalkState } from '../../lib/walkable';
import type { ExternalPose, Guidance } from './useGuidance';

export type CameraView = 'overview' | 'pov';
export type Driver = 'autowalk' | 'manual';
export type OrientationStatus = 'off' | 'unsupported' | 'insecure' | 'denied' | 'waiting' | 'active';

/** What the 3D view needs per frame for the POV camera (read outside React rendering). */
export interface LookState {
  view: CameraView;
  /** Absolute view heading (deg CCW from +X), or null = follow the walker's heading + offset. */
  yaw: number | null;
  yawOffset: number;
  pitch: number;
  /** Sideways tilt of the phone (deg, + = top edge to the right); 0 without device orientation. */
  roll?: number;
}
export interface LookStore {
  get(): LookState;
  subscribe(listener: () => void): () => void;
}

const WALK_MPS = 3; // default walking speed (m/s); adjustable 1-8, Shift doubles it
const SPEED_KEY = 'arinav:walk-mps';
const readSpeed = () => {
  const v = typeof localStorage !== 'undefined' ? Number(localStorage.getItem(SPEED_KEY)) : NaN;
  return Number.isFinite(v) && v >= 1 && v <= 8 ? v : WALK_MPS;
};
const TURN_DPS = 100;
const LOOK_DEG_PER_PX = 0.25;
const MAX_PITCH = 60;
// The phone may look almost straight up and down (ceiling, floor); the camera stays defined.
const MAX_DEVICE_PITCH = 88;

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

export interface RideOption {
  connectorId: string;
  floorId: string;
  floorName: string;
  kind: string;
  usable: boolean;
  reason: string | null;
  level: number;
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
  /** Keyboard/joystick walking speed in m/s (1-8, default 3; Shift doubles it). */
  walkSpeed: number;
  setWalkSpeed: (mps: number) => void;
  /** The next floor up/down reachable from here (stairs or lift), for E/Q and the HUD prompt. */
  rideOptions: { up: RideOption | null; down: RideOption | null };
  /** Take the stairs/lift one floor up (+1) or down (-1); false if nothing usable. */
  rideStep: (dir: 1 | -1) => boolean;
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
  // Calibration: at `azimuth0` (deviceLook) the person faced building heading `heading`.
  const deviceCal = useRef<{ azimuth0: number; heading: number } | null>(null);
  const lastAzimuth = useRef<number | null>(null);
  const smoothed = useRef<number | null>(null);
  const smoothedTilt = useRef<{ pitch: number; roll: number } | null>(null);
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

  // Walking speed for the keyboard / joystick (remembered locally).
  const [walkSpeed, setWalkSpeedState] = useState(readSpeed);
  const walkSpeedRef = useRef(walkSpeed);
  walkSpeedRef.current = walkSpeed;
  const setWalkSpeed = useCallback((mps: number) => {
    const v = Math.max(1, Math.min(8, mps));
    setWalkSpeedState(v);
    try {
      localStorage.setItem(SPEED_KEY, String(v));
    } catch {
      /* private mode: not remembered */
    }
  }, []);

  // Without `game` (user mode, phones) the pose still exists for first person and phone rotation,
  // but nothing moves it except the route walker: the phone itself is the first-person screen.
  const startManual = useCallback((): string | null => {
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
      // E / Page Up: stairs or lift one floor up; Q / Page Down: one floor down (where you stand)
      if (pose.current && ['e', 'q', 'PageUp', 'PageDown'].includes(k) && !e.repeat) {
        if (rideStepRef.current(k === 'e' || k === 'PageUp' ? 1 : -1)) e.preventDefault();
        return;
      }
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
          const speed = walkSpeedRef.current * (k.has('Shift') ? 2 : 1) * dt;
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

  // Device orientation: permission only from a user gesture; rotation, never position. All three
  // axes (deviceLook): turning, looking up/down to ceiling and floor, and sideways tilt.
  const onOrientation = useCallback(
    (e: DeviceOrientationEvent) => {
      if (e.alpha === null || e.alpha === undefined || e.beta === null || e.gamma === null) return;
      const angle = typeof screen !== 'undefined' && screen.orientation ? screen.orientation.angle : 0;
      const device = deviceLook(e.alpha, e.beta ?? 90, e.gamma ?? 0, angle);
      lastAzimuth.current = device.azimuth;
      if (!deviceCal.current) {
        deviceCal.current = { azimuth0: device.azimuth, heading: viewHeading() ?? 90 };
        setOrientation('active');
      }
      const heading = wrap360(deviceCal.current.heading + angleDiff(deviceCal.current.azimuth0, device.azimuth));
      smoothed.current = smoothAngle(smoothed.current, heading, 0.3);
      const pitch = Math.max(-MAX_DEVICE_PITCH, Math.min(MAX_DEVICE_PITCH, device.pitch));
      const tilt = smoothedTilt.current;
      smoothedTilt.current = tilt
        ? { pitch: tilt.pitch + (pitch - tilt.pitch) * 0.3, roll: tilt.roll + (device.roll - tilt.roll) * 0.3 }
        : { pitch, roll: device.roll };
      updateLook({ yaw: smoothed.current, yawOffset: 0, ...smoothedTilt.current });
      if (pose.current) pose.current.heading = smoothed.current;
    },
    [viewHeading, updateLook],
  );
  // One stable listener: re-renders change onOrientation, and removing/adding per render lost events.
  const orientationHandler = useRef(onOrientation);
  orientationHandler.current = onOrientation;
  const orientationListener = useCallback((e: DeviceOrientationEvent) => orientationHandler.current(e), []);

  const enableOrientation = useCallback(async () => {
    if (!features.orientation) return;
    const Ctor = (typeof window !== 'undefined' ? window.DeviceOrientationEvent : undefined) as
      | (typeof DeviceOrientationEvent & { requestPermission?: () => Promise<'granted' | 'denied'> })
      | undefined;
    if (!Ctor) {
      setOrientation('unsupported');
      return;
    }
    // Phone browsers block motion sensors on plain http pages (iOS then denies the permission).
    if (typeof window !== 'undefined' && window.isSecureContext === false) {
      setOrientation('insecure');
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
    smoothedTilt.current = null;
    setOrientation('waiting'); // becomes 'active' with the first real event, never before
    window.addEventListener('deviceorientation', orientationListener);
  }, [features.orientation, orientationListener]);

  const calibrate = useCallback(() => {
    // "I now face the direction shown": re-anchor the device to the current walking direction.
    const p = latest.current.guidance.pose.get();
    const target = pose.current?.heading ?? p?.headingDeg ?? 90;
    if (lastAzimuth.current !== null) {
      deviceCal.current = { azimuth0: lastAzimuth.current, heading: target };
      smoothed.current = target;
      updateLook({ yaw: target, yawOffset: 0 });
    }
  }, [updateLook]);

  const disableOrientation = useCallback(() => {
    window.removeEventListener('deviceorientation', orientationListener);
    deviceCal.current = null;
    smoothed.current = null;
    smoothedTilt.current = null;
    setOrientation('off');
    updateLook({ yaw: pose.current ? pose.current.heading : null, yawOffset: 0, pitch: -8, roll: 0 });
  }, [orientationListener, updateLook]);

  useEffect(() => () => window.removeEventListener('deviceorientation', orientationListener), [orientationListener]);

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

  // Next floor up/down from here: nearest level in that direction, usable rides first.
  const rideOptions = useMemo(() => {
    const out: { up: RideOption | null; down: RideOption | null } = { up: null, down: null };
    if (!model || !manualPose || !rides.length) return out;
    const level = new Map(model.floors.map((f) => [f.id, f.level]));
    const here = level.get(manualPose.floor_id) ?? 0;
    for (const r of rides)
      for (const f of r.floors) {
        const l = level.get(f.id);
        if (l === undefined || l === here) continue;
        const key = l > here ? 'up' : 'down';
        const option: RideOption = { connectorId: r.id, floorId: f.id, floorName: f.name, kind: r.kind, usable: r.usable, reason: r.reason, level: l };
        const best = out[key];
        const better =
          !best ||
          (option.usable && !best.usable) ||
          (option.usable === best.usable && Math.abs(l - here) < Math.abs(best.level - here));
        if (better) out[key] = option;
      }
    return out;
  }, [model, manualPose, rides]);
  const rideStep = useCallback(
    (dir: 1 | -1) => {
      const option = dir > 0 ? rideOptions.up : rideOptions.down;
      if (!option || !option.usable) return false;
      rideTo(option.connectorId, option.floorId);
      return true;
    },
    [rideOptions, rideTo],
  );
  const rideStepRef = useRef(rideStep);
  rideStepRef.current = rideStep;

  useEffect(() => {
    if (!import.meta.env.DEV) return;
    const handle = {
      look: () => look.get(),
      pose: () => (pose.current ? { ...pose.current } : null),
      driver: () => (pose.current ? 'manual' : 'autowalk'),
      orientation: () => orientationRef.current,
      viewHeading: () => viewHeading(),
    };
    (window as unknown as { __arinavImmersive?: typeof handle }).__arinavImmersive = handle;
  }, [look, viewHeading]);
  const orientationRef = useRef(orientation);
  orientationRef.current = orientation;

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
    walkSpeed,
    setWalkSpeed,
    rideOptions,
    rideStep,
    manualPose,
    viewHeading,
  };
}
