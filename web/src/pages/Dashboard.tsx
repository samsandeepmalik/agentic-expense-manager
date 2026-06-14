import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { get, type Dashboard as DashboardData } from "../api";
import { BudgetRail } from "../components/BudgetRail";
import { CategoryPie, TrendChart } from "../components/Charts";
import { QuickAdd } from "../components/QuickAdd";
import { RecentTable } from "../components/RecentTable";
import { money, signedMoney } from "../format";

// NET tick-up: 0 -> target in ~400ms. Respects prefers-reduced-motion.
function useCountUp(target: number, ms = 400) {
  const [value, setValue] = useState(0);
  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setValue(target); return;
    }
    setValue(0);
    let raf = 0;
    const start = performance.now();
    const tick = (now: number) => {
      const p = Math.min((now - start) / ms, 1);
      setValue(target * p);
      if (p < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target, ms]);
  return value;
}

export default function Dashboard({ period }: { period: string }) {
  const [adding, setAdding] = useState(false);
  const { data, isLoading } = useQuery({
    queryKey: ["dashboard", period],
    refetchInterval: 15000,
    queryFn: () => get<DashboardData>(`/api/dashboard?period=${period}`),
  });
  // Rules of Hooks: must be called before early return. 0 while loading, animates when data arrives.
  const net = useCountUp(data?.metrics.net ?? 0);
  if (isLoading || !data) return <div className="card lbl">Loading…</div>;
  const { metrics } = data;
  return (
    <div className="stack reveal">
      <div className="hero">
        <div>
          <div className="lbl">Income</div>
          <div className="mono hero-side pos">{money(metrics.income)}</div>
        </div>
        <div className="hero-net">
          <div className="lbl">Net</div>
          <div className="mono hero-big">{signedMoney(net)}</div>
          <button className="primary" onClick={() => setAdding(true)}>+ Quick add</button>
        </div>
        <div>
          <div className="lbl">Expenses</div>
          <div className="mono hero-side neg">{money(metrics.expenses)}</div>
        </div>
      </div>
      <div className="muted" style={{ textAlign: "center", fontSize: "0.75rem", marginTop: "-8px" }}
           title="Counted = the attributable share of each transaction after applying the category's % (e.g. 50% business-use). Not necessarily dollars paid.">
        Figures show <em>counted</em> amounts — category&nbsp;% applied
      </div>
      <div className="card">
        <div className="lbl">Flow — last 6 months</div>
        <TrendChart data={data.trend} />
      </div>
      <div className="split2">
        <div className="card">
          <div className="lbl">By category</div>
          <CategoryPie data={data.by_category} />
        </div>
        <BudgetRail budgets={data.budgets} />
      </div>
      <RecentTable rows={data.recent} />
      {adding && <QuickAdd onClose={() => setAdding(false)} />}
    </div>
  );
}
