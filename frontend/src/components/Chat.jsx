import { useState, useRef, useEffect } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api } from "../api";

const SUGGESTIONS = [
  "How many casual leave days do I get per year?",
  "My VPN won't connect. What should I try?",
  "Who reports to Priya Sharma?",
  "Compare Q1 and Q2 2025 sales",
  "Show open tickets",
  "Give me a daily digest for Arjun Mehta",
];

// The free-tier backend sleeps when idle and takes up to a minute to wake.
const SLOW_NOTICE_MS = 8000;

export default function Chat() {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [slow, setSlow] = useState(false);
  const [error, setError] = useState(null);
  const sessionId = useRef(crypto.randomUUID());
  const bottomRef = useRef(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  async function send(question) {
    if (!question || loading) return;
    setMessages((m) => [...m, { role: "human", content: question }]);
    setInput("");
    setLoading(true);
    setError(null);
    const slowTimer = setTimeout(() => setSlow(true), SLOW_NOTICE_MS);
    try {
      const res = await api.ask(question, sessionId.current);
      sessionId.current = res.session_id;
      setMessages((m) => [
        ...m,
        { role: "assistant", content: res.answer, actionTaken: res.action_taken, sources: res.sources },
      ]);
    } catch (err) {
      setError(err.message);
    } finally {
      clearTimeout(slowTimer);
      setSlow(false);
      setLoading(false);
    }
  }

  function handleSubmit(e) {
    e.preventDefault();
    send(input.trim());
  }

  function newChat() {
    sessionId.current = crypto.randomUUID();
    setMessages([]);
    setError(null);
  }

  return (
    <div className="chat">
      <div className="chat-toolbar">
        <button type="button" className="link-button" onClick={newChat} disabled={loading || messages.length === 0}>
          New chat
        </button>
      </div>
      <div className="chat-messages">
        {messages.length === 0 && (
          <div className="chat-welcome">
            <p className="chat-empty">Ask about policies, raise a ticket, look up a colleague, or pull a report.</p>
            <div className="suggestions">
              {SUGGESTIONS.map((s) => (
                <button key={s} type="button" className="suggestion" onClick={() => send(s)}>
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`chat-bubble chat-bubble--${m.role}`}>
            <div className="chat-bubble-content">
              {m.role === "assistant" ? <Markdown remarkPlugins={[remarkGfm]}>{m.content}</Markdown> : m.content}
            </div>
            {m.actionTaken && <div className="chat-bubble-meta">tool: {m.actionTaken}</div>}
            {m.sources?.length > 0 && <div className="chat-bubble-meta">sources: {m.sources.join(", ")}</div>}
          </div>
        ))}
        {loading && (
          <div className="chat-bubble chat-bubble--assistant chat-bubble--loading">
            {slow ? "Waking up the server, this can take up to a minute…" : "Thinking…"}
          </div>
        )}
        <div ref={bottomRef} />
      </div>
      {error && <div className="chat-error">{error}</div>}
      <form className="chat-input" onSubmit={handleSubmit}>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask Nexus anything…"
          maxLength={2000}
          disabled={loading}
        />
        <button type="submit" disabled={loading || !input.trim()}>
          Send
        </button>
      </form>
    </div>
  );
}
