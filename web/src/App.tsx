import { Route, Routes } from "react-router-dom";
import { Suspense, lazy, useState } from "react";
import { TopBar } from "./components/TopBar";
import { ChatBubble } from "./components/ChatBubble";
import { ThemeProvider, useThemeState } from "./useTheme";

// Route-level code splitting: each page (and its heavy deps, e.g. recharts) is
// loaded on demand so the initial bundle stays small.
const Dashboard = lazy(() => import("./pages/Dashboard"));
const Transactions = lazy(() => import("./pages/Transactions"));
const Chat = lazy(() => import("./pages/Chat"));
const Settings = lazy(() => import("./pages/Settings"));

export default function App() {
  const [period, setPeriod] = useState<string>("");   // "" = current month
  const themeState = useThemeState();
  return (
    <ThemeProvider value={themeState}>
      <div className="shell">
        <TopBar period={period} onPeriod={setPeriod} />
        <Suspense fallback={<div className="muted" style={{ padding: 24 }}>Loading…</div>}>
          <Routes>
            <Route path="/" element={<Dashboard period={period} />} />
            <Route path="/transactions" element={<Transactions period={period} />} />
            <Route path="/chat" element={<Chat />} />
            <Route path="/settings" element={<Settings />} />
          </Routes>
        </Suspense>
        <ChatBubble />
      </div>
    </ThemeProvider>
  );
}
