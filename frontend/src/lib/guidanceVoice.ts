// Spoken turn-by-turn hints: one utterance at a time, newest pending wins, stale audio is
// cancelled on route changes, and audio failures never reach the caller (navigation goes on).
// Framework-free; the audio side is an injected port (see createSpeechPort in ./voice).

export interface SpeechPort {
  /** Text to base64 16-bit PCM; should reject (or resolve, ignored) once `signal` aborts. */
  synthesize(text: string, speed: number, signal: AbortSignal): Promise<{ pcm: string; rate: number }>;
  /** Resolves when playback finished or `signal` aborted. */
  play(pcm: string, rate: number, signal: AbortSignal): Promise<void>;
  /** Stops any audio of this port immediately. */
  stop(): void;
}

export interface GuidanceVoiceState {
  speaking: boolean;
  muted: boolean;
  speed: number;
  last: string | null;
  error: string | null;
}

export interface GuidanceVoiceOptions {
  speed?: number;
  muted?: boolean;
  onChange?: (state: GuidanceVoiceState) => void;
}

export const GUIDANCE_SPEED = { min: 0.7, max: 1.2, default: 1.1 } as const;
const MAX_KEYS = 500;
const MAX_CACHED = 64;

export function clampSpeed(speed: number): number {
  if (!Number.isFinite(speed)) return GUIDANCE_SPEED.default;
  const clamped = Math.min(GUIDANCE_SPEED.max, Math.max(GUIDANCE_SPEED.min, speed));
  return Math.round(clamped * 100) / 100;
}

function shortError(error: unknown): string {
  const message = error instanceof Error ? error.message : typeof error === 'string' ? error : '';
  const text = message.trim() || 'Spoken hint failed.';
  return text.length > 120 ? `${text.slice(0, 117)}...` : text;
}

export class GuidanceVoice {
  private readonly port: SpeechPort;
  private readonly onChange?: (state: GuidanceVoiceState) => void;
  private current: AbortController | null = null;
  private pending: string | null = null;
  private suspended = false;
  private readonly keys = new Set<string>();
  private readonly cache = new Map<string, { pcm: string; rate: number }>();
  private snapshot: GuidanceVoiceState;

  constructor(port: SpeechPort, options: GuidanceVoiceOptions = {}) {
    this.port = port;
    this.onChange = options.onChange;
    this.snapshot = {
      speaking: false,
      muted: options.muted ?? false,
      speed: clampSpeed(options.speed ?? GUIDANCE_SPEED.default),
      last: null,
      error: null,
    };
  }

  /** Immutable snapshot; replaced (new object) on every change. */
  get state(): GuidanceVoiceState {
    return this.snapshot;
  }

  /**
   * Speaks `text` once per `key` (e.g. `${routeRevision}:${waypointIndex}`). While speaking, it
   * becomes the single pending utterance (replacing an older pending one). Muted: remembered as
   * `last` (for repeat) but silent. Suspended: ignored entirely (the key is not consumed).
   */
  say(key: string, text: string): void {
    const clean = text.trim();
    if (this.suspended || !clean || this.keys.has(key)) return;
    this.keys.add(key);
    if (this.keys.size > MAX_KEYS) this.keys.delete(this.keys.values().next().value as string);
    this.update({ last: clean });
    if (this.snapshot.muted) return;
    if (this.current) this.pending = clean;
    else this.start(clean);
  }

  /** Speaks the last text again right away (interrupting); does nothing when muted/suspended. */
  repeat(): void {
    const last = this.snapshot.last;
    if (!last || this.snapshot.muted || this.suspended) return;
    this.halt();
    this.start(last);
  }

  /** Stops the current utterance and drops the pending one (route or destination changed). */
  cancel(): void {
    this.halt();
    this.update({ speaking: false });
  }

  setMuted(muted: boolean): void {
    if (muted) this.halt();
    this.update({ muted, ...(muted && { speaking: false }) });
  }

  setSpeed(speed: number): void {
    this.update({ speed: clampSpeed(speed) });
  }

  /** E.g. while push-to-talk records: stops playback, drops pending, ignores say() until resume. */
  suspend(): void {
    this.suspended = true;
    this.cancel();
  }

  resume(): void {
    this.suspended = false;
  }

  private halt(): void {
    this.pending = null;
    const current = this.current;
    this.current = null;
    if (current) {
      current.abort();
      try {
        this.port.stop();
      } catch {
        // Port errors never reach callers.
      }
    }
  }

  private start(text: string): void {
    const controller = new AbortController();
    this.current = controller;
    this.update({ speaking: true });
    void this.run(text, controller);
  }

  private async run(text: string, controller: AbortController): Promise<void> {
    const { signal } = controller;
    const speed = this.snapshot.speed;
    const cacheKey = `${speed}|${text}`;
    let error: string | null | undefined;
    try {
      let audio = this.cache.get(cacheKey);
      if (!audio) {
        audio = await this.port.synthesize(text, speed, signal);
        if (signal.aborted) return;
        this.remember(cacheKey, audio);
      }
      await this.port.play(audio.pcm, audio.rate, signal);
      if (!signal.aborted) error = null;
    } catch (e) {
      if (!signal.aborted) error = shortError(e);
    } finally {
      this.finish(controller, error);
    }
  }

  private finish(controller: AbortController, error: string | null | undefined): void {
    if (this.current !== controller) {
      // Superseded (cancel, mute, repeat): only a late error report is dropped.
      return;
    }
    this.current = null;
    const next = this.pending;
    this.pending = null;
    const chain = next !== null && !this.snapshot.muted && !this.suspended;
    // Chaining straight into the pending utterance keeps `speaking` true (no flicker).
    const patch: Partial<GuidanceVoiceState> = chain ? {} : { speaking: false };
    if (error !== undefined) patch.error = error;
    this.update(patch);
    if (chain) this.start(next);
  }

  private remember(key: string, audio: { pcm: string; rate: number }): void {
    this.cache.set(key, audio);
    if (this.cache.size > MAX_CACHED) this.cache.delete(this.cache.keys().next().value as string);
  }

  private update(patch: Partial<GuidanceVoiceState>): void {
    const next = { ...this.snapshot, ...patch };
    const changed = (Object.keys(next) as (keyof GuidanceVoiceState)[]).some(
      (k) => next[k] !== this.snapshot[k],
    );
    if (!changed) return;
    this.snapshot = next;
    try {
      this.onChange?.(next);
    } catch {
      // A listener error must not break the voice queue.
    }
  }
}
