// Push-to-talk recording (AudioWorklet, public/recorder.js) to a 16 kHz mono WAV, and
// playback of the assistant's 16-bit PCM replies and spoken guidance hints. Pure codecs are
// exported for tests. All playback registers in one module-level registry so push-to-talk can
// silence everything before the microphone opens (it must never record our own speech).

import { api } from './api';
import type { SpeechPort } from './guidanceVoice';

export const STT_RATE = 16000;
export const MAX_SECONDS = 10;
const MIN_SECONDS = 0.3;

/** Downsample float chunks (any rate) to 16 kHz mono 16-bit WAV, base64, at most `maxSeconds`. */
export function encodeWav(chunks: Float32Array[], rate: number, maxSeconds = MAX_SECONDS): string {
  const size = chunks.reduce((s, c) => s + c.length, 0);
  const samples = new Float32Array(size);
  let offset = 0;
  for (const c of chunks) {
    samples.set(c, offset);
    offset += c.length;
  }
  const ratio = rate / STT_RATE;
  const count = Math.min(STT_RATE * maxSeconds, Math.floor(size / ratio));
  const bytes = new ArrayBuffer(44 + count * 2);
  const view = new DataView(bytes);
  const text = (at: number, v: string) => {
    for (let i = 0; i < v.length; i++) view.setUint8(at + i, v.charCodeAt(i));
  };
  text(0, 'RIFF');
  view.setUint32(4, 36 + count * 2, true);
  text(8, 'WAVE');
  text(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // mono
  view.setUint32(24, STT_RATE, true);
  view.setUint32(28, STT_RATE * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  text(36, 'data');
  view.setUint32(40, count * 2, true);
  for (let i = 0; i < count; i++) {
    const a = Math.floor(i * ratio);
    const b = Math.max(a + 1, Math.floor((i + 1) * ratio));
    let sum = 0;
    for (let j = a; j < b && j < size; j++) sum += samples[j];
    const v = Math.max(-1, Math.min(1, sum / (b - a)));
    view.setInt16(44 + i * 2, v < 0 ? v * 32768 : v * 32767, true);
  }
  const u8 = new Uint8Array(bytes);
  let binary = '';
  for (let i = 0; i < u8.length; i += 16384) {
    binary += String.fromCharCode(...u8.subarray(i, i + 16384));
  }
  return btoa(binary);
}

/** base64 16-bit little-endian PCM to floats in [-1, 1). */
export function decodePcm16(encoded: string): Float32Array {
  const raw = atob(encoded);
  const out = new Float32Array(Math.floor(raw.length / 2));
  for (let i = 0; i < out.length; i++) {
    let v = raw.charCodeAt(i * 2) | (raw.charCodeAt(i * 2 + 1) << 8);
    if (v >= 32768) v -= 65536;
    out[i] = v / 32768;
  }
  return out;
}

// --- global playback registry -----------------------------------------------------------
const players = new Set<() => void>();
/** Recorders that are starting or recording; guidance playback stays silent meanwhile. */
const microphones = new Set<object>();

/** Stops every audio we are playing (chat replies and guidance hints). */
export function stopAllPlayback(): void {
  for (const stop of [...players]) {
    try {
      stop();
    } catch {
      // Already stopped.
    }
  }
}

/** True while push-to-talk is starting or recording. */
export const microphoneActive = () => microphones.size > 0;

export const voiceSupported = () =>
  typeof window !== 'undefined' &&
  typeof window.AudioContext === 'function' &&
  typeof window.AudioWorkletNode === 'function' &&
  !!navigator.mediaDevices?.getUserMedia;

interface Recording {
  stream: MediaStream;
  source: MediaStreamAudioSourceNode;
  node: AudioWorkletNode;
  gain: GainNode;
  chunks: Float32Array[];
  frames: number;
  rate: number;
  timer: number;
}

/** One AudioContext for recording and playback (created on the first user gesture). */
export class VoiceIO {
  private context: AudioContext | null = null;
  private workletReady = false;
  private recording: Recording | null = null;
  private playing: AudioBufferSourceNode | null = null;
  /** Set while a start is pending, so a quick release cancels it. */
  private held = false;

  get isRecording() {
    return this.recording !== null;
  }

  private async audio(): Promise<AudioContext> {
    this.context ??= new AudioContext();
    if (this.context.state === 'suspended') await this.context.resume();
    return this.context;
  }

  /** Starts recording; `onLimit` fires when the 10 s limit stops it. */
  async start(onLimit: () => void): Promise<boolean> {
    if (this.recording) return true;
    this.held = true;
    microphones.add(this);
    let stream: MediaStream | undefined;
    try {
      const ctx = await this.audio();
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
      });
      if (!this.held) {
        stream.getTracks().forEach((t) => t.stop());
        microphones.delete(this);
        return false;
      }
      if (!this.workletReady) {
        await ctx.audioWorklet.addModule('/recorder.js');
        this.workletReady = true;
      }
      const source = ctx.createMediaStreamSource(stream);
      const node = new AudioWorkletNode(ctx, 'synera-recorder');
      const gain = ctx.createGain();
      gain.gain.value = 0;
      const rec: Recording = {
        stream,
        source,
        node,
        gain,
        chunks: [],
        frames: 0,
        rate: ctx.sampleRate,
        timer: window.setTimeout(onLimit, MAX_SECONDS * 1000),
      };
      this.recording = rec;
      node.port.onmessage = (e: MessageEvent<Float32Array>) => {
        if (this.recording !== rec) return;
        rec.chunks.push(e.data);
        rec.frames += e.data.length;
      };
      source.connect(node);
      node.connect(gain);
      gain.connect(ctx.destination);
      return true;
    } catch (error) {
      stream?.getTracks().forEach((t) => t.stop());
      this.held = false;
      microphones.delete(this);
      throw error;
    }
  }

  /** Stops recording: null when nothing was recording, else the WAV (base64; null if too short). */
  stop(): { wav: string | null } | null {
    this.held = false;
    const rec = this.recording;
    if (!rec) {
      // A start still awaiting the microphone cleans itself up when it sees `held` is false.
      return null;
    }
    microphones.delete(this);
    this.recording = null;
    window.clearTimeout(rec.timer);
    rec.source.disconnect();
    rec.node.disconnect();
    rec.node.port.close();
    rec.gain.disconnect();
    rec.stream.getTracks().forEach((t) => t.stop());
    if (rec.frames < rec.rate * MIN_SECONDS) return { wav: null };
    return { wav: encodeWav(rec.chunks, rec.rate) };
  }

  async play(encoded: string, rate: number): Promise<void> {
    this.stopPlayback();
    const ctx = await this.audio();
    const samples = decodePcm16(encoded);
    if (!samples.length) return;
    const buffer = ctx.createBuffer(1, samples.length, rate);
    buffer.getChannelData(0).set(samples);
    const source = ctx.createBufferSource();
    source.buffer = buffer;
    source.connect(ctx.destination);
    this.playing = source;
    const stop = () => this.stopPlayback();
    players.add(stop);
    try {
      await new Promise<void>((resolve) => {
        source.onended = () => resolve();
        source.start();
      });
    } finally {
      players.delete(stop);
      if (this.playing === source) this.playing = null;
    }
  }

  stopPlayback() {
    try {
      this.playing?.stop();
    } catch {
      // Already stopped.
    }
    this.playing = null;
  }

  dispose() {
    this.stop();
    this.stopPlayback();
    void this.context?.close();
    this.context = null;
    this.workletReady = false;
  }
}

// --- guidance hints -----------------------------------------------------------------------
const RESUME_TIMEOUT_MS = 1500;

export interface GuidanceSpeechPort extends SpeechPort {
  /** Creates/resumes the AudioContext; call from a user gesture (autoplay policy). */
  unlock(): void;
  /** Closes the AudioContext. */
  dispose(): void;
}

/** SpeechPort over `api.speak` with its own abortable AudioContext playback. */
export function createSpeechPort(): GuidanceSpeechPort {
  let context: AudioContext | null = null;
  let source: AudioBufferSourceNode | null = null;
  let finish: (() => void) | null = null;

  const stop = () => {
    const node = source;
    source = null;
    try {
      node?.stop();
    } catch {
      // Already stopped.
    }
    finish?.();
  };

  const audio = () => (context ??= new AudioContext());

  /** Resumes the context, giving up on abort or when the browser blocks autoplay. */
  const resumed = async (signal: AbortSignal): Promise<AudioContext> => {
    const ctx = audio();
    if (ctx.state !== 'suspended') return ctx;
    let timer = 0;
    let onAbort = () => {};
    try {
      await Promise.race([
        ctx.resume(),
        new Promise<never>((_, reject) => {
          timer = window.setTimeout(
            () => reject(new Error('Audio is blocked: tap the page to enable spoken hints.')),
            RESUME_TIMEOUT_MS,
          );
        }),
        new Promise<void>((resolve) => {
          onAbort = resolve;
          signal.addEventListener('abort', onAbort, { once: true });
        }),
      ]);
    } finally {
      window.clearTimeout(timer);
      signal.removeEventListener('abort', onAbort);
    }
    return ctx;
  };

  return {
    async synthesize(text, speed, signal) {
      const response = await api.speak(text, speed, signal);
      return { pcm: response.audio_pcm16, rate: response.sample_rate };
    },

    async play(pcm, rate, signal) {
      if (signal.aborted || microphoneActive()) return;
      stop();
      const ctx = await resumed(signal);
      if (signal.aborted || microphoneActive()) return;
      const samples = decodePcm16(pcm);
      if (!samples.length) return;
      const buffer = ctx.createBuffer(1, samples.length, rate);
      buffer.getChannelData(0).set(samples);
      const node = ctx.createBufferSource();
      node.buffer = buffer;
      node.connect(ctx.destination);
      source = node;
      players.add(stop);
      try {
        await new Promise<void>((resolve) => {
          const done = () => {
            signal.removeEventListener('abort', stop);
            if (finish === done) finish = null;
            resolve();
          };
          finish = done;
          node.onended = done;
          signal.addEventListener('abort', stop, { once: true });
          node.start();
        });
      } finally {
        if (source === node) source = null;
        if (!source) players.delete(stop);
        node.disconnect();
      }
    },

    stop,

    unlock() {
      const ctx = audio();
      if (ctx.state === 'suspended') void ctx.resume().catch(() => {});
    },

    dispose() {
      stop();
      players.delete(stop);
      void context?.close().catch(() => {});
      context = null;
    },
  };
}
