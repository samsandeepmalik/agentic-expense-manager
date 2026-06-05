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
    <div style={{ display: "flex", gap: 16, height: "calc(100vh - 130px)" }}>
      <div className="card" style={{ width: 260, overflowY: "auto" }}>
        <button className="primary" style={{ width: "100%" }}
                onClick={() => create.mutate()}>＋ New chat</button>
        {(sessions.data ?? []).map((s) => (
          <div key={s.id} onClick={() => setActive(s.id)}
               style={{ padding: "10px 8px", borderRadius: 10, cursor: "pointer",
                        marginTop: 6, display: "flex", justifyContent: "space-between",
                        background: active === s.id ? "var(--green-soft)" : "transparent" }}>
            <span style={{ overflow: "hidden", textOverflow: "ellipsis",
                           whiteSpace: "nowrap" }}>{s.title}</span>
            <button className="ghost" style={{ color: "var(--amber)" }}
                    onClick={(e) => { e.stopPropagation(); remove.mutate(s.id); }}>✕</button>
          </div>))}
        {(waSessions.data ?? []).length > 0 && (
          <p className="muted" style={{ margin: "14px 0 2px" }}>WhatsApp</p>)}
        {(waSessions.data ?? []).map((s) => (
          <div key={s.id} onClick={() => setActive(s.id)}
               style={{ padding: "10px 8px", borderRadius: 10, cursor: "pointer",
                        marginTop: 6, display: "flex", gap: 6, alignItems: "center",
                        background: active === s.id ? "var(--green-soft)" : "transparent" }}>
            <span>💬</span>
            <span style={{ overflow: "hidden", textOverflow: "ellipsis",
                           whiteSpace: "nowrap" }}>{s.title}</span>
          </div>))}
      </div>
      <div className="card" style={{ flex: 1 }}>
        {active ? <ChatThread key={active} sessionId={active} readOnly={isWa} />
          : <p className="muted" style={{ textAlign: "center", marginTop: 80 }}>
              Pick a chat or start a new one.</p>}
      </div>
    </div>
  );
}
