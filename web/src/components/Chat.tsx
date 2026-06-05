import { useRef, useState } from "react";
import { streamChat, type ChatEvent, type UiSpec } from "../api";
import { GenUI } from "./GenUI";

interface ChatItem {
  role: "user" | "assistant" | "status";
  text: string;
  imageName?: string;
  uiSpecs?: UiSpec[];
  tools?: string[];
}

export function Chat() {
  const [items, setItems] = useState<ChatItem[]>([]);
  const [input, setInput] = useState("");
  const [image, setImage] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const sessionRef = useRef<string>("");
  const fileRef = useRef<HTMLInputElement>(null);

  async function send() {
    if (busy || (!input.trim() && !image)) return;
    const message = input.trim();
    const attachedImage = image;
    setInput("");
    setImage(null);
    if (fileRef.current) fileRef.current.value = "";
    setBusy(true);

    setItems((previous) => [
      ...previous,
      { role: "user", text: message || "(receipt image)", imageName: attachedImage?.name },
      { role: "assistant", text: "", uiSpecs: [], tools: [] },
    ]);

    const apply = (updater: (item: ChatItem) => ChatItem) =>
      setItems((previous) => {
        const next = [...previous];
        next[next.length - 1] = updater(next[next.length - 1]);
        return next;
      });

    try {
      await streamChat(message, sessionRef.current, attachedImage, (event: ChatEvent) => {
        if (event.type === "session") sessionRef.current = event.session_id;
        else if (event.type === "delta")
          apply((item) => ({ ...item, text: item.text + event.text }));
        else if (event.type === "status")
          apply((item) => ({ ...item, tools: [...(item.tools ?? []), event.text] }));
        else if (event.type === "tool" && event.status === "start")
          apply((item) => ({ ...item, tools: [...(item.tools ?? []), event.name] }));
        else if (event.type === "ui")
          apply((item) => ({ ...item, uiSpecs: [...(item.uiSpecs ?? []), event.spec] }));
        else if (event.type === "done")
          apply((item) => ({ ...item, text: item.text || event.text }));
      });
    } catch (error) {
      apply((item) => ({ ...item, text: `${item.text}\n⚠ ${String(error)}` }));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="chat">
      <div className="chat-scroll">
        {items.length === 0 && (
          <div className="chat-empty">
            <p>Ask about your money or drop a receipt.</p>
            <p className="hint">
              “What are my expenses this month?” · “Show income vs expenses for the
              last 6 months” · “Spent $42.50 at Metro on groceries”
            </p>
          </div>
        )}
        {items.map((item, index) =>
          item.role === "user" ? (
            <div key={index} className="bubble user">
              {item.text}
              {item.imageName && <span className="attachment">📎 {item.imageName}</span>}
            </div>
          ) : (
            <div key={index} className="bubble assistant">
              {item.tools && item.tools.length > 0 && (
                <div className="tool-trail">
                  {item.tools.map((tool, toolIndex) => (
                    <span key={toolIndex} className="tool-chip">⚙ {tool}</span>
                  ))}
                </div>
              )}
              <div className="bubble-text">{item.text || (busy && index === items.length - 1 ? "…" : "")}</div>
              {(item.uiSpecs ?? []).map((spec, specIndex) => (
                <GenUI key={specIndex} spec={spec} />
              ))}
            </div>
          ),
        )}
      </div>
      <div className="chat-input">
        <label className="icon-button" title="Attach receipt image">
          📷
          <input
            ref={fileRef}
            type="file"
            accept="image/*"
            hidden
            onChange={(event) => setImage(event.target.files?.[0] ?? null)}
          />
        </label>
        {image && <span className="attachment">{image.name}</span>}
        <input
          value={input}
          placeholder="Message or receipt details…"
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={(event) => event.key === "Enter" && send()}
          disabled={busy}
        />
        <button onClick={send} disabled={busy || (!input.trim() && !image)}>
          {busy ? "…" : "Send"}
        </button>
      </div>
    </div>
  );
}
