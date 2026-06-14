import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { del, get, post, type ChatSession } from "../api";
import { ChatThread } from "../components/ChatThread";

export default function Chat() {
  const queryClient = useQueryClient();
  const [active, setActive] = useState<string | null>(null);
  const sessions = useQuery({ queryKey: ["chat-sessions"],
    queryFn: () => get<ChatSession[]>("/api/chat/sessions") });
  const waSessions = useQuery({ queryKey: ["wa-chat-sessions"], refetchInterval: 5000,
    queryFn: () => get<ChatSession[]>("/api/chat/sessions?channel=whatsapp") });
  const isWa = active?.startsWith("wa:") ?? false;
  const create = useMutation({
    mutationFn: () => post<ChatSession>("/api/chat/sessions"),
    onSuccess: (s) => {
      queryClient.invalidateQueries({ queryKey: ["chat-sessions"] });
      setActive(s.id);
    } });
  const remove = useMutation({
    mutationFn: (id: string) => del(`/api/chat/sessions/${id}`),
    onSuccess: (_, id) => {
      queryClient.invalidateQueries({ queryKey: ["chat-sessions"] });
      if (active === id) setActive(null);
    } });

  return (
    <div className="row" style={{ alignItems: "stretch", height: "calc(100vh - 130px)" }}>
      <div className="card" style={{ width: 260, overflowY: "auto" }}>
        <button className="primary" style={{ width: "100%" }}
                onClick={() => create.mutate()}>+ New chat</button>
        {(sessions.data ?? []).length > 0 && (
          <p className="lbl muted chat-side-head">
            Chats <span className="mono">{sessions.data!.length}</span></p>)}
        {(sessions.data ?? []).map((s) => (
          <div key={s.id} onClick={() => setActive(s.id)}
               className={`chatitem${active === s.id ? " invert" : ""}`}>
            <span className="chatitem-title">{s.title}</span>
            <button className="ghost danger del" aria-label="Delete chat"
                    onClick={(e) => { e.stopPropagation(); remove.mutate(s.id); }}>✕</button>
          </div>))}
        {(waSessions.data ?? []).length > 0 && (
          <p className="lbl muted chat-side-head">
            WhatsApp <span className="mono">{waSessions.data!.length}</span></p>)}
        {(waSessions.data ?? []).map((s) => (
          <div key={s.id} onClick={() => setActive(s.id)}
               className={`chatitem${active === s.id ? " invert" : ""}`}>
            <span>💬</span>
            <span className="chatitem-title grow">{s.title}</span>
          </div>))}
      </div>
      <div className="card grow" style={{ display: "flex" }}>
        {active ? <ChatThread key={active} sessionId={active} readOnly={isWa} />
          : <div className="chat-empty">
              <p className="mono chat-prompt">&gt; ASK ANYTHING ABOUT YOUR MONEY_</p>
              <p className="muted">Record expenses, query totals, drop receipt photos.</p>
              <button className="primary" onClick={() => create.mutate()}>
                + Start a chat</button>
            </div>}
      </div>
    </div>
  );
}
