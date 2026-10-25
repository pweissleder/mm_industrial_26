import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
  type SubmitEvent,
} from 'react';
import {
  api,
  type AssistantStatus,
  type ConfirmResponse,
  type GuardrailInfo,
  type HealthResponse,
  type Proposal,
  type TurnResponse,
  type UiState,
} from '../../lib/api';
import type { BuildingModel, Selection } from '../../lib/building';
import { replyAsks, type AppMode, type SimulatedLocation } from '../../lib/nav';
import { voiceSupported } from '../../lib/voice';
import { AssistantReply, ProposalCard, type ProposalState } from './ChatReply';
import { LogoMark } from './Logo';
import {
  ChevronIcon,
  CloseIcon,
  MicIcon,
  PinIcon,
  ReplayIcon,
  SendIcon,
  SparkleIcon,
  SpeakerIcon,
  SpeakerOffIcon,
} from './Icons';
import { useVoice } from './useVoice';

type Entry =
  | { id: number; role: 'user'; text: string; voice?: boolean }
  | {
      id: number;
      role: 'assistant';
      text: string;
      turn?: TurnResponse;
      /** Set when the team agent's input guardrail produced this reply. */
      guardrail?: GuardrailInfo | null;
      audio?: { pcm: string; rate: number };
    }
  | { id: number; role: 'proposal'; proposal: Proposal; state: ProposalState }
  | { id: number; role: 'notice'; text: string; offerReset?: boolean };

type NewEntry = Entry extends infer E ? (E extends Entry ? Omit<E, 'id'> : never) : never;

const GUARDRAIL_LABELS: Record<GuardrailInfo['category'], string> = {
  off_topic: 'off-topic',
  prompt_injection: 'prompt injection',
  unsafe: 'unsafe request',
};

export interface Draft {
  text: string;
  nonce: number;
  /** Send right away instead of only filling the input. */
  send?: boolean;
}

/** What a map pick answers: the user's (simulated) location, or the assistant's question. */
export type PickPurpose = 'location' | 'answer';

interface Props {
  health: HealthResponse | null;
  backendDown: boolean;
  draft: Draft | null;
  /** The assistant (`/api/assistant`); without it the dock talks to the plain `/api/chat` agent. */
  assistant?: AssistantStatus | null;
  /** Who is using the app; decides which chat controls exist. */
  profile?: AppMode;
  /** Location-specific prompts from the HQ pipeline; replace the static ones when non-empty. */
  suggestions?: string[];
  /** Explicit user choices, sent with every turn. */
  ui?: UiState;
  selection?: { name: string; room: boolean } | null;
  simulated?: SimulatedLocation | null;
  onTurn?: (turn: TurnResponse) => void;
  onConfirmed?: (result: ConfirmResponse) => void;
  /** Next map click answers `purpose`; `reply` also sends it as the user's answer. */
  onPick?: (purpose: PickPurpose, reply: boolean) => void;
  onUseSelectionAsLocation?: () => void;
  onClearSimulated?: () => void;
  buildingId?: string | null;
  /** The shown building; rooms and assets it names in replies become links. */
  model?: BuildingModel | null;
  onSelect?: (sel: Selection) => void;
  /** The agent changed the building; reload it so the 3D view shows the edit. */
  onBuildingChanged?: () => void;
}

/** Name/ref/id -> selection, skipping names shared by several things (e.g. twenty "Desk"s). */
export function linkTargets(model: BuildingModel): Map<string, Selection> {
  const found = new Map<string, Selection | null>();
  const add = (label: string | null | undefined, sel: Selection) => {
    if (!label || label.length < 2) return;
    const key = label.toLowerCase();
    const prev = found.get(key);
    found.set(key, prev === undefined || (prev && prev.id === sel.id) ? sel : null);
  };
  for (const s of model.spaces) for (const l of [s.name, s.ref, s.id]) add(l, { kind: 'space', id: s.id });
  for (const a of model.assets) for (const l of [a.name, a.id]) add(l, { kind: 'asset', id: a.id });
  return new Map([...found].filter((e): e is [string, Selection] => e[1] !== null));
}

/** Split text into plain strings and [matched text, selection] pairs, longest names first. */
export function linkify(text: string, targets: Map<string, Selection>): (string | [string, Selection])[] {
  if (!targets.size) return [text];
  const esc = [...targets.keys()].sort((a, b) => b.length - a.length).map((k) => k.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
  const re = new RegExp(`(?<![\\w-])(${esc.join('|')})(?![\\w-])`, 'gi');
  return text.split(re).map((part, i) => (i % 2 ? [part, targets.get(part.toLowerCase())!] : part));
}

const SUGGESTIONS = [
  'Where is the nearest fire extinguisher?',
  'Route from the lobby to the 3D printer',
  'Which rooms are on floor 1?',
  'Close the lift and route me to floor 1 step-free',
];

const WAITING = [
  'Understanding your question',
  'Finding destinations',
  'Checking routes',
  'Preparing the answer',
];

const SESSION_KEY = 'arinav:assistant-session';
/** Idle time before the open conversation folds down to the input bar. */
const CHAT_IDLE_MS = 15_000;
const SPEAK_KEY = 'arinav:speak-replies';
const SPEED_KEY = 'arinav:speech-speed';
const NO_UI: UiState = { building_id: null };
const PaperclipIcon = (p: { width?: number; height?: number; className?: string }) => (
  <svg
    className={p.className}
    viewBox="0 0 24 24"
    width={p.width ?? 16}
    height={p.height ?? 16}
    fill="none"
    stroke="currentColor"
    strokeWidth="1.8"
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden="true"
  >
    <path d="m21 11.5-8.6 8.6a5.5 5.5 0 0 1-7.8-7.8l9-9a3.7 3.7 0 0 1 5.2 5.2l-9 9a1.8 1.8 0 0 1-2.6-2.6l8.3-8.3" />
  </svg>
);

// Pill geometry in px (see .attach-chip): the name's max width, everything around the name
// when unfolded (padding, leading icon, gaps, remove button) and the folded icon-only size.
const PILL_TEXT_MAX = 200;
const PILL_CHROME = 68;
const PILL_COMPACT = 34;
// Measures the input's text (placeholder or typed), with a rough fallback where canvas is missing.
let measureCtx: { font: string; measureText: (t: string) => { width: number } } | null = null;
function textWidth(text: string, font: string): number {
  if (!measureCtx) {
    const canvas = document.createElement('canvas');
    measureCtx = (typeof canvas.getContext === 'function' && !navigator.userAgent.includes('jsdom')
      ? canvas.getContext('2d')
      : null) ?? { font: '', measureText: (t: string) => ({ width: t.length * 7.5 }) };
  }
  measureCtx.font = font;
  return measureCtx.measureText(text).width;
}

/**
 * Context sent with each question (location, selected item), shown inside the input bar.
 * Compact: only its icon, which turns into the remove cross on hover or focus.
 */
function ContextPill(props: {
  Icon: (p: { width?: number; height?: number; className?: string }) => ReactNode;
  text: string;
  title: string;
  compact: boolean;
  removeLabel: string;
  onRemove: () => void;
  location?: boolean;
}) {
  const { Icon } = props;
  return (
    <span
      className={`attach-chip${props.location ? ' attach-chip--location' : ''}${
        props.compact ? ' attach-chip--compact' : ''
      }`}
      title={props.title}
    >
      <Icon width={14} height={14} className="attach-chip__clip" />
      <span className="attach-chip__text">{props.text}</span>
      <button
        type="button"
        className="icon-btn icon-btn--sm"
        onClick={props.onRemove}
        aria-label={props.removeLabel}
      >
        <Icon width={14} height={14} className="attach-chip__icon-clip" />
        <CloseIcon width={14} height={14} className="attach-chip__icon-x" />
      </button>
    </span>
  );
}

function read(storage: () => Storage, key: string): string | null {
  try {
    return storage().getItem(key);
  } catch {
    return null;
  }
}

function write(storage: () => Storage, key: string, value: string | null) {
  try {
    if (value === null) storage().removeItem(key);
    else storage().setItem(key, value);
  } catch {
    // Storage may be unavailable (private mode); these are conveniences only.
  }
}

const session = () => sessionStorage;
const local = () => localStorage;

const isEditable = (el: Element | null) =>
  !!el &&
  (el instanceof HTMLInputElement ||
    el instanceof HTMLTextAreaElement ||
    el instanceof HTMLSelectElement ||
    (el instanceof HTMLElement && el.isContentEditable));

export default function ChatDock({
  health,
  backendDown,
  draft,
  assistant = null,
  profile = 'user',
  suggestions,
  ui = NO_UI,
  selection = null,
  simulated = null,
  onTurn,
  onConfirmed,
  onPick,
  onUseSelectionAsLocation,
  onClearSimulated,
  buildingId = null,
  model = null,
  onSelect,
  onBuildingChanged,
}: Props) {
  const developer = profile === 'developer';
  const isNavigator = profile === 'navigator';
  const [entries, setEntries] = useState<Entry[]>([]);
  // The selection the user detached from the chat; a different selection attaches again.
  const [detachedKey, setDetachedKey] = useState<string | null>(null);
  const [input, setInput] = useState('');
  const [threadId, setThreadId] = useState<string>();
  const [pending, setPending] = useState<false | 'thinking' | 'transcribing'>(false);
  const [waitStep, setWaitStep] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(true);
  const [speak, setSpeak] = useState(() => read(local, SPEAK_KEY) === 'true');
  const [speed, setSpeed] = useState<number | null>(() => {
    const v = Number(read(local, SPEED_KEY));
    return read(local, SPEED_KEY) !== null && Number.isFinite(v) && v > 0 ? v : null;
  });
  const inputRef = useRef<HTMLInputElement>(null);
  const logRef = useRef<HTMLOListElement>(null);
  const talkRef = useRef<HTMLButtonElement>(null);
  const nextId = useRef(1);
  const busy = pending !== false;
  // Async callers (transcription, drafts) can't rely on the `busy` of their render.
  const busyRef = useRef(false);
  // Bumped by a reset, so replies still in flight from the old conversation are dropped.
  const epoch = useRef(0);
  const targets = useMemo(() => (model ? linkTargets(model) : new Map<string, Selection>()), [model]);

  const renderReply = (text: string): ReactNode =>
    linkify(text, targets).map((part, i) =>
      typeof part === 'string' ? (
        part
      ) : (
        <button key={i} type="button" className="chat__link" onClick={() => onSelect?.(part[1])}>
          {part[0]}
        </button>
      ),
    );

  // A thread's memory belongs to one building, so a new building starts a new thread; an
  // assistant session can span buildings, so offer to start over instead of wiping the log.
  const seenBuilding = useRef(buildingId);
  useEffect(() => {
    setThreadId(undefined);
    const previous = seenBuilding.current;
    seenBuilding.current = buildingId;
    if (!previous || !buildingId || previous === buildingId) return;
    setEntries((prev) =>
      prev.some((e) => e.role === 'user')
        ? [
            ...prev,
            {
              id: nextId.current++,
              role: 'notice',
              text: 'You switched buildings. Start a new conversation for a clean context?',
              offerReset: true,
            },
          ]
        : prev,
    );
  }, [buildingId]);

  const live = !!assistant;
  const canSpeak = live && !!assistant?.voice;
  const voiceOn = canSpeak && voiceSupported();
  const range = assistant?.tts_speed;
  const ttsSpeed = range
    ? Math.min(range.max, Math.max(range.min, speed ?? range.default))
    : undefined;

  const push = useCallback((entry: NewEntry) => {
    const id = nextId.current++;
    setEntries((prev) => [...prev, { ...entry, id } as Entry]);
    return id;
  }, []);

  const voice = useVoice({
    onAudio: (wav) => void transcribe(wav),
    onError: (text) => {
      setOpen(true);
      push({ role: 'notice', text });
    },
  });

  useEffect(() => {
    const log = logRef.current;
    if (log) log.scrollTop = log.scrollHeight;
  }, [entries, pending]);

  // While waiting, step through plain progress phases (the real ones arrive with the reply).
  useEffect(() => {
    if (pending !== 'thinking') return;
    setWaitStep(0);
    const timer = window.setInterval(
      () => setWaitStep((s) => Math.min(s + 1, WAITING.length - 1)),
      1600,
    );
    return () => window.clearInterval(timer);
  }, [pending]);

  const selKey = selection
    ? `${ui.inspected ? `${ui.inspected.kind}:${ui.inspected.id}` : ''}|${selection.name}`
    : null;
  const attached = !!selection && detachedKey !== selKey;
  // Context pills in the input bar fold to their icons one after the other (the one next to the
  // text first) as soon as the typed text, or the placeholder, would run into them. The room is
  // worked out from the pills' target sizes, so folding animations don't feed back into it.
  const [folded, setFolded] = useState(0);
  const fitPills = useCallback(() => {
    const el = inputRef.current;
    const bar = el?.parentElement;
    if (!el || !bar) return;
    const pills = [...bar.querySelectorAll<HTMLElement>('.attach-chip')];
    const need = textWidth(el.value || el.placeholder, getComputedStyle(el).font) + 12;
    const total = el.clientWidth + pills.reduce((sum, p) => sum + p.getBoundingClientRect().width, 0);
    const full = pills.map(
      (p) => Math.min(PILL_TEXT_MAX, p.querySelector('.attach-chip__text')?.scrollWidth ?? 0) + PILL_CHROME,
    );
    let k = 0;
    const roomFor = (n: number) =>
      total - n * PILL_COMPACT - full.slice(n).reduce((sum, w) => sum + w, 0);
    while (k < pills.length && need > roomFor(k)) k += 1;
    setFolded(k);
  }, []);
  useLayoutEffect(fitPills, [fitPills, input, voice.recording, simulated, attached, selection]);
  useEffect(() => {
    const el = inputRef.current;
    if (!el || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(() => fitPills());
    observer.observe(el.parentElement ?? el);
    return () => observer.disconnect();
  }, [fitPills]);

  async function sendLegacy(text: string) {
    const started = epoch.current;
    try {
      const { reply, thread_id, guardrail, building_changed } = await api.chat(text, threadId, buildingId);
      if (started !== epoch.current) return;
      setThreadId(thread_id);
      if (building_changed) onBuildingChanged?.();
      push({ role: 'assistant', text: reply, guardrail });
    } catch {
      if (started !== epoch.current) return;
      setError('The building is not answering. Is the backend running?');
    }
  }

  async function sendTurn(text: string, viaVoice: boolean) {
    const sessionId = read(session, SESSION_KEY) ?? undefined;
    const wantSpeech = canSpeak && (viaVoice || isNavigator || speak);
    const sendUi = selection && detachedKey === selKey ? { ...ui, inspected: null } : ui;
    const started = epoch.current;
    try {
      const turn = await api.assistantTurn({
        ...(sessionId && { session_id: sessionId }),
        text,
        ui: sendUi,
        speak: wantSpeech,
        voice: viaVoice,
        ...(wantSpeech && ttsSpeed !== undefined && { tts_speed: ttsSpeed }),
      });
      if (started !== epoch.current) return;
      if (turn.session_id) write(session, SESSION_KEY, turn.session_id);
      const audio = turn.audio_pcm16
        ? { pcm: turn.audio_pcm16, rate: turn.sample_rate ?? 16000 }
        : undefined;
      push({ role: 'assistant', text: turn.reply, turn, audio });
      if (turn.ui?.proposal) push({ role: 'proposal', proposal: turn.ui.proposal, state: 'open' });
      if (turn.voice_error) {
        push({ role: 'notice', text: 'The spoken reply failed; the text answer is complete.' });
      }
      onTurn?.(turn);
      if (audio) void voice.play(audio.pcm, audio.rate);
    } catch {
      if (started !== epoch.current) return;
      setError('The assistant is not answering right now. Please try again, or start a new conversation.');
    }
  }

  async function send(text: string, viaVoice = false) {
    if (!text || busyRef.current) return;
    busyRef.current = true;
    if (!viaVoice) push({ role: 'user', text });
    setInput('');
    setOpen(true);
    setPending('thinking');
    setError(null);
    voice.stopPlayback();
    const started = epoch.current;
    try {
      if (live) await sendTurn(text, viaVoice);
      else await sendLegacy(text);
    } finally {
      if (started === epoch.current) {
        busyRef.current = false;
        setPending(false);
      }
    }
  }

  async function transcribe(wav: string) {
    if (busyRef.current) return;
    busyRef.current = true;
    setOpen(true);
    setPending('transcribing');
    setError(null);
    let text = '';
    const started = epoch.current;
    try {
      text = (await api.assistantTranscribe(wav)).text.trim();
    } catch {
      if (started === epoch.current) {
        setError('Could not transcribe the recording. Please try again or type your question.');
      }
    }
    if (started !== epoch.current) return;
    busyRef.current = false;
    setPending(false);
    if (!text) return;
    push({ role: 'user', text, voice: true });
    await sendRef.current(text, true);
  }

  // Keep `send` fresh for effects and async callers without re-running them on every render.
  const sendRef = useRef(send);
  sendRef.current = send;

  useEffect(() => {
    if (!draft) return;
    if (draft.send) {
      void sendRef.current(draft.text);
      return;
    }
    setInput(draft.text);
    inputRef.current?.focus();
  }, [draft]);

  // Hold Space to talk, unless typing or another control has focus.
  const { start: startTalk, stop: stopTalk, recording } = voice;
  useEffect(() => {
    if (!voiceOn) return;
    const usable = () => {
      const el = document.activeElement;
      return el === talkRef.current || !(isEditable(el) || el instanceof HTMLButtonElement);
    };
    const down = (e: KeyboardEvent) => {
      if (e.code !== 'Space' || e.repeat || !usable() || busy) return;
      e.preventDefault();
      void startTalk();
    };
    const up = (e: KeyboardEvent) => {
      if (e.code !== 'Space' || !recording) return;
      e.preventDefault();
      stopTalk();
    };
    window.addEventListener('keydown', down);
    window.addEventListener('keyup', up);
    return () => {
      window.removeEventListener('keydown', down);
      window.removeEventListener('keyup', up);
    };
  }, [voiceOn, busy, startTalk, stopTalk, recording]);

  // The conversation folds away after a while without interaction (pointer, keys, scrolling).
  // It stays open while a reply is pending, while recording, while the pointer is over it and
  // while a proposal waits for an answer; a new message restarts the clock.
  const [activity, setActivity] = useState(0);
  const [hovering, setHovering] = useState(false);
  const touch = () => setActivity((n) => n + 1);
  const awaitingAnswer = entries.some((e) => e.role === 'proposal' && e.state === 'open');
  useEffect(() => {
    if (!open || busy || recording || hovering || awaitingAnswer || !entries.length) return;
    const timer = window.setTimeout(() => setOpen(false), CHAT_IDLE_MS);
    return () => window.clearTimeout(timer);
  }, [open, busy, recording, hovering, awaitingAnswer, entries.length, activity]);

  async function answerProposal(entryId: number, proposal: Proposal, approved: boolean) {
    const sessionId = read(session, SESSION_KEY) ?? '';
    const mark = (state: ProposalState) =>
      setEntries((prev) =>
        prev.map((e) => (e.id === entryId && e.role === 'proposal' ? { ...e, state } : e)),
      );
    mark('busy');
    try {
      const result = await api.assistantConfirm(sessionId, proposal.proposal_id, approved);
      mark(result.status === 'applied' ? 'applied' : 'cancelled');
      push({ role: 'assistant', text: result.message });
      onConfirmed?.(result);
    } catch {
      mark('failed');
      push({ role: 'notice', text: 'The change could not be applied. Nothing was changed.' });
    }
  }

  /** Start over: forget the server-side session or thread and drop any reply still on its way. */
  function newConversation() {
    const sessionId = read(session, SESSION_KEY);
    epoch.current++;
    busyRef.current = false;
    setPending(false);
    voice.stopPlayback();
    // Fire and forget: a backend that is down must not block starting over.
    if (sessionId) void api.assistantReset(sessionId).catch(() => undefined);
    write(session, SESSION_KEY, null);
    setThreadId(undefined);
    setEntries([]);
    setError(null);
  }

  function handleSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    void send(input.trim());
  }

  const toggleSpeak = () => {
    const next = !speak;
    setSpeak(next);
    write(local, SPEAK_KEY, String(next));
    if (!next) voice.stopPlayback();
  };

  const changeSpeed = (v: number) => {
    setSpeed(v);
    write(local, SPEED_KEY, String(v));
  };

  const status = backendDown
    ? 'Backend unreachable'
    : assistant
      ? assistant.live
        ? `Claude · live${assistant.voice ? ' · voice' : ''}`
        : 'Assistant offline'
      : health
        ? health.llm_enabled
          ? 'Claude · online'
          : 'Offline mode'
        : 'Connecting…';
  const good = assistant ? assistant.live : health?.llm_enabled;
  const statusTone = backendDown ? 'bad' : good ? 'good' : 'warn';
  const hasLog = entries.length > 0 || !!error;
  const messages = entries.filter((e) => e.role === 'user' || e.role === 'assistant').length;

  // Quick answers when the last reply asks for something the map can answer.
  const last = entries[entries.length - 1];
  const asks = live && !busy && last?.role === 'assistant' ? replyAsks(last.text) : null;
  const latestReply = last?.role === 'assistant' && !busy ? last.text : null;

  return (
    <section
      className="chat"
      aria-label="Chat with AriNav"
      onPointerDown={touch}
      onKeyDown={touch}
      onWheel={touch}
      onPointerEnter={() => setHovering(true)}
      onPointerLeave={() => setHovering(false)}
    >
      {hasLog && open && !isNavigator && (
        <div className="chat__panel card">
          <header className="chat__head">
            <LogoMark size={22} />
            <span className="chat__name">{live ? 'AriNav Assistant' : 'AriNav Agent'}</span>
            {developer && (
              <span className={`status status--${statusTone}`} role="status">
                {status}
              </span>
            )}
            {canSpeak && (
              <button
                className="icon-btn chat__speak-toggle"
                aria-pressed={speak}
                onClick={toggleSpeak}
                aria-label={speak ? 'Stop reading replies aloud' : 'Read replies aloud'}
                title={
                  speak
                    ? 'Reading replies aloud (click to mute)'
                    : 'Replies muted (click to read them aloud)'
                }
              >
                {speak ? (
                  <SpeakerIcon width={20} height={20} />
                ) : (
                  <SpeakerOffIcon width={20} height={20} />
                )}
              </button>
            )}
            <button
              className="btn btn--ghost btn--sm chat__new"
              onClick={newConversation}
              title="Clear the conversation and start a fresh session"
            >
              <ReplayIcon width={14} height={14} />
              <span className="chat__new-label">New conversation</span>
            </button>
            <button className="icon-btn" onClick={() => setOpen(false)} aria-label="Minimise chat">
              <ChevronIcon />
            </button>
          </header>
          <ol className="chat__log" aria-label="Conversation" ref={logRef}>
            {entries.map((m) => {
              if (m.role === 'proposal') {
                return (
                  <li key={m.id} className="chat__msg chat__msg--assistant chat__msg--card">
                    <ProposalCard
                      proposal={m.proposal}
                      state={m.state}
                      onAnswer={(approved) => void answerProposal(m.id, m.proposal, approved)}
                    />
                  </li>
                );
              }
              if (m.role === 'notice') {
                return (
                  <li key={m.id} className="chat__msg chat__msg--notice">
                    {m.text}
                    {m.offerReset && (
                      <button className="chip chat__reset-chip" onClick={newConversation}>
                        Start new conversation
                      </button>
                    )}
                  </li>
                );
              }
              if (m.role === 'user') {
                return (
                  <li key={m.id} className="chat__msg chat__msg--user">
                    {m.voice && (
                      <MicIcon width={13} height={13} className="chat__voice-mark" />
                    )}
                    {m.text}
                  </li>
                );
              }
              if (m.guardrail) {
                return (
                  <li key={m.id} className="chat__msg chat__msg--assistant chat__msg--blocked">
                    {m.text}
                    <span className="chat__flag" title={m.guardrail.reason}>
                      Rejected by guidelines ·{' '}
                      {GUARDRAIL_LABELS[m.guardrail.category] ?? m.guardrail.category}
                    </span>
                  </li>
                );
              }
              const audio = m.audio;
              return (
                <li key={m.id} className="chat__msg chat__msg--assistant">
                  <AssistantReply
                    text={m.text}
                    renderText={onSelect ? renderReply : undefined}
                    turn={m.turn}
                    developer={developer}
                    canReplay={!!audio}
                    onReplay={() => audio && void voice.play(audio.pcm, audio.rate)}
                  />
                </li>
              );
            })}
            {busy && (
              <li
                className="chat__msg chat__msg--assistant chat__typing"
                aria-label="AriNav is typing"
              >
                <span />
                <span />
                <span />
                <em className="chat__phase">
                  {pending === 'transcribing' ? 'Transcribing speech' : WAITING[waitStep]}…
                </em>
              </li>
            )}
          </ol>
          {asks && (
            <div className="chat__chips" aria-label="Quick answers">
              {selection &&
                (asks === 'start' ? (
                  selection.room && (
                    <button className="chip" onClick={() => onUseSelectionAsLocation?.()}>
                      Use my selected room
                    </button>
                  )
                ) : (
                  <button className="chip" onClick={() => void send(`I mean ${selection.name}.`)}>
                    {selection.room ? 'Use my selected room' : `Use ${selection.name}`}
                  </button>
                ))}
              <button
                className="chip"
                onClick={() => onPick?.(asks === 'start' ? 'location' : 'answer', true)}
              >
                Pick on map
              </button>
            </div>
          )}
          {error && (
            <div className="chat__error" role="alert">
              <span>{error}</span>
              <button className="chip chat__reset-chip" onClick={newConversation}>
                <ReplayIcon width={13} height={13} /> Start over
              </button>
            </div>
          )}
        </div>
      )}

      {!hasLog && !isNavigator && (
        <div className="chat__suggestions" aria-label="Suggestions">
          {(suggestions?.length ? suggestions : SUGGESTIONS).map((s) => (
            <button key={s} className="chip chip--soft" onClick={() => void send(s)}>
              {s}
            </button>
          ))}
        </div>
      )}

      {isNavigator && (error || latestReply) && (
        <div className={`chat__latest card${error ? ' chat__latest--error' : ''}`} role={error ? 'alert' : 'status'}>
          {error ?? (latestReply && onSelect ? renderReply(latestReply) : latestReply)}
        </div>
      )}

      {live && developer && (!simulated || (speak && canSpeak)) && (
        <div className="chat__context">
          {!simulated && (
            <button className="chip chip--soft" onClick={() => onPick?.('location', false)}>
              <PinIcon width={14} height={14} /> Set simulated location
            </button>
          )}
          {speak && canSpeak && range && ttsSpeed !== undefined && (
            <label className="chat__speed">
              <span>Speed {ttsSpeed.toFixed(2)}×</span>
              <input
                type="range"
                className="range"
                min={range.min}
                max={range.max}
                step={0.05}
                value={ttsSpeed}
                aria-label="Speech speed"
                style={
                  {
                    '--pct': `${((ttsSpeed - range.min) / (range.max - range.min || 1)) * 100}%`,
                  } as CSSProperties
                }
                onChange={(e) => changeSpeed(Number(e.target.value))}
              />
            </label>
          )}
        </div>
      )}

      <form className="chat__bar" onSubmit={handleSubmit}>
        {hasLog && !open && !isNavigator ? (
          <button
            type="button"
            className="icon-btn chat__expand"
            onClick={() => setOpen(true)}
            aria-label={`Show conversation (${messages} messages)`}
          >
            <ChevronIcon style={{ transform: 'rotate(180deg)' }} />
          </button>
        ) : (
          <span className="chat__spark" aria-hidden="true">
            <SparkleIcon />
          </span>
        )}
        <label htmlFor="chat-input" className="visually-hidden">
          Message
        </label>
        <input
          id="chat-input"
          ref={inputRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={voice.recording ? 'Listening…' : 'Ask AriNav about this building…'}
          autoComplete="off"
          enterKeyHint="send"
          maxLength={2000}
        />
        {!hasLog && developer && (
          <span className={`status status--${statusTone} chat__status`} role="status">
            {status}
          </span>
        )}
        {live && !isNavigator && simulated && (
          <ContextPill
            Icon={PinIcon}
            text={`Location: ${simulated.label}`}
            title={`Location: ${simulated.label} · sent as your start`}
            compact={folded >= 1}
            removeLabel="Clear location"
            onRemove={() => onClearSimulated?.()}
            location
          />
        )}
        {live && !isNavigator && selection && attached && (
          <ContextPill
            Icon={PaperclipIcon}
            text={selection.name}
            title={`${selection.name} · sent with your questions as context`}
            compact={folded >= (simulated ? 2 : 1)}
            removeLabel={`Detach ${selection.name}`}
            onRemove={() => setDetachedKey(selKey)}
          />
        )}
        {voiceOn && (
          <button
            ref={talkRef}
            type="button"
            className={`talk-btn${voice.recording ? ' talk-btn--on' : ''}`}
            disabled={busy && !voice.recording}
            aria-label="Hold to talk"
            aria-pressed={voice.recording}
            title="Hold to talk (or hold Space)"
            onPointerDown={(e) => {
              e.preventDefault();
              e.currentTarget.setPointerCapture?.(e.pointerId);
              void voice.start();
            }}
            onPointerUp={() => voice.stop()}
            onPointerCancel={() => voice.stop()}
          >
            <MicIcon />
          </button>
        )}
        <button type="submit" className="send-btn" disabled={busy || !input.trim()} aria-label="Send">
          <SendIcon />
        </button>
      </form>
    </section>
  );
}
