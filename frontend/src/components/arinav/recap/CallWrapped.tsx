// Call Wrapped: the recap of the last conversations as a full-screen story (tap to advance,
// hold to pause, auto-advance, replay at the end). Content comes from `/api/recaps/{id}`.
import { AnimatePresence, MotionConfig, motion } from 'motion/react';
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type PointerEvent,
} from 'react';

import { api, type Recap } from '../../../lib/api';
import { CloseIcon, PauseIcon, PlayIcon, ReplayIcon } from '../Icons';
import { buildSlides } from './slides';
import '../../../styles/recap.css';

export const SLIDE_MS = 5000;
const POLL_MS = 1000;
const POLL_LIMIT = 90; // ~1.5 minutes before giving up
const HOLD_MS = 200;

interface Props {
  recapId: string;
  onClose: () => void;
  /** For tests: the recap fetcher (defaults to the API). */
  load?: (id: string) => Promise<Recap>;
}

/** Fills the current bar over `duration` (paused while `paused`) and calls `onDone` at the end. */
function Progress({
  count,
  index,
  paused,
  duration,
  onDone,
}: {
  count: number;
  index: number;
  paused: boolean;
  duration: number;
  onDone: () => void;
}) {
  const [fill, setFill] = useState(0);
  const elapsed = useRef(0);
  const done = useRef(onDone);
  done.current = onDone;

  useEffect(() => {
    elapsed.current = 0;
    setFill(0);
  }, [index]);

  useEffect(() => {
    if (paused) return;
    let last = performance.now();
    let frame = requestAnimationFrame(function tick(now) {
      elapsed.current += now - last;
      last = now;
      const value = Math.min(1, elapsed.current / duration);
      setFill(value);
      if (value >= 1) done.current();
      else frame = requestAnimationFrame(tick);
    });
    return () => cancelAnimationFrame(frame);
  }, [paused, duration, index]);

  return (
    <div className="wrapped__progress" aria-hidden="true">
      {Array.from({ length: count }, (_, i) => (
        <span key={i} className="wrapped__progress-track">
          <span
            className="wrapped__progress-fill"
            style={{ transform: `scaleX(${i < index ? 1 : i === index ? fill : 0})` }}
          />
        </span>
      ))}
    </div>
  );
}

export default function CallWrapped({ recapId, onClose, load = api.recap }: Props) {
  const [recap, setRecap] = useState<Recap | null>(null);
  const [failed, setFailed] = useState(false);
  const [index, setIndex] = useState(0);
  const [direction, setDirection] = useState(1);
  const [paused, setPaused] = useState(false);
  const [held, setHeld] = useState(false);
  const [run, setRun] = useState(0); // bumps on replay so every animation restarts
  const dialog = useRef<HTMLDivElement>(null);
  const press = useRef<{ at: number; timer: number } | null>(null);

  // Load the recap; while it is generated, ask again every second.
  useEffect(() => {
    let alive = true;
    let tries = 0;
    let timer = 0;
    setRecap(null);
    setFailed(false);
    const poll = async () => {
      try {
        const value = await load(recapId);
        if (!alive) return;
        if (value.status === 'ready') return setRecap(value);
        if (value.status === 'failed') return setFailed(true);
      } catch {
        // The backend may be restarting; keep asking until the limit.
      }
      if (!alive) return;
      if (++tries >= POLL_LIMIT) return setFailed(true);
      timer = window.setTimeout(() => void poll(), POLL_MS);
    };
    void poll();
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [recapId, load]);

  useEffect(() => dialog.current?.focus(), []);

  const replay = useCallback(() => {
    setDirection(-1);
    setIndex(0);
    setPaused(false);
    setRun((r) => r + 1);
  }, []);

  const slides = useMemo(() => {
    if (!recap) return [];
    const actions = (
      <>
        <button className="wrapped__btn wrapped__btn--primary" onClick={replay}>
          <ReplayIcon width={16} height={16} /> Replay
        </button>
        <button className="wrapped__btn" onClick={onClose}>
          Done
        </button>
      </>
    );
    return buildSlides(recap, actions);
  }, [recap, replay, onClose]);
  const last = slides.length - 1;
  const slide = slides[Math.min(index, last)];

  const go = useCallback(
    (step: number) => {
      setDirection(step);
      setIndex((i) => Math.max(0, Math.min(last, i + step)));
    },
    [last],
  );

  const onKey = (event: KeyboardEvent) => {
    if (event.key === 'Escape') onClose();
    else if (event.key === 'ArrowRight' || event.key === ' ') {
      event.preventDefault();
      go(1);
    } else if (event.key === 'ArrowLeft') go(-1);
  };

  // Tap: left third goes back, the rest forward. Holding pauses until released.
  const down = (event: PointerEvent) => {
    if ((event.target as Element).closest('button')) return;
    const timer = window.setTimeout(() => setHeld(true), HOLD_MS);
    press.current = { at: event.clientX, timer };
  };
  const up = (event: PointerEvent) => {
    const start = press.current;
    press.current = null;
    if (!start) return;
    window.clearTimeout(start.timer);
    if (held) return setHeld(false);
    const box = event.currentTarget.getBoundingClientRect();
    go(start.at - box.left < box.width / 3 ? -1 : 1);
  };
  const cancel = () => {
    if (press.current) window.clearTimeout(press.current.timer);
    press.current = null;
    setHeld(false);
  };

  return (
    <MotionConfig reducedMotion="user">
      <div className="wrapped" role="presentation">
        <div
          ref={dialog}
          className={`wrapped__frame wrapped__frame--${slide?.tone ?? 'navy'}`}
          role="dialog"
          aria-modal="true"
          aria-label="Call Wrapped"
          tabIndex={-1}
          onKeyDown={onKey}
        >
          {slide ? (
            <>
              <Progress
                key={run}
                count={slides.length}
                index={index}
                paused={paused || held || index === last}
                duration={SLIDE_MS}
                onDone={() => go(1)}
              />
              <div className="wrapped__controls">
                <button
                  className="wrapped__icon"
                  onClick={() => setPaused((p) => !p)}
                  aria-label={paused ? 'Play' : 'Pause'}
                  disabled={index === last}
                >
                  {paused ? <PlayIcon /> : <PauseIcon />}
                </button>
                <button className="wrapped__icon" onClick={onClose} aria-label="Close recap">
                  <CloseIcon />
                </button>
              </div>
              <div
                className="wrapped__stage"
                onPointerDown={down}
                onPointerUp={up}
                onPointerCancel={cancel}
                onPointerLeave={cancel}
              >
                <AnimatePresence initial={false} custom={direction}>
                  <motion.section
                    key={`${run}-${slide.id}`}
                    className="wrapped__slide"
                    aria-roledescription="slide"
                    aria-label={`${index + 1} of ${slides.length}: ${slide.label}`}
                    custom={direction}
                    initial={{ opacity: 0, x: direction * 48 }}
                    animate={{ opacity: 1, x: 0 }}
                    exit={{ opacity: 0, x: direction * -48 }}
                    transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
                  >
                    {slide.body}
                  </motion.section>
                </AnimatePresence>
              </div>
              <p className="wrapped__sr" aria-live="polite">
                {`Slide ${index + 1} of ${slides.length}: ${slide.label}`}
              </p>
            </>
          ) : (
            <div className="wrapped__loading" role="status">
              <button
                className="wrapped__icon wrapped__loading-close"
                onClick={onClose}
                aria-label="Close recap"
              >
                <CloseIcon />
              </button>
              {failed ? (
                <>
                  <p className="wrapped__title">Your recap isn't available right now.</p>
                  <button className="wrapped__btn wrapped__btn--primary" onClick={onClose}>
                    Close
                  </button>
                </>
              ) : (
                <>
                  <span className="wrapped__spinner" aria-hidden="true" />
                  <p className="wrapped__kicker">Call Wrapped</p>
                  <p className="wrapped__title">Wrapping up your conversations…</p>
                </>
              )}
            </div>
          )}
        </div>
      </div>
    </MotionConfig>
  );
}
