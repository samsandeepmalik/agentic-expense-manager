import { Suspense, lazy, useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { get, streamChat, type ChatEvent, type UiSpec } from "../api";

// GenUI pulls in recharts; load it only when the agent actually renders a spec,
// keeping the chart library out of the initial bundle.
const GenUI = lazy(() => import("./GenUI").then((m) => ({ default: m.GenUI })));
// react-markdown is sizeable; load it lazily too — only when a bubble renders.
const Markdown = lazy(() => import("./Markdown").then((m) => ({ default: m.Markdown })));

interface Item { role: "user" | "assistant"; text: string;
  uiSpecs?: UiSpec[]; tools?: string[]; }

const EXAMPLES = [
  "What are my expenses this month?",
  "Add $12.50 coffee at Starbucks",
  "Show spending by category",
];

export function ChatThread({ sessionId, compact = false, readOnly = false }:
    { sessionId: string; compact?: boolean; readOnly?: boolean }) {
  const queryClient = useQueryClient();
  const [items, setItems] = useState<Item[]>([]);
  const [input, setInput] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const scroller = useRef<HTMLDivElement>(null);

  const history = useQuery({ queryKey: ["chat", sessionId],
    queryFn: () => get<{ messages: { role: string; content:
      { text: string; ui_specs?: UiSpec[] } }[] }>(`/api/chat/sessions/${sessionId}`) });

  useEffect(() => {
    if (history.data) setItems(history.data.messages.map((m) => ({
      role: m.role as "user" | "assistant", text: m.content.text,
      uiSpecs: m.content.ui_specs ?? [] })));
  }, [history.data]);
  useEffect(() => { scroller.current?.scrollTo(0, 1e9); }, [items]);

  async function send() {
    if (busy || (!input.trim() && !file)) return;
    const message = input.trim(); const attachment = file;
    setInput(""); setFile(null); setBusy(true);
    setItems((prev) => [...prev, { role: "user", text: message || (attachment ? `(file: ${attachment.name})` : "") },
                        { role: "assistant", text: "", uiSpecs: [], tools: [] }]);
    const applyLast = (fn: (i: Item) => Item) => setItems((prev) => {
      const next = [...prev]; next[next.length - 1] = fn(next[next.length - 1]); return next; });
    try {
      await streamChat(sessionId, message, attachment, (event: ChatEvent) => {
        if (event.type === "delta") applyLast((i) => ({ ...i, text: i.text + event.text }));
        else if (event.type === "tool" && event.status === "start")
          applyLast((i) => ({ ...i, tools: [...(i.tools ?? []), event.name] }));
        else if (event.type === "ui")
          applyLast((i) => ({ ...i, uiSpecs: [...(i.uiSpecs ?? []), event.spec] }));
        else if (event.type === "done") {
          applyLast((i) => ({ ...i, text: i.text || event.text }));
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
              : item.text || (busy && index === items.length - 1 ? "…" : "")}
            {(item.uiSpecs ?? []).length > 0 && (
              <Suspense fallback={<span className="muted">…</span>}>
                {(item.uiSpecs ?? []).map((spec, i) => <GenUI key={i} spec={spec} />)}
              </Suspense>)}
          </div>))}
      </div>
      {readOnly && (
        <p className="lbl muted" style={{ textAlign: "center", paddingTop: 8 }}>
          WhatsApp conversation — reply from your phone.</p>)}
      {!readOnly && <div className="row chat-inputrow">
        <label style={{ cursor: "pointer", fontSize: 20 }}
               title="Attach a receipt, or a CSV/Excel/PDF statement">📎
          <input type="file" accept="image/*,application/pdf,.csv,.xlsx,.xls" hidden
                 onChange={(e) => setFile(e.target.files?.[0] ?? null)} /></label>
        {file && <span className="muted">{file.name}</span>}
        <input className="grow" value={input} disabled={busy}
               placeholder="MESSAGE, RECEIPT OR STATEMENT…"
               onChange={(e) => setInput(e.target.value)}
               onKeyDown={(e) => e.key === "Enter" && send()} />
        <button className="primary" disabled={busy || (!input.trim() && !file)}
                onClick={send}>{busy ? "…" : "Send"}</button>
      </div>}
    </div>
  );
}
