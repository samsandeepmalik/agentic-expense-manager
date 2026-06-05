import { Route, Routes } from "react-router-dom";
import { useState } from "react";
import { TopBar } from "./components/TopBar";
import { ChatBubble } from "./components/ChatBubble";
import Dashboard from "./pages/Dashboard";
import Transactions from "./pages/Transactions";
import Chat from "./pages/Chat";
import Settings from "./pages/Settings";

export default function App() {
  const [period, setPeriod] = useState<string>("");   // "" = current month
  return (
    <div style={{ maxWidth: 1180, margin: "0 auto", padding: "0 24px 48px" }}>
      <TopBar period={period} onPeriod={setPeriod} />
      <Routes>
        <Route path="/" element={<Dashboard period={period} />} />
        <Route path="/transactions" element={<Transactions period={period} />} />
        <Route path="/chat" element={<Chat />} />
        <Route path="/settings" element={<Settings />} />
      </Routes>
      <ChatBubble />
    </div>
  );
}
