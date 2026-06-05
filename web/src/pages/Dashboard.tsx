import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { get, type Dashboard as DashboardData } from "../api";
import { BudgetRail } from "../components/BudgetRail";
import { CategoryPie, TrendChart } from "../components/Charts";
import { QuickAdd } from "../components/QuickAdd";
import { RecentTable } from "../components/RecentTable";

function Metric({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="card" style={{ flex: 1, textAlign: "center" }}>
      <div className="muted" style={{ textTransform: "uppercase", fontSize: 11,
                                      letterSpacing: ".06em" }}>{label}</div>
      <div style={{ fontSize: 26, fontWeight: 700, color: tone }}>{value}</div>
    </div>
  );
}

export default function Dashboard({ period }: { period: string }) {
  const [adding, setAdding] = useState(false);
  const { data, isLoading } = useQuery({
    queryKey: ["dashboard", period],
    refetchInterval: 15000,
    queryFn: () => get<DashboardData>(`/api/dashboard?period=${period}`),
  });
  if (isLoading || !data) return <div className="card">Loading…</div>;
  const { metrics } = data;
  return (
    <div style={{ display: "flex", gap: 16 }}>
      <div style={{ flex: 3, display: "flex", flexDirection: "column", gap: 16 }}>
        <div style={{ display: "flex", gap: 16 }}>
          <Metric label="Income" value={`$${metrics.income.toFixed(2)}`} tone="var(--green)" />
          <Metric label="Expenses" value={`$${metrics.expenses.toFixed(2)}`} tone="var(--amber)" />
          <Metric label="Net" value={`$${metrics.net.toFixed(2)}`} />
          <div className="card" style={{ flex: 1, display: "flex", alignItems: "center",
                                         justifyContent: "center" }}>
            <button className="primary" onClick={() => setAdding(true)}>＋ Quick add</button>
          </div>
        </div>
        <div style={{ display: "flex", gap: 16 }}>
          <div className="card" style={{ flex: 3 }}>
            <b>Income vs expenses</b><TrendChart data={data.trend} /></div>
          <div className="card" style={{ flex: 2 }}>
            <b>Expenses by category</b><CategoryPie data={data.by_category} /></div>
        </div>
        <RecentTable rows={data.recent} />
      </div>
      <div style={{ flex: 1 }}><BudgetRail budgets={data.budgets} /></div>
      {adding && <QuickAdd onClose={() => setAdding(false)} />}
    </div>
  );
}
