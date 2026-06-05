import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { get, streamChat, type ChatEvent, type UiSpec } from "../api";
import { GenUI } from "./GenUI";

interface Item { role: "user" | "assistant"; text: string;
  uiSpecs?: UiSpec[]; tools?: string[]; }

export function ChatThread({ sessionId, compact = false }:
    { sessionId: string; compact?: boolean }) {
  const queryClient = useQueryClient();
  const [items, setItems] = useState<Item[]>([]);
  const [input, setInput] = useState("");
  const [image, setImage] = useState<File | null>(null);
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
    if (busy || (!input.trim() && !image)) return;
    const message = input.trim(); const attachment = image;
    setInput(""); setImage(null); setBusy(true);
    setItems((prev) => [...prev, { role: "user", text: message || "(receipt image)" },
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
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <div ref={scroller} style={{ flex: 1, overflowY: "auto", display: "flex",
                                   flexDirection: "column", gap: 10, padding: 4 }}>
        {items.length === 0 && (
          <p className="muted" style={{ margin: "auto", textAlign: "center" }}>
            Ask “what are my expenses this month?” or drop a receipt photo.</p>)}
        {items.map((item, index) => (
          <div key={index} style={{
            alignSelf: item.role === "user" ? "flex-end" : "flex-start",
            background: item.role === "user" ? "var(--green)" : "#fff",
            color: item.role === "user" ? "#fff" : "var(--text)",
            borderRadius: 14, padding: "10px 14px",
            maxWidth: compact ? "95%" : "80%", whiteSpace: "pre-wrap",
            boxShadow: "var(--shadow)" }}>
            {(item.tools ?? []).length > 0 && (
              <div className="muted" style={{ fontSize: 11, marginBottom: 6 }}>
                ⚙ {(item.tools ?? []).join(" · ")}</div>)}
            {item.text || (busy && index === items.length - 1 ? "…" : "")}
            {(item.uiSpecs ?? []).map((spec, i) => <GenUI key={i} spec={spec} />)}
          </div>))}
      </div>
      <div style={{ display: "flex", gap: 8, paddingTop: 10 }}>
        <label style={{ cursor: "pointer", fontSize: 20, alignSelf: "center" }}>📷
          <input type="file" accept="image/*" hidden
                 onChange={(e) => setImage(e.target.files?.[0] ?? null)} /></label>
        {image && <span className="muted" style={{ alignSelf: "center" }}>{image.name}</span>}
        <input style={{ flex: 1 }} value={input} disabled={busy}
               placeholder="Message or receipt details…"
               onChange={(e) => setInput(e.target.value)}
               onKeyDown={(e) => e.key === "Enter" && send()} />
        <button className="primary" disabled={busy || (!input.trim() && !image)}
                onClick={send}>{busy ? "…" : "Send"}</button>
      </div>
    </div>
  );
}
