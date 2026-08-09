import { Route, Routes } from "react-router-dom";
import { Suspense, lazy, useState, useEffect } from "react";
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
  const [customFrom, setCustomFrom] = useState("");
  const [customTo, setCustomTo] = useState("");
  const themeState = useThemeState();

  // An applied custom range looks like "YYYY-MM-DD:YYYY-MM-DD" (set by the Apply button).
  const isAppliedRange = /^\d{4}-\d{2}-\d{2}:\d{4}-\d{2}-\d{2}$/.test(period);

  // Year presets (e.g. "2026-01-01:2026-12-31") are named options in the dropdown —
  // don't show the custom panel or replace the label with "Custom range…" for them.
  const isYearPreset = /^\d{4}-01-01:\d{4}-12-31$/.test(period);

  // When user picks a named preset (not "custom" and not an applied range), clear the inputs.
  useEffect(() => {
    if (period !== "custom" && !isAppliedRange) {
      setCustomFrom("");
      setCustomTo("");
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [period]);

  // Keep the custom panel open when a range is applied so users can adjust it,
  // but year presets are self-contained — no panel needed.
  const showCustomPanel = (period === "custom" || isAppliedRange) && !isYearPreset;

  // Show "custom" in the TopBar select whenever a user-typed applied range is active.
  // Year presets have their own option value so keep them as-is.
  const selectPeriod = isAppliedRange && !isYearPreset ? "custom" : period;

  // While "custom" is selected but no range applied yet, fall back to current month.
  const apiPeriod = period === "custom" ? "" : period;

  return (
    <ThemeProvider value={themeState}>
      <div className="shell">
        <TopBar period={selectPeriod} onPeriod={setPeriod} />
        <div style={{ display: showCustomPanel ? "flex" : "none", gap: "0.5rem",
            padding: "0.5rem 1rem", background: "var(--bg-card, #ede8e0)",
            alignItems: "center", flexWrap: "wrap" }}>
          <label style={{ display: "flex", gap: "0.25rem", alignItems: "center" }}>
            From <input type="date" value={customFrom} onChange={(e) => setCustomFrom(e.target.value)} />
          </label>
          <label style={{ display: "flex", gap: "0.25rem", alignItems: "center" }}>
            To <input type="date" value={customTo} onChange={(e) => setCustomTo(e.target.value)} />
          </label>
          {customFrom && customTo && customFrom > customTo && (
            <span className="neg" style={{ fontSize: "0.85em" }}>From must be before To</span>
          )}
          <button className="seg"
            disabled={!customFrom || !customTo || customFrom > customTo}
            onClick={() => setPeriod(customFrom + ":" + customTo)}>
            Apply
          </button>
        </div>
        <Suspense fallback={<div className="muted" style={{ padding: 24 }}>Loading…</div>}>
          <Routes>
            <Route path="/" element={<Dashboard period={apiPeriod} />} />
            <Route path="/transactions" element={<Transactions period={apiPeriod} />} />
            <Route path="/chat" element={<Chat />} />
            <Route path="/settings" element={<Settings />} />
          </Routes>
        </Suspense>
        <ChatBubble />
      </div>
    </ThemeProvider>
  );
}
