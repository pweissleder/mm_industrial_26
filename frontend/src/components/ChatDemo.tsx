import { useEffect, useState, type SubmitEvent } from 'react';
import { api, type HealthResponse } from '../lib/api';

interface Message {
  role: 'user' | 'assistant';
  text: string;
}

export default function ChatDemo() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [backendDown, setBackendDown] = useState(false);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [threadId, setThreadId] = useState<string>();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .health()
      .then(setHealth)
      .catch(() => setBackendDown(true));
  }, []);

  async function handleSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    const text = input.trim();
    if (!text || pending) return;

    setMessages((prev) => [...prev, { role: 'user', text }]);
    setInput('');
    setPending(true);
    setError(null);
    try {
      const { reply, thread_id } = await api.chat(text, threadId);
      setThreadId(thread_id);
      setMessages((prev) => [...prev, { role: 'assistant', text: reply }]);
    } catch {
      setError('The building is not answering. Is the backend running?');
    } finally {
      setPending(false);
    }
  }

  return (
    <section className="chat">
      <p className="chat__status" role="status">
        {backendDown && 'Backend unreachable'}
        {health &&
          `Backend ${health.status} · ${health.llm_enabled ? 'Claude' : 'offline mode'}` +
            (health.tracing_enabled ? ' · LangSmith tracing' : '')}
        {!health && !backendDown && 'Connecting…'}
      </p>

      <ol className="chat__log" aria-label="Conversation">
        {messages.map((m, i) => (
          <li key={i} className={`chat__msg chat__msg--${m.role}`}>
            {m.text}
          </li>
        ))}
      </ol>

      {error && (
        <p className="chat__error" role="alert">
          {error}
        </p>
      )}

      <form className="chat__form" onSubmit={handleSubmit}>
        <label htmlFor="chat-input" className="visually-hidden">
          Message
        </label>
        <input
          id="chat-input"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Where is the 3D printer?"
          autoComplete="off"
        />
        <button type="submit" disabled={pending || !input.trim()}>
          {pending ? 'Thinking…' : 'Send'}
        </button>
      </form>
    </section>
  );
}
