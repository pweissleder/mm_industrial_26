import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { Recap } from '../../../lib/api';
import CallWrapped, { SLIDE_MS } from './CallWrapped';

const facts: NonNullable<Recap['facts']> = {
  conversations: 2,
  from: '2026-10-24T09:00:00+00:00',
  to: '2026-10-25T10:00:00+00:00',
  buildings: ['Factory'],
  talk: { minutes: 3.5, questions: 4, voice_questions: 1 },
  searches: [
    { category: 'toilet', label: 'toilet', count: 3 },
    { category: 'coffee_machine', label: 'coffee machine', count: 1 },
  ],
  routes: {
    count: 2,
    distance_m: 55.8,
    longest: { from: 'Office 1.04', to: 'Toilet 0.22', distance_m: 27.9 },
    comparison: { unit: 'pool lengths', value: 2.2 },
  },
  vertical: {
    floor_changes: { stairs: 1, lift: 1 },
    climbed_m: { stairs: 0, lift: 7.2 },
    descended_m: { stairs: 3.5, lift: 0 },
    steps: 42,
  },
  alltime: {
    conversations: 5,
    questions: 12,
    distance_m: 312,
    floor_changes: 4,
    top_searches: [{ category: 'toilet', label: 'toilet', count: 5 }],
  },
};

const ready = (content: Partial<NonNullable<Recap['content']>> = {}): Recap => ({
  id: 'r1',
  status: 'ready',
  created_at: '2026-10-25T10:00:00+00:00',
  session_ids: ['a', 'b'],
  source: 'claude',
  facts,
  content: {
    topTopics: ['Nearest toilet', 'Coffee', 'Stairs'],
    mood: 'Curious',
    moodEmoji: '🔎',
    bestQuote: 'where can I get a coffee?',
    summary: 'You found your way. Coffee was top of mind.',
    nextSteps: ['Try a step-free route.'],
    ...content,
  },
});

// intro, talk, searches, distance, vertical, topics, mood, quote, all-time, summary
const FULL = 10;

const current = () => screen.getByText(/^Slide \d+ of \d+:/).textContent ?? '';
const slideNumber = () => Number(/^Slide (\d+)/.exec(current())?.[1]);

async function flush(ms = 0) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

async function open(recap: Recap | Recap[], onClose = vi.fn()) {
  const answers = Array.isArray(recap) ? [...recap] : [recap];
  const load = vi.fn(async () => (answers.length > 1 ? answers.shift()! : answers[0]));
  render(<CallWrapped recapId="r1" onClose={onClose} load={load} />);
  await flush();
  return { load, onClose };
}

function tap(fraction: number) {
  const stage = document.querySelector('.wrapped__stage') as HTMLElement;
  stage.getBoundingClientRect = () => ({ left: 0, width: 300 }) as DOMRect;
  fireEvent.pointerDown(stage, { clientX: 300 * fraction });
  fireEvent.pointerUp(stage, { clientX: 300 * fraction });
}

function reducedMotion(on: boolean) {
  window.matchMedia = vi.fn((query: string) => ({
    matches: on && query.includes('reduce'),
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })) as unknown as typeof window.matchMedia;
}

beforeEach(() => {
  vi.useFakeTimers({
    toFake: [
      'setTimeout',
      'clearTimeout',
      'requestAnimationFrame',
      'cancelAnimationFrame',
      'performance',
    ],
  });
  reducedMotion(false);
});

afterEach(() => {
  vi.useRealTimers();
});

describe('CallWrapped', () => {
  it('shows a loading state until the recap is generated', async () => {
    const pending = { ...ready(), status: 'pending' as const, facts: undefined, content: undefined };
    const { load } = await open([pending, ready()]);
    expect(screen.getByText('Wrapping up your conversations…')).toBeInTheDocument();
    await flush(1000);
    expect(load).toHaveBeenCalledTimes(2);
    expect(current()).toContain(`Slide 1 of ${FULL}: Your last 2 conversations with AriNav`);
  });

  it('advances on tap, goes back on a tap in the left third, and auto-advances', async () => {
    await open(ready());
    tap(0.8);
    expect(slideNumber()).toBe(2);
    expect(current()).toContain('3.5 minutes talking to AriNav, 4 questions');
    tap(0.1);
    expect(slideNumber()).toBe(1);
    await flush(SLIDE_MS + 50);
    expect(slideNumber()).toBe(2);
  });

  it('pauses while held and with the pause button', async () => {
    await open(ready());
    fireEvent.click(screen.getByRole('button', { name: 'Pause' }));
    await flush(SLIDE_MS * 2);
    expect(slideNumber()).toBe(1);
    fireEvent.click(screen.getByRole('button', { name: 'Play' }));
    await flush(SLIDE_MS + 50);
    expect(slideNumber()).toBe(2);
  });

  it('stops on the summary and replays from the start', async () => {
    const { onClose } = await open(ready());
    for (let i = 1; i < FULL; i++) tap(0.9);
    expect(current()).toContain(`Slide ${FULL} of ${FULL}: Summary`);
    await flush(SLIDE_MS * 2);
    expect(slideNumber()).toBe(FULL); // no auto-advance past the end
    expect(screen.getByText('Try a step-free route.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Replay/ }));
    expect(slideNumber()).toBe(1);
    fireEvent.click(screen.getByRole('button', { name: 'Close recap' }));
    expect(onClose).toHaveBeenCalled();
  });

  it('renders fallback content and leaves out slides without data', async () => {
    const fallback = ready({
      topTopics: ['Toilet', 'Coffee machine', 'Finding your way'],
      mood: 'Focused',
      moodEmoji: '🧭',
      bestQuote: null,
      nextSteps: [],
    });
    fallback.source = 'fallback';
    fallback.facts = {
      ...facts,
      routes: { ...facts.routes, count: 0, distance_m: 0, longest: null },
      vertical: { ...facts.vertical, floor_changes: { stairs: 0, lift: 0 } },
    };
    await open(fallback);
    expect(current()).toContain('Slide 1 of 7:'); // no distance, vertical or quote slide
    const labels = [current()];
    for (let i = 1; i < 7; i++) {
      tap(0.9);
      labels.push(current());
    }
    expect(labels.some((l) => l.includes('The vibe: Focused 🧭'))).toBe(true);
    expect(labels.at(-1)).toContain('Summary: You found your way.');
  });

  it('closes with Escape and explains a failed recap', async () => {
    const onClose = vi.fn();
    await open({ ...ready(), status: 'failed', facts: undefined, content: undefined }, onClose);
    expect(screen.getByText("Your recap isn't available right now.")).toBeInTheDocument();
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
    expect(onClose).toHaveBeenCalled();
  });

  it('shows final numbers at once with reduced motion', async () => {
    reducedMotion(true);
    await open(ready());
    tap(0.9); // talk slide
    expect(screen.getByLabelText('3.5')).toHaveTextContent('3.5');
  });

  it('counts numbers up from zero otherwise', async () => {
    await open(ready());
    tap(0.9);
    expect(screen.getByLabelText('3.5').textContent).toBe('0.0');
  });
});
