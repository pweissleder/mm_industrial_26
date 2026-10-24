import { useEffect, useMemo, useRef, useState, type ReactNode, type SubmitEvent } from 'react';
import { api, type HealthResponse } from '../../lib/api';
import type { BuildingModel, Selection } from '../../lib/building';
import { LogoMark } from './Logo';
import { ChevronIcon, SendIcon, SparkleIcon } from './Icons';

interface Message {
  role: 'user' | 'assistant';
  text: string;
}

export interface Draft {
  text: string;
  nonce: number;
}

interface Props {
  health: HealthResponse | null;
  backendDown: boolean;
  draft: Draft | null;
  buildingId?: string | null;
  /** The shown building; rooms and assets it names in replies become links. */
  model?: BuildingModel | null;
  onSelect?: (sel: Selection) => void;
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
];

export default function ChatDock({ health, backendDown, draft, buildingId = null, model = null, onSelect }: Props) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [threadId, setThreadId] = useState<string>();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(true);
  const inputRef = useRef<HTMLInputElement>(null);
  const logRef = useRef<HTMLOListElement>(null);
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

  // A thread's memory belongs to one building, so a new building starts a new thread.
  useEffect(() => setThreadId(undefined), [buildingId]);

  useEffect(() => {
    if (!draft) return;
    setInput(draft.text);
    inputRef.current?.focus();
  }, [draft]);

  useEffect(() => {
    const log = logRef.current;
    if (log) log.scrollTop = log.scrollHeight;
  }, [messages, pending]);

  async function send(text: string) {
    if (!text || pending) return;
    setMessages((prev) => [...prev, { role: 'user', text }]);
    setInput('');
    setOpen(true);
    setPending(true);
    setError(null);
    try {
      const { reply, thread_id } = await api.chat(text, threadId, buildingId);
      setThreadId(thread_id);
      setMessages((prev) => [...prev, { role: 'assistant', text: reply }]);
    } catch {
      setError('The building is not answering. Is the backend running?');
    } finally {
      setPending(false);
    }
  }

  function handleSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    void send(input.trim());
  }

  const status = backendDown
    ? 'Backend unreachable'
    : health
      ? health.llm_enabled
        ? 'Claude · online'
        : 'Offline mode'
      : 'Connecting…';
  const statusTone = backendDown ? 'bad' : health?.llm_enabled ? 'good' : 'warn';
  const hasLog = messages.length > 0 || !!error;

  return (
    <section className="chat" aria-label="Chat with AriNav">
      {hasLog && open && (
        <div className="chat__panel card">
          <header className="chat__head">
            <LogoMark size={22} />
            <span className="chat__name">AriNav agent</span>
            <span className={`status status--${statusTone}`} role="status">
              {status}
            </span>
            <button className="icon-btn" onClick={() => setOpen(false)} aria-label="Minimise chat">
              <ChevronIcon />
            </button>
          </header>
          <ol className="chat__log" aria-label="Conversation" ref={logRef}>
            {messages.map((m, i) => (
              <li key={i} className={`chat__msg chat__msg--${m.role}`}>
                {m.role === 'assistant' && onSelect ? renderReply(m.text) : m.text}
              </li>
            ))}
            {pending && (
              <li className="chat__msg chat__msg--assistant chat__typing" aria-label="AriNav is typing">
                <span />
                <span />
                <span />
              </li>
            )}
          </ol>
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

      <form className="chat__bar" onSubmit={handleSubmit}>
        {hasLog && !open ? (
          <button
            type="button"
            className="icon-btn chat__expand"
            onClick={() => setOpen(true)}
            aria-label={`Show conversation (${messages.length} messages)`}
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
          placeholder="Ask AriNav about this building…"
          autoComplete="off"
          enterKeyHint="send"
        />
        {!hasLog && (
          <span className={`status status--${statusTone} chat__status`} role="status">
            {status}
          </span>
        )}
        <button
          type="submit"
          className="send-btn"
          disabled={pending || !input.trim()}
          aria-label="Send"
        >
          <SendIcon />
        </button>
      </form>
    </section>
  );
}
