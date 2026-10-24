import { useCallback, useEffect, useRef, useState, type CSSProperties, type SubmitEvent } from 'react';
import {
  api,
  type AssistantStatus,
  type ConfirmResponse,
  type HealthResponse,
  type Proposal,
  type TurnResponse,
  type UiState,
} from '../../lib/api';
import { replyAsks, type SimulatedLocation } from '../../lib/nav';
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
} from './Icons';
import { useVoice } from './useVoice';

type Entry =
  | { id: number; role: 'user'; text: string; voice?: boolean }
  | {
      id: number;
      role: 'assistant';
      text: string;
      turn?: TurnResponse;
      audio?: { pcm: string; rate: number };
    }
  | { id: number; role: 'proposal'; proposal: Proposal; state: ProposalState }
  | { id: number; role: 'notice'; text: string };

type NewEntry = Entry extends infer E ? (E extends Entry ? Omit<E, 'id'> : never) : never;

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
  developer?: boolean;
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
}

const SUGGESTIONS = [
  'Where is the nearest fire extinguisher?',
  'Route from the lobby to the 3D printer',
  'Which rooms are on floor 1?',
];

const WAITING = [
  'Understanding your question',
  'Finding destinations',
  'Checking routes',
  'Preparing the answer',
];

const SESSION_KEY = 'arinav:assistant-session';
const SPEAK_KEY = 'arinav:speak-replies';
const SPEED_KEY = 'arinav:speech-speed';
const NO_UI: UiState = { building_id: null };

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
  developer = false,
  ui = NO_UI,
  selection = null,
  simulated = null,
  onTurn,
  onConfirmed,
  onPick,
  onUseSelectionAsLocation,
  onClearSimulated,
}: Props) {
  const [entries, setEntries] = useState<Entry[]>([]);
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

  async function sendLegacy(text: string) {
    try {
      const { reply, thread_id } = await api.chat(text, threadId);
      setThreadId(thread_id);
      push({ role: 'assistant', text: reply });
    } catch {
      setError('The building is not answering. Is the backend running?');
    }
  }

  async function sendTurn(text: string, viaVoice: boolean) {
    const sessionId = read(session, SESSION_KEY) ?? undefined;
    const wantSpeech = viaVoice || (speak && canSpeak);
    try {
      const turn = await api.assistantTurn({
        ...(sessionId && { session_id: sessionId }),
        text,
        ui,
        speak: wantSpeech,
        voice: viaVoice,
        ...(wantSpeech && ttsSpeed !== undefined && { tts_speed: ttsSpeed }),
      });
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
      setError('The assistant is not answering right now. Please try again.');
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
    try {
      if (live) await sendTurn(text, viaVoice);
      else await sendLegacy(text);
    } finally {
      busyRef.current = false;
      setPending(false);
    }
  }

  async function transcribe(wav: string) {
    if (busyRef.current) return;
    busyRef.current = true;
    setOpen(true);
    setPending('transcribing');
    setError(null);
    let text = '';
    try {
      text = (await api.assistantTranscribe(wav)).text.trim();
    } catch {
      setError('Could not transcribe the recording. Please try again or type your question.');
    }
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

  async function newConversation() {
    const sessionId = read(session, SESSION_KEY);
    voice.stopPlayback();
    if (sessionId) await api.assistantReset(sessionId).catch(() => undefined);
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
  let lastAudio: { pcm: string; rate: number } | undefined;
  for (const e of entries) if (e.role === 'assistant' && e.audio) lastAudio = e.audio;

  const talkTitle = !canSpeak
    ? 'Voice is not available on this server'
    : !voiceSupported()
      ? 'This browser cannot record audio'
      : 'Hold to talk (or hold Space)';

  return (
    <section className="chat" aria-label="Chat with AriNav">
      {hasLog && open && (
        <div className="chat__panel card">
          <header className="chat__head">
            <LogoMark size={22} />
            <span className="chat__name">{live ? 'AriNav assistant' : 'AriNav agent'}</span>
            <span className={`status status--${statusTone}`} role="status">
              {status}
            </span>
            {live && (
              <button
                className="btn btn--ghost btn--sm chat__new"
                onClick={() => void newConversation()}
                disabled={busy}
              >
                New conversation
              </button>
            )}
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
              const audio = m.audio;
              return (
                <li key={m.id} className="chat__msg chat__msg--assistant">
                  <AssistantReply
                    text={m.text}
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
            <p className="chat__error" role="alert">
              {error}
            </p>
          )}
        </div>
      )}

      {!hasLog && (
        <div className="chat__suggestions" aria-label="Suggestions">
          {SUGGESTIONS.map((s) => (
            <button key={s} className="chip chip--soft" onClick={() => void send(s)}>
              {s}
            </button>
          ))}
        </div>
      )}

      {live && (
        <div className="chat__context">
          {simulated ? (
            <span className="sim-chip" title="Simulated location, sent as your start">
              <PinIcon width={14} height={14} />
              <span className="sim-chip__text">Simulated location: {simulated.label}</span>
              <button
                className="icon-btn icon-btn--sm"
                onClick={() => onClearSimulated?.()}
                aria-label="Clear simulated location"
              >
                <CloseIcon width={14} height={14} />
              </button>
            </span>
          ) : (
            <button className="chip chip--soft" onClick={() => onPick?.('location', false)}>
              <PinIcon width={14} height={14} /> Set simulated location
            </button>
          )}
          <button
            className="chip chip--soft chat__speak"
            aria-pressed={speak && canSpeak}
            onClick={toggleSpeak}
            disabled={!canSpeak}
            title={canSpeak ? 'Read replies aloud' : 'Voice is not available on this server'}
          >
            <SpeakerIcon width={14} height={14} /> Speak replies
          </button>
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
          {lastAudio && (
            <button
              className="icon-btn icon-btn--sm chat__replay-last"
              onClick={() => lastAudio && void voice.play(lastAudio.pcm, lastAudio.rate)}
              aria-label="Replay last spoken reply"
              title="Replay last spoken reply"
            >
              <ReplayIcon width={15} height={15} />
            </button>
          )}
        </div>
      )}

      <form className="chat__bar" onSubmit={handleSubmit}>
        {hasLog && !open ? (
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
        {!hasLog && (
          <span className={`status status--${statusTone} chat__status`} role="status">
            {status}
          </span>
        )}
        {live && (
          <button
            ref={talkRef}
            type="button"
            className={`talk-btn${voice.recording ? ' talk-btn--on' : ''}`}
            disabled={!voiceOn || (busy && !voice.recording)}
            aria-label="Hold to talk"
            aria-pressed={voice.recording}
            title={talkTitle}
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
