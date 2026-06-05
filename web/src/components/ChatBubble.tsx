import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { post, type ChatSession } from "../api";
import { ChatThread } from "./ChatThread";

export function ChatBubble() {
  const [open, setOpen] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const create = useMutation({
    mutationFn: () => post<ChatSession>("/api/chat/sessions"),
    onSuccess: (s) => setSessionId(s.id) });

  const toggle = () => {
    if (!open && !sessionId) create.mutate();
    setOpen((o) => !o);
  };
  return (
    <>
      {open && sessionId && (
        <div className="card" style={{ position: "fixed", right: 24, bottom: 90,
             width: 400, height: 520, zIndex: 40, display: "flex" }}>
          <ChatThread sessionId={sessionId} compact /></div>)}
      <button onClick={toggle} style={{ position: "fixed", right: 24, bottom: 24,
          width: 54, height: 54, borderRadius: 27, border: 0, fontSize: 22,
          background: "var(--green)", color: "#fff", cursor: "pointer",
          boxShadow: "var(--shadow)", zIndex: 40 }}>
        {open ? "✕" : "💬"}</button>
    </>
  );
}
