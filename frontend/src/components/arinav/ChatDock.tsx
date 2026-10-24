import { useEffect, useRef, useState, type SubmitEvent } from 'react';
import { api, type GuardrailInfo, type HealthResponse } from '../../lib/api';
import { LogoMark } from './Logo';
import { ChevronIcon, SendIcon, SparkleIcon } from './Icons';

interface Message {
  role: 'user' | 'assistant';
  text: string;
  guardrail?: GuardrailInfo | null;
}

const GUARDRAIL_LABELS: Record<GuardrailInfo['category'], string> = {
  off_topic: 'off-topic',
  prompt_injection: 'prompt injection',
  unsafe: 'unsafe request',
};

export interface Draft {
  text: string;
  nonce: number;
}

interface Props {
  health: HealthResponse | null;
  backendDown: boolean;
  draft: Draft | null;
}

const SUGGESTIONS = [
  'Where is the nearest fire extinguisher?',
  'Route from the lobby to the 3D printer',
  'Which rooms are on floor 1?',
];

export default function ChatDock({ health, backendDown, draft }: Props) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [threadId, setThreadId] = useState<string>();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(true);
  const inputRef = useRef<HTMLInputElement>(null);
  const logRef = useRef<HTMLOListElement>(null);

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
      const { reply, thread_id, guardrail } = await api.chat(text, threadId);
      setThreadId(thread_id);
      setMessages((prev) => [...prev, { role: 'assistant', text: reply, guardrail }]);
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
              <li
                key={i}
                className={`chat__msg chat__msg--${m.role}${m.guardrail ? ' chat__msg--blocked' : ''}`}
              >
                {m.text}
                {m.guardrail && (
                  <span className="chat__flag" title={m.guardrail.reason}>
                    Rejected by guidelines · {GUARDRAIL_LABELS[m.guardrail.category] ?? m.guardrail.category}
                  </span>
                )}
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
