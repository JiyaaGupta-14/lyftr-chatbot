import { useState, useRef, useEffect } from "react";
import "./index.css";

const API = import.meta.env.VITE_API_URL || "http://localhost:8000";
const newId = () => "s-" + Math.random().toString(36).slice(2, 10);

export default function App() {
  const [sessionId] = useState(newId);
  const [input, setInput] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [msgs, setMsgs] = useState([]);
  const [loading, setLoading] = useState(false);
  const endRef = useRef(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [msgs, loading]);

  async function send() {
    const text = input.trim();
    if (!text || loading) return;
    setInput("");
    setMsgs((m) => [...m, { role: "user", content: text }]);
    setLoading(true);
    try {
      const body = { session_id: sessionId, message: text };
      if (systemPrompt.trim()) body.system_prompt = systemPrompt.trim();
      const res = await fetch(`${API}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const msg =
          typeof data.detail === "string"
            ? data.detail
            : "Invalid request. Messages must be 1-4000 characters.";
        setMsgs((m) => [...m, { role: "error", content: msg }]);
      } else {
        setMsgs((m) => [...m, { role: "assistant", content: data.reply, meta: data.metadata }]);
      }
    } catch {
      setMsgs((m) => [...m, { role: "error", content: "Could not reach the server. Please try again." }]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="app">
      <h1>LLM Chatbot</h1>
      <input
        data-testid="system-prompt-input"
        className="sys"
        placeholder="Optional system prompt for this session"
        value={systemPrompt}
        onChange={(e) => setSystemPrompt(e.target.value)}
      />
      <div className="chat">
        {msgs.map((m, i) =>
          m.role === "user" ? (
            <div key={i} className="bubble user">{m.content}</div>
          ) : m.role === "error" ? (
            <div key={i} className="bubble error">{m.content}</div>
          ) : (
            <div key={i} data-testid="assistant-message" className="bubble bot">
              <div>{m.content}</div>
              <div className="meta">
                <span>Source: <b data-testid="meta-source">{m.meta.resolution_source}</b></span>
                <span>Model: <b data-testid="meta-model">{m.meta.model}</b></span>
                <span>Latency: <b data-testid="meta-latency">{m.meta.latency_ms} ms</b></span>
                <span>In: <b data-testid="meta-input-tokens">{m.meta.input_tokens}</b></span>
                <span>Out: <b data-testid="meta-output-tokens">{m.meta.output_tokens}</b></span>
              </div>
            </div>
          )
        )}
        {loading && <div className="bubble bot">Thinking...</div>}
        <div ref={endRef} />
      </div>
      <div className="row">
        <input
          data-testid="chat-input"
          placeholder="Type a message"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send()}
        />
        <button data-testid="send-button" onClick={send} disabled={loading}>Send</button>
      </div>
    </div>
  );
}