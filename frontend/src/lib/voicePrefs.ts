// One spoken-voice setting for the chat replies and the navigation hints: on/off and speed,
// kept in localStorage (the chat's original keys) and shared live between both via a tiny store.
import { useSyncExternalStore } from 'react';

const SPEAK_KEY = 'arinav:speak-replies';
const SPEED_KEY = 'arinav:speech-speed';

export interface VoicePrefs {
  /** null: never chosen, so the profile decides (on in the navigator, off elsewhere). */
  speak: boolean | null;
  /** null: the server's default speed. */
  speed: number | null;
}

function read(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, value: string | null) {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    // Storage may be unavailable (private mode); remembering is only a convenience.
  }
}

function load(): VoicePrefs {
  const speak = read(SPEAK_KEY);
  const speed = Number(read(SPEED_KEY));
  return {
    speak: speak === 'true' ? true : speak === 'false' ? false : null,
    speed: read(SPEED_KEY) !== null && Number.isFinite(speed) && speed > 0 ? speed : null,
  };
}

let prefs: VoicePrefs = load();
const listeners = new Set<() => void>();

export function setVoicePrefs(patch: Partial<VoicePrefs>): void {
  prefs = { ...prefs, ...patch };
  if ('speak' in patch) write(SPEAK_KEY, prefs.speak === null ? null : String(prefs.speak));
  if ('speed' in patch) write(SPEED_KEY, prefs.speed === null ? null : String(prefs.speed));
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useVoicePrefs(): VoicePrefs {
  return useSyncExternalStore(subscribe, () => prefs);
}

/** Whether replies and hints are spoken: the user's choice, else on only in the navigator. */
export const voiceWanted = (p: VoicePrefs, navigator: boolean) => p.speak ?? navigator;
