import { Link, useLocation } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { get, post, type Profile } from "../api";
import { useTheme } from "../useTheme";

function periodOptions(): { value: string; label: string }[] {
  const now = new Date();
  const options = [{ value: "", label: "This month" }];
  for (let back = 1; back <= 3; back++) {
    const d = new Date(now.getFullYear(), now.getMonth() - back, 1);
    const value = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
    options.push({ value, label: d.toLocaleString("en", { month: "long", year: "numeric" }) });
  }
  options.push(
    { value: "last3", label: "Last 3 months" },
    { value: "last6", label: "Last 6 months" },
    { value: "ytd", label: "Year to date" },
  );
  // Year presets: current year + 4 prior years
  for (let y = now.getFullYear(); y >= now.getFullYear() - 4; y--) {
    options.push({ value: `${y}-01-01:${y}-12-31`, label: String(y) });
  }
  options.push({ value: "custom", label: "Custom range…" });
  return options;
}

export function TopBar({ period, onPeriod }:
    { period: string; onPeriod: (p: string) => void }) {
  const location = useLocation();
  const queryClient = useQueryClient();
  const { theme, toggle } = useTheme();
  const profiles = useQuery({ queryKey: ["profiles"],
    queryFn: () => get<Profile[]>("/api/profiles") });
  const activate = useMutation({
    mutationFn: (id: number) => post(`/api/profiles/${id}/activate`),
    onSuccess: () => queryClient.invalidateQueries() });  // everything is per-profile
  const active = profiles.data?.find((p) => p.active);
  const sync = useQuery({ queryKey: ["sync"], refetchInterval: 30000,
    queryFn: () => get<{ enabled: boolean; pending: number; prior_pending: number }>("/api/sync/status") });
  const links = [["/", "Dashboard"], ["/transactions", "Transactions"],
                 ["/chat", "Chat"], ["/settings", "Settings"]] as const;
  return (
    <header className="masthead">
      <b className="brand">Expense&nbsp;Mgr</b>
      <nav className="navsegs">
        {links.map(([to, label]) => (
          <Link key={to} to={to}
                className={`seg${location.pathname === to ? " invert" : ""}`}>
            {label}</Link>))}
      </nav>
      <span className="grow" />
      {(profiles.data?.length ?? 0) > 1 && (
        <select value={active?.id ?? profiles.data?.[0]?.id}
                onChange={(e) => activate.mutate(Number(e.target.value))}>
          {profiles.data?.map((p) => (
            <option key={p.id} value={p.id}>
              {p.kind === "incorporation" ? "🏢" : "👤"} {p.name}</option>))}
        </select>)}
      <select value={period} onChange={(e) => onPeriod(e.target.value)}>
        {periodOptions().map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
      <button className="seg" title="Toggle theme"
              aria-label={theme === "light" ? "Switch to dark theme" : "Switch to light theme"}
              onClick={toggle}>
        {theme === "light" ? "☾" : "☀"}</button>
      <span className="syncdot"
            title={sync.data?.enabled
              ? [sync.data.pending ? `${sync.data.pending} pending` : "",
                 sync.data.prior_pending ? `${sync.data.prior_pending} prior-year pending (use Resync)` : ""]
                  .filter(Boolean).join(", ") || "Synced"
              : "Google sync off"}
            data-state={!sync.data || !sync.data.enabled ? "off"
              : (sync.data.pending || sync.data.prior_pending) ? "pending" : "ok"} />
    </header>
  );
}
