// Immersive navigation controls (night task §5/§6), isolated from the shared layout files:
// a small mode bar (overview/POV, autowalk/walk yourself, mouse lock, phone rotation), the POV HUD
// (next step + relative arrow, "simulated" badge), a drag-to-look layer and a touch joystick.
import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent, type ReactNode } from 'react';
import type { Features } from '../../lib/features';
import type { Guidance } from './useGuidance';
import type { Immersive } from './useImmersive';

const ORIENTATION_TEXT: Record<string, string> = {
  off: 'Phone rotation',
  unsupported: 'Rotation not available',
  denied: 'Rotation not allowed',
  waiting: 'Waiting for sensor…',
  active: 'Rotation on',
};

export function ImmersiveBar(props: {
  immersive: Immersive;
  guidance: Guidance;
  features: Features;
  onNotice: (text: string) => void;
  /** Extra controls of other modules (e.g. the session memory chip). */
  children?: ReactNode;
}) {
  const { immersive: im, features } = props;
  const pov = im.view === 'pov';
  const manual = im.driver === 'manual';
  return (
    <div className="immersive-bar card" role="toolbar" aria-label="Navigation view">
      {features.pov && (
        <div className="segmented segmented--small" role="group" aria-label="Camera">
          <button className="segmented__item" aria-pressed={!pov} onClick={() => im.setView('overview')}>
            Overview
          </button>
          <button
            className="segmented__item"
            aria-pressed={pov}
            onClick={() => {
              if (!props.guidance.pose.get() && !manual) {
                const why = im.startManual();
                if (why) return props.onNotice(why);
              }
              im.setView('pov');
            }}
          >
            First person
          </button>
        </div>
      )}
      {features.game && (
        <button
          className={`chip${manual ? ' chip--on' : ''}`}
          aria-pressed={manual}
          onClick={() => {
            if (manual) return im.stopManual();
            const why = im.startManual();
            if (why) props.onNotice(why);
          }}
          title="Walk yourself with WASD / arrow keys or the joystick"
        >
          {manual ? 'Walking yourself' : 'Walk myself'}
        </button>
      )}
      {features.orientation && pov && (
        <>
          <button
            className={`chip${im.orientation === 'active' ? ' chip--on' : ''}`}
            onClick={() => (im.orientation === 'active' || im.orientation === 'waiting' ? im.disableOrientation() : void im.enableOrientation())}
            title="Turn your phone to look around (rotation only, not position)"
          >
            {ORIENTATION_TEXT[im.orientation]}
          </button>
          {im.orientation === 'active' && (
            <button className="chip" onClick={im.calibrate} title="Hold the phone in the walking direction, then tap">
              Calibrate
            </button>
          )}
        </>
      )}
      {manual &&
        im.rides.map((r) =>
          r.floors.map((f) => (
            <button
              key={`${r.id}-${f.id}`}
              className="chip"
              disabled={!r.usable}
              title={r.reason === 'not step-free' ? 'Not step-free' : r.reason === 'closed' ? 'Closed' : undefined}
              onClick={() => im.rideTo(r.id, f.id)}
            >
              {r.kind === 'lift' ? 'Lift' : 'Stairs'} to {f.name}
              {r.usable ? '' : ` · ${r.reason}`}
            </button>
          )),
        )}
      {props.children}
    </div>
  );
}

/** POV overlay: next step, relative arrow and a clear "simulated" label. */
export function PovHud(props: { immersive: Immersive; guidance: Guidance; manualHelp: boolean; split?: boolean }) {
  const { immersive: im, guidance: g } = props;
  if (im.view !== 'pov') return null;
  const ins = g.instruction;
  const moving = g.snapshot.route && g.snapshot.route.status === 'ok';
  return (
    <div className={`pov-hud${props.split ? ' pov-hud--split' : ''}`} aria-live="polite">
      <span className="pov-hud__badge">Simulated walkthrough</span>
      {moving && (
        <div className="pov-hud__step">
          {im.arrowDeg !== null && (
            <span
              className="pov-hud__arrow"
              style={{ transform: `rotate(${im.arrowDeg}deg)` }}
              aria-label="Direction to go"
              data-deg={Math.round(im.arrowDeg)}
            >
              ↑
            </span>
          )}
          <span className="pov-hud__text">
            {g.snapshot.status === 'arrived' ? 'You have arrived.' : (ins?.text ?? 'Follow the line.')}
          </span>
        </div>
      )}
      {props.manualHelp && <span className="pov-hud__help">WASD / arrows to walk · drag to look · Esc frees the mouse</span>}
    </div>
  );
}

/** Captures drags over the 3D view in POV to look around; optional pointer lock on request. */
export function LookLayer(props: { immersive: Immersive }) {
  const { immersive: im } = props;
  const ref = useRef<HTMLDivElement>(null);
  const last = useRef<[number, number] | null>(null);
  const [locked, setLocked] = useState(false);
  useEffect(() => {
    // Only while this layer really holds the pointer lock (null === null must not count).
    const onLock = () => setLocked(!!ref.current && document.pointerLockElement === ref.current);
    const onMove = (e: MouseEvent) => {
      if (ref.current && document.pointerLockElement === ref.current) im.dragLook(e.movementX, e.movementY);
    };
    document.addEventListener('pointerlockchange', onLock);
    document.addEventListener('mousemove', onMove);
    return () => {
      document.removeEventListener('pointerlockchange', onLock);
      document.removeEventListener('mousemove', onMove);
    };
  }, [im]);
  if (im.view !== 'pov') return null;
  const down = (e: ReactPointerEvent) => {
    last.current = [e.clientX, e.clientY];
    (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
  };
  const moveLook = (e: ReactPointerEvent) => {
    if (!last.current || locked) return;
    im.dragLook(e.clientX - last.current[0], e.clientY - last.current[1]);
    last.current = [e.clientX, e.clientY];
  };
  return (
    <div
      ref={ref}
      className="look-layer"
      onPointerDown={down}
      onPointerMove={moveLook}
      onPointerUp={() => (last.current = null)}
      onPointerCancel={() => (last.current = null)}
      onDoubleClick={() => void ref.current?.requestPointerLock?.()}
      title="Drag to look around · double-click to lock the mouse (Esc to release)"
      aria-label="Drag to look around"
    />
  );
}

/** Touch joystick for manual walking (forward/back + strafe); thumb-sized, bottom-left. */
export function TouchJoystick(props: { immersive: Immersive }) {
  const { immersive: im } = props;
  const base = useRef<HTMLDivElement>(null);
  const [knob, setKnob] = useState<[number, number]>([0, 0]);
  // Touch screens only: on a desktop the keyboard walks and the stick would cover the panel.
  const touch = typeof window !== 'undefined' && window.matchMedia?.('(pointer: coarse)').matches;
  if (im.driver !== 'manual' || !touch) return null;
  const update = (e: ReactPointerEvent) => {
    const r = base.current?.getBoundingClientRect();
    if (!r) return;
    const radius = r.width / 2;
    let x = (e.clientX - (r.left + radius)) / radius;
    let y = (e.clientY - (r.top + radius)) / radius;
    const len = Math.hypot(x, y);
    if (len > 1) {
      x /= len;
      y /= len;
    }
    setKnob([x, y]);
    im.setStick(x, -y);
  };
  const release = () => {
    setKnob([0, 0]);
    im.setStick(0, 0);
  };
  return (
    <div
      ref={base}
      className="joystick"
      onPointerDown={(e) => {
        (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
        update(e);
      }}
      onPointerMove={(e) => e.buttons && update(e)}
      onPointerUp={release}
      onPointerCancel={release}
      aria-label="Walk joystick"
    >
      <span className="joystick__knob" style={{ transform: `translate(${knob[0] * 32}px, ${knob[1] * 32}px)` }} />
    </div>
  );
}
