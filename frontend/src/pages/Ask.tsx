import { FormEvent, ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { api, Json } from "../api";
import { Card, Icon } from "../components";

type Msg = { role: "user" | "assistant" | "notice"; content: string; error?: boolean; usage?: string };
type Snap = { id: string; time: string } | null;
type Model = { id: string; name: string; context_length?: number; prompt_per_m?: number | null; completion_per_m?: number | null };

/* Good, affordable families for explaining; the newest version of each found in OpenRouter's list is suggested. */
const SUGGESTED = [/^anthropic\/claude-sonnet-[\d.]+$/, /^anthropic\/claude-haiku-[\d.]+$/, /^openai\/gpt-[\d.]+-mini$/,
  /^google\/gemini-[\d.]+-flash$/];
function suggested(models: Model[]): Model[] {
  return SUGGESTED.map((re) => models.filter((m) => re.test(m.id))
    .sort((a, b) => b.id.localeCompare(a.id, undefined, { numeric: true }))[0]).filter(Boolean);
}
const STARTERS = [
  "Why are the fans on or off right now?",
  "What would need to change for the fans to turn on?",
  "Is outdoor air drier than the basement right now?",
  "What happened with the fans over the last few hours?",
];
const CHAT_KEY = "ventctl.chat";

const SNAP_KEY = "ventctl.chat.snapshot";

function loadChat(): Msg[] {
  try { return JSON.parse(sessionStorage.getItem(CHAT_KEY) || "[]"); } catch { return []; }
}
function loadSnap(): Snap {
  try { return JSON.parse(sessionStorage.getItem(SNAP_KEY) || "null"); } catch { return null; }
}
function clock(iso: string) {
  return new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}
/* "12,400 tokens in (11,900 reused from cache) · 210 out · $0.0041", from OpenRouter's usage report */
function usageLine(u: Json): string | undefined {
  if (!u?.prompt_tokens) return undefined;
  const cached = u.prompt_tokens_details?.cached_tokens || 0;
  const parts = [`${u.prompt_tokens.toLocaleString()} tokens in${cached ? ` (${cached.toLocaleString()} reused from cache)` : ""}`,
    `${(u.completion_tokens || 0).toLocaleString()} out`];
  if (typeof u.cost === "number") parts.push(`$${u.cost < 0.01 ? u.cost.toFixed(4) : u.cost.toFixed(3)}`);
  return parts.join(" · ");
}

/* Tiny, safe formatter for the model's answers: paragraphs, bullet lists and **bold**. No HTML injection. */
function inline(s: string): ReactNode[] {
  return s.split(/(\*\*[^*]+\*\*|\*[^*\s][^*]*\*)/g).map((p, i) =>
    p.startsWith("**") && p.endsWith("**") && p.length > 4 ? <strong key={i}>{p.slice(2, -2)}</strong>
      : p.length > 2 && p.startsWith("*") && p.endsWith("*") ? <em key={i}>{p.slice(1, -1)}</em> : <span key={i}>{p}</span>);
}
function Formatted({ text }: { text: string }) {
  const blocks = text.split(/\n{2,}/);
  return <>{blocks.map((b, i) => {
    const lines = b.split("\n").filter((l) => l.trim());
    if (lines.length && lines.every((l) => /^\s*([-*•]|\d+\.)\s+/.test(l))) {
      return <ul key={i}>{lines.map((l, j) => <li key={j}>{inline(l.replace(/^\s*([-*•]|\d+\.)\s+/, ""))}</li>)}</ul>;
    }
    return <p key={i}>{lines.map((l, j) => <span key={j}>{j > 0 && <br />}{inline(l.replace(/^#+\s*/, ""))}</span>)}</p>;
  })}</>;
}

function price(m: Model) {
  if (m.prompt_per_m == null) return "";
  if (!m.prompt_per_m && !m.completion_per_m) return " · free";
  return ` · $${m.prompt_per_m}/$${m.completion_per_m} per M tokens`;
}

export default function Ask() {
  const [settings, setSettings] = useState<Json>(null);
  const [keyDraft, setKeyDraft] = useState("");
  const [editingKey, setEditingKey] = useState(false);
  const [models, setModels] = useState<Model[]>([]);
  const [modelsErr, setModelsErr] = useState("");
  const [filter, setFilter] = useState("");
  const [model, setModel] = useState("");
  const [msgs, setMsgs] = useState<Msg[]>(loadChat);
  const [snap, setSnap] = useState<Snap>(loadSnap);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api.chatSettings().then((s) => { setSettings(s); setModel(s.model); }).catch((e) => setErr(String(e.message || e)));
    api.chatModels().then((r) => setModels(r.models)).catch((e) => setModelsErr(String(e.message || e)));
  }, []);
  useEffect(() => {
    try {
      sessionStorage.setItem(CHAT_KEY, JSON.stringify(msgs));
      sessionStorage.setItem(SNAP_KEY, JSON.stringify(snap));
    } catch { /* storage unavailable */ }
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [msgs, busy, snap]);

  const shown = useMemo(() => {
    const f = filter.trim().toLowerCase();
    const list = f ? models.filter((m) => m.id.toLowerCase().includes(f) || m.name.toLowerCase().includes(f)) : models;
    return { suggested: suggested(models).filter((m) => list.includes(m)), all: list };
  }, [models, filter]);
  const current = models.find((m) => m.id === model);
  // the saved model was retired or renamed on OpenRouter: switch to a suggested one
  useEffect(() => {
    if (models.length && model && !current && suggested(models)[0]) pickModel(suggested(models)[0].id);
  }, [models]); // eslint-disable-line react-hooks/exhaustive-deps

  async function saveKey(e: FormEvent) {
    e.preventDefault();
    if (!keyDraft.trim()) return;
    try {
      const s = await api.saveChatSettings({ api_key: keyDraft.trim() });
      setSettings(s); setKeyDraft(""); setEditingKey(false); setErr("");
    } catch (e: Json) { setErr(String(e.message || e)); }
  }
  async function removeKey() {
    setSettings(await api.saveChatSettings({ api_key: "" }));
    setEditingKey(true);
  }
  async function pickModel(id: string) {
    setModel(id);
    try { setSettings(await api.saveChatSettings({ model: id })); } catch { /* kept for this session */ }
  }

  async function send(text: string) {
    const q = text.trim();
    if (!q || busy) return;
    const next: Msg[] = [...msgs, { role: "user", content: q }];
    setMsgs(next); setInput(""); setBusy(true); setErr("");
    try {
      const history = next.filter((m) => !m.error && m.role !== "notice").map(({ role, content }) => ({ role, content }));
      const r = await api.chat(history, model || undefined, snap?.id);
      if (r.snapshot_id && r.snapshot_id !== snap?.id) setSnap({ id: r.snapshot_id, time: r.snapshot_time });
      setMsgs([...next, { role: "assistant", content: r.reply || "(no answer)", usage: usageLine(r.usage) }]);
    } catch (e: Json) {
      setMsgs([...next, { role: "assistant", content: String(e.message || e), error: true }]);
    } finally { setBusy(false); }
  }

  function refreshReadings() {
    setSnap(null);
    setMsgs([...msgs, { role: "notice", content: "Fresh readings will be taken with your next question." }]);
  }
  function newConversation() {
    setMsgs([]);
    setSnap(null);
  }

  const needKey = settings && (!settings.has_key || editingKey);

  return (
    <div className="grid ask">
      <Card title={<span className="row"><Icon name="info" /> Ask why the fans are on or off</span>}>
        <p className="muted" style={{ marginTop: 0 }}>
          Chat with an AI model about what the controller is doing. Your first question takes a snapshot of the current readings,
          the controller's latest decision and reasons, the last few hours of history, recent events, your settings and a
          plain-language summary of the rules. Follow-up questions reuse that same snapshot, which keeps them cheaper.
          Press Refresh readings for up-to-date numbers. It can only explain: it can't turn fans on or off or change settings.
        </p>
        {needKey ? (
          <form className="row" onSubmit={saveKey}>
            <div className="field" style={{ flex: "1 1 280px" }}>
              <label htmlFor="orkey">OpenRouter API key</label>
              <input id="orkey" type="password" autoComplete="off" placeholder="sk-or-v1-…" value={keyDraft}
                onChange={(e) => setKeyDraft(e.target.value)} />
              <span className="hint">Get one at openrouter.ai/keys. It's saved only on this computer (in the app's data
                folder) and is only ever sent to OpenRouter.</span>
            </div>
            <button className="btn primary" type="submit" disabled={!keyDraft.trim()}>Save key</button>
            {settings?.has_key && <button className="btn" type="button" onClick={() => setEditingKey(false)}>Cancel</button>}
          </form>
        ) : settings && (
          <div className="row">
            <span className="badge good">Key saved {settings.key_hint}</span>
            <button className="btn link" onClick={() => setEditingKey(true)}>Replace</button>
            <button className="btn link" onClick={removeKey}>Remove</button>
          </div>
        )}
        <div className="row ask-model">
          <div className="field" style={{ flex: "1 1 160px" }}>
            <label htmlFor="mfilter">Model</label>
            <input id="mfilter" placeholder="Search models (e.g. claude, gpt, gemini)" value={filter}
              onChange={(e) => setFilter(e.target.value)} />
          </div>
          <div className="field" style={{ flex: "2 1 280px" }}>
            <select id="msel" aria-label="Model" value={model} onChange={(e) => pickModel(e.target.value)}>
              {!current && model && <option value={model}>{model}</option>}
              {shown.suggested.length > 0 && <optgroup label="Suggested">
                {shown.suggested.map((m) => <option key={"s" + m.id} value={m.id}>{m.name}{price(m)}</option>)}
              </optgroup>}
              <optgroup label={`All models (${shown.all.length})`}>
                {shown.all.map((m) => <option key={m.id} value={m.id}>{m.name}{price(m)}</option>)}
              </optgroup>
            </select>
          </div>
        </div>
        {modelsErr && <div className="faint">Couldn't load the model list ({modelsErr}). The saved model still works.</div>}
        {err && <div className="alert warning" style={{ marginTop: 8 }}><span className="icon"><Icon name="alert" /></span><div>{err}</div></div>}
      </Card>

      <Card title="Conversation" right={msgs.length > 0 && <span className="row">
        {snap && <span className="faint">Readings as of {clock(snap.time)}</span>}
        {snap && <button className="btn" onClick={refreshReadings} disabled={busy}>Refresh readings</button>}
        <button className="btn" onClick={newConversation} disabled={busy}>New conversation</button></span>}>
        <div className="chat-log">
          {msgs.length === 0 && (
            <div className="chat-starters">
              <span className="faint">Try one of these:</span>
              {STARTERS.map((s) => <button key={s} className="btn" disabled={busy || !settings?.has_key} onClick={() => send(s)}>{s}</button>)}
            </div>
          )}
          {msgs.map((m, i) => m.role === "notice"
            ? <div key={i} className="chat-notice faint">{m.content}</div>
            : (
              <div key={i} className={`chat-msg ${m.role}${m.error ? " error" : ""}`}>
                {m.role === "assistant" && !m.error ? <Formatted text={m.content} /> : <p>{m.content}</p>}
                {m.usage && <div className="chat-usage faint">{m.usage}</div>}
              </div>
            ))}
          {busy && <div className="chat-msg assistant faint"><p>{snap ? "Thinking…" : "Looking at the current readings…"}</p></div>}
          <div ref={endRef} />
        </div>
        <form className="chat-input" onSubmit={(e) => { e.preventDefault(); send(input); }}>
          <textarea rows={2} placeholder={settings?.has_key ? "Ask a question, e.g. why did the fans stop an hour ago?" : "Save your OpenRouter key above first"}
            value={input} disabled={!settings?.has_key}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(input); } }} />
          <button className="btn primary" type="submit" disabled={busy || !input.trim() || !settings?.has_key}>Ask</button>
        </form>
        <div className="faint" style={{ marginTop: 6 }}>
          Answers come from an AI model and can be wrong. The Dashboard shows the controller's own checks.{" "}
          <a href={`/api/chat/context${snap ? `?snapshot_id=${snap.id}` : ""}`} target="_blank" rel="noreferrer">See exactly what the model is sent</a>.
        </div>
      </Card>
    </div>
  );
}
