import { Link, useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { get } from "../api";

function periodOptions(): { value: string; label: string }[] {
  const now = new Date();
  const options = [{ value: "", label: "This month" }];
  for (let back = 1; back <= 3; back++) {
    const d = new Date(now.getFullYear(), now.getMonth() - back, 1);
    const value = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
    options.push({ value, label: d.toLocaleString("en", { month: "long", year: "numeric" }) });
  }
  return [...options, { value: "last3", label: "Last 3 months" },
          { value: "last6", label: "Last 6 months" }, { value: "ytd", label: "Year to date" }];
}

export function TopBar({ period, onPeriod }:
    { period: string; onPeriod: (p: string) => void }) {
  const location = useLocation();
  const sync = useQuery({ queryKey: ["sync"], refetchInterval: 30000,
    queryFn: () => get<{ enabled: boolean; pending: number }>("/api/sync/status") });
  const links = [["/", "Dashboard"], ["/transactions", "Transactions"],
                 ["/chat", "Chat"], ["/settings", "Settings"]] as const;
  return (
    <header style={{ display: "flex", alignItems: "center", gap: 18, padding: "18px 0",
                     borderBottom: "1px solid #efe9de", marginBottom: 24 }}>
      <b style={{ fontSize: 19 }}>💰 Expense Manager</b>
      <nav style={{ display: "flex", gap: 4 }}>
        {links.map(([to, label]) => (
          <Link key={to} to={to} style={{ textDecoration: "none", padding: "6px 12px",
            borderRadius: 10, color: location.pathname === to ? "#fff" : "var(--text)",
            background: location.pathname === to ? "var(--green)" : "transparent" }}>
            {label}</Link>))}
      </nav>
      <span style={{ flex: 1 }} />
      <select value={period} onChange={(e) => onPeriod(e.target.value)}>
        {periodOptions().map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
      <span title={sync.data?.enabled ? `${sync.data.pending} pending` : "Google sync off"}
            style={{ width: 10, height: 10, borderRadius: 5,
                     background: !sync.data?.enabled ? "#cfc6b8"
                       : sync.data.pending ? "var(--amber)" : "var(--green)" }} />
    </header>
  );
}
