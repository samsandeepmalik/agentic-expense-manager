import { Suspense, lazy, useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { get, streamChat, type ChatEvent, type UiSpec } from "../api";

// GenUI pulls in recharts; load it only when the agent actually renders a spec,
// keeping the chart library out of the initial bundle.
const GenUI = lazy(() => import("./GenUI").then((m) => ({ default: m.GenUI })));
// react-markdown is sizeable; load it lazily too — only when a bubble renders.
const Markdown = lazy(() => import("./Markdown").then((m) => ({ default: m.Markdown })));

interface Item { role: "user" | "assistant"; text: string;
  uiSpecs?: UiSpec[]; tools?: string[]; status?: string; }
interface Pending { file: File; url: string | null; }

const EXAMPLES = [
  "What are my expenses this month?",
  "Add $12.50 coffee at Starbucks",
  "Show spending by category",
];

const MAX_FILES = 10;
const STATEMENT_EXT = /\.(csv|xlsx|xls)$/i;
const isStatement = (f: File) => STATEMENT_EXT.test(f.name);

export function ChatThread({ sessionId, compact = false, readOnly = false }:
    { sessionId: string; compact?: boolean; readOnly?: boolean }) {
  const queryClient = useQueryClient();
  const [items, setItems] = useState<Item[]>([]);
  const [input, setInput] = useState("");
  const [pending, setPending] = useState<Pending[]>([]);
  const [attachError, setAttachError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const scroller = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const pendingRef = useRef<Pending[]>(pending);
  pendingRef.current = pending;

  // Auto-grow the textarea up to the CSS max-height (~6 lines), then it scrolls internally.
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [input]);

  // Revoke any still-pending object URLs on unmount (removal/send already revoke their own).
  useEffect(() => () => {
    for (const p of pendingRef.current) if (p.url) URL.revokeObjectURL(p.url);
  }, []);

  const history = useQuery({ queryKey: ["chat", sessionId],
    queryFn: () => get<{ messages: { role: string; content:
      { text: string; ui_specs?: UiSpec[] } }[] }>(`/api/chat/sessions/${sessionId}`) });

  useEffect(() => {
    if (history.data) setItems(history.data.messages.map((m) => ({
      role: m.role as "user" | "assistant", text: m.content.text,
      uiSpecs: m.content.ui_specs ?? [] })));
  }, [history.data]);
  useEffect(() => { scroller.current?.scrollTo(0, 1e9); }, [items]);

  function onPickFiles(e: React.ChangeEvent<HTMLInputElement>) {
    const picked = Array.from(e.target.files ?? []);
    e.target.value = ""; // allow re-picking the same file later
    if (picked.length === 0) return;
    const combined = [...pending.map((p) => p.file), ...picked];
    if (combined.length > MAX_FILES) {
      setAttachError(`Max ${MAX_FILES} files per message.`); return;
    }
    if (combined.length > 1 && combined.some(isStatement)) {
      setAttachError("Attach a statement on its own, separate from receipts."); return;
    }
    setAttachError(null);
    setPending((prev) => [...prev, ...picked.map((f) => ({
      file: f, url: f.type.startsWith("image/") ? URL.createObjectURL(f) : null }))]);
  }

  function removePending(index: number) {
    setAttachError(null);
    setPending((prev) => {
      const target = prev[index];
      if (target?.url) URL.revokeObjectURL(target.url);
      return prev.filter((_, i) => i !== index);
    });
  }

  async function send() {
    if (busy || (!input.trim() && pending.length === 0)) return;
    const message = input.trim(); const attachments = pending.map((p) => p.file);
    for (const p of pending) if (p.url) URL.revokeObjectURL(p.url);
    setInput(""); setPending([]); setAttachError(null); setBusy(true);
    setItems((prev) => [...prev, { role: "user", text: message || (attachments.length
                          ? `(files: ${attachments.map((f) => f.name).join(", ")})` : "") },
                        { role: "assistant", text: "", uiSpecs: [], tools: [] }]);
    const applyLast = (fn: (i: Item) => Item) => setItems((prev) => {
      const next = [...prev]; next[next.length - 1] = fn(next[next.length - 1]); return next; });
    try {
      await streamChat(sessionId, message, attachments, (event: ChatEvent) => {
        if (event.type === "status")
          applyLast((i) => ({ ...i, status: event.text }));
        else if (event.type === "delta")
          applyLast((i) => ({ ...i, text: i.text + event.text, status: undefined }));
        else if (event.type === "tool" && event.status === "start")
          applyLast((i) => ({ ...i, tools: [...(i.tools ?? []), event.name], status: undefined }));
        else if (event.type === "ui")
          applyLast((i) => ({ ...i, uiSpecs: [...(i.uiSpecs ?? []), event.spec] }));
        else if (event.type === "done") {
          applyLast((i) => ({ ...i, text: i.text || event.text, status: undefined }));
          queryClient.invalidateQueries({ queryKey: ["dashboard"] });
          queryClient.invalidateQueries({ queryKey: ["transactions"] });
          queryClient.invalidateQueries({ queryKey: ["chat-sessions"] });
        }
      });
    } catch (err) {
      applyLast((i) => ({ ...i, text: `${i.text}\n⚠ ${String(err)}` }));
    } finally { setBusy(false); }
  }

  return (
    <div className="grow" style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <div ref={scroller} className="chat-scroll">
        {items.length === 0 && !readOnly && (
          <div className="chat-empty">
            <p className="mono chat-prompt">&gt; ASK ANYTHING ABOUT YOUR MONEY</p>
            <div className="row" style={{ flexWrap: "wrap", justifyContent: "center" }}>
              {EXAMPLES.map((q) => (
                <button key={q} className="ghost" onClick={() => setInput(q)}>{q}</button>))}
            </div>
          </div>)}
        {items.length === 0 && readOnly && (
          <p className="muted" style={{ margin: "auto" }}>No messages yet.</p>)}
        {items.map((item, index) => (
          <div key={index}
               className={`bubble ${item.role === "user" ? "user" : "agent"}${compact ? " compact" : ""}`}>
            <div className="bubble-meta lbl">
              {item.role === "user" ? "You" : "Agent"}
              {(item.tools ?? []).length > 0 && <> · ⚙ {(item.tools ?? []).join(" · ")}</>}
            </div>
            {item.role === "assistant" && item.text
              ? <Suspense fallback={<span>{item.text}</span>}>
                  <Markdown>{item.text}</Markdown>
                </Suspense>
              : item.text || (busy && index === items.length - 1 ? (item.status ?? "…") : "")}
            {(item.uiSpecs ?? []).length > 0 && (
              <Suspense fallback={<span className="muted">…</span>}>
                {(item.uiSpecs ?? []).map((spec, i) => <GenUI key={i} spec={spec} />)}
              </Suspense>)}
          </div>))}
      </div>
      {readOnly && (
        <p className="lbl muted" style={{ textAlign: "center", paddingTop: 8 }}>
          WhatsApp conversation — reply from your phone.</p>)}
      {!readOnly && <div className="composer">
        {pending.length > 0 && <div className="composer-thumbs">
          {pending.map((p, i) => (
            <div className="composer-thumb" key={i}>
              {p.url
                ? <img src={p.url} alt={p.file.name} />
                : <div className="composer-thumb-generic">
                    <span className="composer-thumb-icon">📄</span>
                    <span className="composer-thumb-name">{p.file.name}</span>
                  </div>}
              <button type="button" className="composer-thumb-x"
                      aria-label={`Remove ${p.file.name}`} onClick={() => removePending(i)}>✕</button>
            </div>))}
        </div>}
        {attachError && <p className="composer-error mono">{attachError}</p>}
        <textarea ref={textareaRef} className="composer-textarea" rows={1} value={input} disabled={busy}
                  placeholder="MESSAGE, RECEIPT OR STATEMENT…"
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
                  }} />
        <div className="composer-actions">
          <label className="composer-attach" title="Attach a receipt, or a CSV/Excel/PDF statement">+
            <input type="file" multiple accept="image/*,application/pdf,.csv,.xlsx,.xls" hidden
                   onChange={onPickFiles} />
          </label>
          <span className="grow" />
          <button type="button" className="composer-send" aria-label="Send"
                  disabled={busy || (!input.trim() && pending.length === 0)}
                  onClick={send}>{busy ? "…" : "↑"}</button>
        </div>
      </div>}
    </div>
  );
}
