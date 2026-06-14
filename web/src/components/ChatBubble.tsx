import { useState } from "react";
import { useLocation } from "react-router-dom";
import { useMutation } from "@tanstack/react-query";
import { post, type ChatSession } from "../api";
import { ChatThread } from "./ChatThread";

export function ChatBubble() {
  const location = useLocation();
  const [open, setOpen] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const create = useMutation({
    mutationFn: () => post<ChatSession>("/api/chat/sessions"),
    onSuccess: (s) => setSessionId(s.id) });

  if (location.pathname === "/chat") return null;  // redundant on the Chat page

  const toggle = () => {
    if (!open && !sessionId) create.mutate();
    setOpen((o) => !o);
  };
  return (
    <>
      {open && sessionId && (
        <div className="card fab-panel">
          <ChatThread sessionId={sessionId} compact /></div>)}
      <button className="fab" aria-label={open ? "Close chat" : "Open chat"}
              onClick={toggle}>{open ? "✕" : "💬"}</button>
    </>
  );
}
