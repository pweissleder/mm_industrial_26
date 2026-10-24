import { describe, expect, it, vi } from 'vitest';
import { GuidanceVoice, type GuidanceVoiceState, type SpeechPort } from './guidanceVoice';

interface Deferred {
  resolve: () => void;
  reject: (e: unknown) => void;
}

/** Fake port: synthesize resolves at once (unless told to fail); play stays open until finished. */
class FakePort implements SpeechPort {
  synthesized: { text: string; speed: number }[] = [];
  plays: { pcm: string; signal: AbortSignal; done: Deferred }[] = [];
  stops = 0;
  failSynth: unknown = null;
  failPlay: unknown = null; // one-shot
  active = 0;
  maxActive = 0;

  async synthesize(text: string, speed: number, signal: AbortSignal) {
    this.synthesized.push({ text, speed });
    if (this.failSynth) throw this.failSynth;
    if (signal.aborted) throw new Error('aborted');
    return { pcm: `pcm:${text}`, rate: 16000 };
  }

  play(pcm: string, _rate: number, signal: AbortSignal): Promise<void> {
    const fail = this.failPlay; // one-shot
    if (fail) {
      this.failPlay = null;
      return Promise.reject(fail);
    }
    this.active++;
    this.maxActive = Math.max(this.maxActive, this.active);
    return new Promise<void>((resolve, reject) => {
      const end = () => {
        this.active--;
        signal.removeEventListener('abort', end);
        resolve();
      };
      signal.addEventListener('abort', end);
      this.plays.push({ pcm, signal, done: { resolve: end, reject } });
    });
  }

  stop() {
    this.stops++;
  }

  /** Finishes the newest open playback. */
  finish() {
    const open = this.plays.filter((p) => !p.signal.aborted);
    open[open.length - 1]?.done.resolve();
  }

  get spoken() {
    return this.plays.map((p) => p.pcm.replace('pcm:', ''));
  }
}

const flush = async () => {
  for (let i = 0; i < 10; i++) await Promise.resolve();
};

function setup(options: { speed?: number; muted?: boolean } = {}) {
  const port = new FakePort();
  const states: GuidanceVoiceState[] = [];
  const voice = new GuidanceVoice(port, { ...options, onChange: (s) => states.push(s) });
  return { port, voice, states };
}

describe('GuidanceVoice', () => {
  it('speaks with default speed 1.1 and reports speaking', async () => {
    const { port, voice } = setup();
    expect(voice.state).toEqual({ speaking: false, muted: false, speed: 1.1, last: null, error: null });
    voice.say('r1:0', 'Go through the door.');
    expect(voice.state.speaking).toBe(true);
    await flush();
    expect(port.synthesized).toEqual([{ text: 'Go through the door.', speed: 1.1 }]);
    expect(port.spoken).toEqual(['Go through the door.']);
    port.finish();
    await flush();
    expect(voice.state.speaking).toBe(false);
    expect(voice.state.last).toBe('Go through the door.');
  });

  it('never overlaps, and the newest pending utterance wins', async () => {
    const { port, voice } = setup();
    voice.say('a', 'First.');
    await flush();
    voice.say('b', 'Second.');
    voice.say('c', 'Third.');
    await flush();
    expect(port.spoken).toEqual(['First.']);
    port.finish();
    await flush();
    expect(port.spoken).toEqual(['First.', 'Third.']);
    expect(voice.state.speaking).toBe(true);
    port.finish();
    await flush();
    expect(port.maxActive).toBe(1);
    expect(port.spoken).toEqual(['First.', 'Third.']);
    expect(voice.state.speaking).toBe(false);
  });

  it('speaks the same key only once', async () => {
    const { port, voice } = setup();
    voice.say('r1:2', 'Turn left.');
    await flush();
    port.finish();
    await flush();
    voice.say('r1:2', 'Turn left.');
    voice.say('r1:2', 'Turn left now.');
    await flush();
    expect(port.spoken).toEqual(['Turn left.']);
    voice.say('r2:2', 'Turn left.'); // new route revision: spoken again
    await flush();
    expect(port.spoken).toEqual(['Turn left.', 'Turn left.']);
  });

  it('cancel aborts current playback and clears pending', async () => {
    const { port, voice } = setup();
    voice.say('a', 'Old route.');
    await flush();
    voice.say('b', 'Old pending.');
    voice.cancel();
    expect(port.plays[0].signal.aborted).toBe(true);
    expect(port.stops).toBe(1);
    expect(voice.state.speaking).toBe(false);
    await flush();
    expect(port.spoken).toEqual(['Old route.']);
    voice.say('c', 'New route.');
    await flush();
    expect(port.spoken).toEqual(['Old route.', 'New route.']);
  });

  it('cancel during synthesis aborts it and never plays the stale audio', async () => {
    const port = new FakePort();
    let signal: AbortSignal | undefined;
    port.synthesize = vi.fn((_t: string, _s: number, sig: AbortSignal) => {
      signal = sig;
      return new Promise<{ pcm: string; rate: number }>((resolve) =>
        setTimeout(() => resolve({ pcm: 'late', rate: 16000 }), 10),
      );
    });
    vi.useFakeTimers();
    try {
      const voice = new GuidanceVoice(port);
      voice.say('a', 'Stale.');
      voice.cancel();
      expect(signal?.aborted).toBe(true);
      await vi.advanceTimersByTimeAsync(20);
      expect(port.plays).toHaveLength(0);
      expect(voice.state.speaking).toBe(false);
      expect(voice.state.error).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it('mute stops current audio and suppresses new hints; repeat respects mute', async () => {
    const { port, voice } = setup();
    voice.say('a', 'Take the lift.');
    await flush();
    voice.setMuted(true);
    expect(port.plays[0].signal.aborted).toBe(true);
    expect(voice.state).toMatchObject({ muted: true, speaking: false });
    voice.say('b', 'Floor 2.');
    voice.repeat();
    await flush();
    expect(port.spoken).toEqual(['Take the lift.']);
    expect(voice.state.last).toBe('Floor 2.');
    voice.setMuted(false);
    voice.repeat();
    await flush();
    expect(port.spoken).toEqual(['Take the lift.', 'Floor 2.']);
  });

  it('starts muted when asked', async () => {
    const { port, voice } = setup({ muted: true });
    voice.say('a', 'Hello.');
    await flush();
    expect(port.synthesized).toHaveLength(0);
  });

  it('repeat interrupts and speaks the last text again', async () => {
    const { port, voice } = setup();
    voice.repeat(); // nothing said yet
    voice.say('a', 'Arrived.');
    await flush();
    voice.repeat();
    await flush();
    expect(port.plays[0].signal.aborted).toBe(true);
    expect(port.spoken).toEqual(['Arrived.', 'Arrived.']);
    expect(port.active).toBe(1);
  });

  it('swallows port errors, sets state.error, then recovers', async () => {
    const { port, voice } = setup();
    port.failSynth = new Error('Voice is off: no ElevenLabs key on the server.');
    expect(() => voice.say('a', 'Turn right.')).not.toThrow();
    await flush();
    expect(voice.state).toMatchObject({ speaking: false, error: 'Voice is off: no ElevenLabs key on the server.' });
    port.failSynth = null;
    port.failPlay = 'boom';
    voice.say('b', 'Turn left.');
    await flush();
    expect(voice.state.error).toBe('boom');
    voice.say('c', 'Straight on.');
    await flush();
    port.finish();
    await flush();
    expect(voice.state.error).toBeNull();
  });

  it('an error still moves on to the pending utterance', async () => {
    const { port, voice } = setup();
    port.failPlay = new Error('x'.repeat(300));
    voice.say('a', 'One.');
    voice.say('b', 'Two.');
    await flush();
    expect(voice.state.error?.length).toBeLessThanOrEqual(120);
    expect(port.spoken).toEqual(['Two.']);
  });

  it('a throwing port.stop or listener never reaches the caller', async () => {
    const port = new FakePort();
    port.stop = () => {
      throw new Error('stop failed');
    };
    const voice = new GuidanceVoice(port, {
      onChange: () => {
        throw new Error('listener failed');
      },
    });
    voice.say('a', 'Hi.');
    await flush();
    expect(() => voice.cancel()).not.toThrow();
  });

  it('suspend stops playback, drops pending and ignores say until resume', async () => {
    const { port, voice } = setup();
    voice.say('a', 'Speaking.');
    await flush();
    voice.say('b', 'Pending.');
    voice.suspend();
    expect(port.plays[0].signal.aborted).toBe(true);
    voice.say('c', 'While recording.');
    voice.repeat();
    await flush();
    expect(port.spoken).toEqual(['Speaking.']);
    expect(voice.state.speaking).toBe(false);
    voice.resume();
    voice.say('c', 'While recording.'); // key was not consumed while suspended
    await flush();
    expect(port.spoken).toEqual(['Speaking.', 'While recording.']);
  });

  it('clamps speed to 0.7..1.2', () => {
    const { voice } = setup({ speed: 3 });
    expect(voice.state.speed).toBe(1.2);
    voice.setSpeed(0.1);
    expect(voice.state.speed).toBe(0.7);
    voice.setSpeed(Number.NaN);
    expect(voice.state.speed).toBe(1.1);
    voice.setSpeed(0.95);
    expect(voice.state.speed).toBe(0.95);
  });

  it('caches synthesized audio by text and speed', async () => {
    const { port, voice } = setup();
    voice.say('r1:0', 'Go through the door.');
    await flush();
    port.finish();
    await flush();
    voice.say('r2:0', 'Go through the door.');
    await flush();
    port.finish();
    voice.repeat();
    await flush();
    expect(port.synthesized).toHaveLength(1);
    expect(port.spoken).toHaveLength(3);
    voice.setSpeed(1.0);
    voice.repeat();
    await flush();
    expect(port.synthesized).toEqual([
      { text: 'Go through the door.', speed: 1.1 },
      { text: 'Go through the door.', speed: 1 },
    ]);
  });

  it('notifies onChange with fresh snapshots', async () => {
    const { voice, states } = setup();
    voice.setSpeed(1.0);
    voice.setSpeed(1.0); // no change, no event
    expect(states).toHaveLength(1);
    expect(states[0]).toBe(voice.state);
    voice.setMuted(true);
    expect(states[0]).toMatchObject({ speed: 1, muted: false }); // old snapshot unchanged
    expect(states[1]).toMatchObject({ speed: 1, muted: true });
  });
});

describe('GuidanceVoice priority', () => {
  it('keeps a pending floor change when a lower-priority hint arrives later', async () => {
    const said: string[] = [];
    let finish: () => void = () => {};
    const port = {
      synthesize: async (text: string) => ({ pcm: text, rate: 16000 }),
      play: (pcm: string) => {
        said.push(pcm);
        return new Promise<void>((r) => (finish = r));
      },
      stop: () => {},
    };
    const voice = new GuidanceVoice(port);
    voice.say('1', 'Go through the door.');
    await Promise.resolve();
    await Promise.resolve();
    voice.say('2', 'Take the stairs down to Ground floor.', 2);
    voice.say('3', 'Turn left.', 0);
    finish();
    await new Promise((r) => setTimeout(r, 0));
    await new Promise((r) => setTimeout(r, 0));
    expect(said).toContain('Take the stairs down to Ground floor.');
    expect(said).not.toContain('Turn left.');
  });
});
