// Immersive navigation controls (night task §5/§6), isolated from the shared layout files:
// a small mode bar (overview/POV, autowalk/walk yourself, mouse lock, phone rotation), the POV HUD
// (next step + relative arrow, "simulated" badge), a drag-to-look layer and a touch joystick.
import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent, type ReactNode } from 'react';
import type { Features } from '../../lib/features';
import { CubeIcon, EyeIcon, PhoneRotateIcon } from './Icons';
import type { Guidance } from './useGuidance';
import type { Immersive } from './useImmersive';

const ORIENTATION_TEXT: Record<string, string> = {
  off: 'Phone rotation',
  unsupported: 'Rotation not available',
  insecure: 'Rotation needs HTTPS',
  denied: 'Rotation not allowed',
  waiting: 'Waiting for sensor…',
  active: 'Rotation on',
};

/** How long the opened view switch waits for a choice before folding again. */
const SWITCH_OPEN_MS = 4000;

function useMedia(query: string): boolean {
  const [value, setValue] = useState(() => typeof window !== 'undefined' && window.matchMedia(query).matches);
  useEffect(() => {
    const mq = window.matchMedia(query);
    const update = () => setValue(mq.matches);
    mq.addEventListener('change', update);
    return () => mq.removeEventListener('change', update);
  }, [query]);
  return value;
}

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
  // The view switch is folded to the current view's icon; hover (mouse), a click or a tap unfolds
  // it to choose; a choice, Escape or a click elsewhere folds it again.
  const touch = useMedia('(pointer: coarse)');
  const hover = useMedia('(hover: hover)');
  const [open, setOpen] = useState(false);
  const switchRef = useRef<HTMLDivElement>(null);
  // Leaving with the mouse folds after a moment, so the unfolding never snaps away under it.
  const leaveTimer = useRef<number | undefined>(undefined);
  const hoverOpen = () => {
    window.clearTimeout(leaveTimer.current);
    setOpen(true);
  };
  const hoverLeave = () => {
    window.clearTimeout(leaveTimer.current);
    leaveTimer.current = window.setTimeout(() => setOpen(false), 450);
  };
  useEffect(() => {
    if (!open) return;
    const timer = window.setTimeout(() => setOpen(false), SWITCH_OPEN_MS);
    const outside = (e: PointerEvent) => {
      if (!switchRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const escape = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    window.addEventListener('pointerdown', outside);
    window.addEventListener('keydown', escape);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener('pointerdown', outside);
      window.removeEventListener('keydown', escape);
    };
  }, [open]);
  const choose = (view: 'overview' | 'pov') => {
    setOpen(false);
    if (view === 'overview') {
      // user view: the still first-person pose ends with first person
      if (!features.game && manual) im.stopManual();
      im.setView('overview');
      return;
    }
    if (!props.guidance.pose.get() && !manual) {
      const why = im.startManual();
      if (why) return props.onNotice(why);
    }
    im.setView('pov');
  };
  const rotating = im.orientation === 'active' || im.orientation === 'waiting';
  return (
    <div className="immersive-bar card" role="toolbar" aria-label="Navigation view">
      {features.pov && (
        // Folded to the current view's icon; the choice opens as a small menu below it, so
        // nothing moves under the pointer.
        <div
          ref={switchRef}
          className={`view-switch${open ? ' view-switch--open' : ''}`}
          role="group"
          aria-label="Camera"
          onMouseEnter={hover ? hoverOpen : undefined}
          onMouseLeave={hover ? hoverLeave : undefined}
        >
          <button
            className="icon-btn icon-btn--sm view-switch__anchor"
            aria-label="Change view"
            aria-haspopup="true"
            aria-expanded={open}
            title={`View: ${pov ? 'First person' : 'Overview'}`}
            onClick={() => setOpen((o) => (hover ? true : !o))}
          >
            {pov ? <EyeIcon width={16} height={16} /> : <CubeIcon width={16} height={16} />}
          </button>
          {open && (
            <div className="view-switch__menu card">
              <button className="view-switch__item" aria-pressed={!pov} aria-label="Overview" onClick={() => choose('overview')}>
                <CubeIcon width={16} height={16} />
                <span>Overview</span>
              </button>
              <button className="view-switch__item" aria-pressed={pov} aria-label="First person" onClick={() => choose('pov')}>
                <EyeIcon width={16} height={16} />
                <span>First person</span>
              </button>
            </div>
          )}
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
      {props.guidance.canStart && (manual || !['active', 'recomputing'].includes(props.guidance.snapshot.status)) && (
        <button
          className="chip"
          onClick={() => {
            // Hand the person back to the route walker, from where they stand.
            if (manual) im.stopManual();
            const status = props.guidance.snapshot.status;
            if (status === 'paused') props.guidance.resume();
            else if (status !== 'active') props.guidance.start();
          }}
          title="Walk the route automatically (Pause and Stop in the navigation card)"
        >
          Auto-walk
        </button>
      )}
      {features.game && manual && (
        <label className="chip immersive-bar__speed" title="Walking speed (Shift doubles it)">
          Speed
          <input
            type="range"
            min={1}
            max={8}
            step={0.5}
            value={im.walkSpeed}
            aria-label="Walking speed in metres per second"
            onChange={(e) => im.setWalkSpeed(Number(e.target.value))}
          />
          <span>{im.walkSpeed.toFixed(1)} m/s</span>
        </label>
      )}
      {features.orientation && pov && touch && im.orientation !== 'unsupported' && (
        <>
          <button
            className="icon-btn icon-btn--sm immersive-bar__rotate"
            aria-pressed={rotating}
            aria-label={ORIENTATION_TEXT[im.orientation]}
            onClick={() => (rotating ? im.disableOrientation() : void im.enableOrientation())}
            title={`${ORIENTATION_TEXT[im.orientation]}: turn your phone to look around (rotation only, not position)`}
          >
            <PhoneRotateIcon width={18} height={18} />
          </button>
          {im.orientation === 'active' && (
            <button className="chip" onClick={im.calibrate} title="Hold the phone in the walking direction, then tap">
              Calibrate
            </button>
          )}
        </>
      )}
      {features.game &&
        manual &&
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

/** POV overlay: next step, relative arrow and a clear "simulated" label. `manualHelp`: walking
 * yourself (developer), with key help and the stairs/lift prompt; the user view has neither. */
export function PovHud(props: { immersive: Immersive; guidance: Guidance; manualHelp: boolean; split?: boolean }) {
  const { immersive: im, guidance: g } = props;
  if (im.view !== 'pov') return null;
  const ins = g.instruction;
  const moving = g.snapshot.route && g.snapshot.route.status === 'ok';
  return (
    <div className={`pov-hud${props.split ? ' pov-hud--split' : ''}`} aria-live="polite">
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
      {props.manualHelp && <RidePrompt immersive={im} />}
      {props.manualHelp && <span className="pov-hud__help">WASD / arrows to walk · drag to look · E / Q stairs and lifts · Esc frees the mouse</span>}
    </div>
  );
}

/** Standing at stairs or a lift while walking yourself: say so, with keys and tap targets. */
function RidePrompt(props: { immersive: Immersive }) {
  const { up, down } = props.immersive.rideOptions;
  if (!up && !down) return null;
  const what = (up ?? down)?.kind === 'lift' ? 'Lift' : 'Stairs';
  const row = (o: NonNullable<typeof up>, dir: 1 | -1, key: string) =>
    o.usable ? (
      <button className="btn btn--primary btn--sm" onClick={() => props.immersive.rideStep(dir)}>
        {dir > 0 ? '▲' : '▼'} {o.floorName} <kbd>{key}</kbd>
      </button>
    ) : (
      <span className="pov-hud__ride-off">
        {dir > 0 ? '▲' : '▼'} {o.floorName}: {o.reason === 'not step-free' ? 'not step-free' : 'closed'}
      </span>
    );
  return (
    <div className="pov-hud__ride" role="group" aria-label={`${what} here`}>
      <span className="pov-hud__ride-title">{what} here</span>
      {up && row(up, 1, 'E')}
      {down && row(down, -1, 'Q')}
    </div>
  );
}

/** Captures drags over the 3D view in POV to look around; optional pointer lock on request. */
export function LookLayer(props: { immersive: Immersive; split?: boolean }) {
  const { immersive: im } = props;
  const ref = useRef<HTMLDivElement>(null);
  const last = useRef<[number, number] | null>(null);
  const start = useRef<[number, number] | null>(null);
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
    start.current = [e.clientX, e.clientY];
    (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
  };
  // A click or tap (no drag) goes through to the 3D view: things in sight open their details.
  const up = (e: ReactPointerEvent) => {
    last.current = null;
    const s = start.current;
    start.current = null;
    const el = ref.current;
    if (!s || !el || locked || Math.hypot(e.clientX - s[0], e.clientY - s[1]) > 6) return;
    el.style.pointerEvents = 'none';
    const under = document.elementFromPoint(e.clientX, e.clientY);
    el.style.pointerEvents = '';
    if (!(under instanceof HTMLCanvasElement)) return;
    const init = { clientX: e.clientX, clientY: e.clientY, bubbles: true, pointerType: e.pointerType, pointerId: e.pointerId };
    under.dispatchEvent(new PointerEvent('pointerdown', init));
    under.dispatchEvent(new PointerEvent('pointerup', init));
  };
  const moveLook = (e: ReactPointerEvent) => {
    if (!last.current || locked) return;
    im.dragLook(e.clientX - last.current[0], e.clientY - last.current[1]);
    last.current = [e.clientX, e.clientY];
  };
  return (
    <div
      ref={ref}
      className={`look-layer${props.split ? ' look-layer--split' : ''}`}
      onPointerDown={down}
      onPointerMove={moveLook}
      onPointerUp={up}
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
      <span className="joystick__knob" style={{ transform: `translate(${knob[0] * 22}px, ${knob[1] * 22}px)` }} />
    </div>
  );
}
