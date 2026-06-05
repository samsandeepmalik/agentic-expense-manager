import type { Budget } from "../api";

export function BudgetRail({ budgets }: { budgets: Budget[] }) {
  return (
    <div className="card">
      <b>Budgets</b>
      {budgets.length === 0 && (
        <p className="muted">Set monthly budgets per category in Settings.</p>)}
      {budgets.map((b) => (
        <div key={b.name} style={{ marginTop: 12 }}>
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: 13 }}>
            <span>{b.name}{b.pct >= 90 ? " ⚠️" : ""}</span>
            <span className="muted">${b.spent.toFixed(0)} / ${b.budget.toFixed(0)}</span>
          </div>
          <div className={`bar${b.pct >= 90 ? " warn" : ""}`}>
            <div style={{ width: `${Math.min(b.pct, 100)}%` }} />
          </div>
        </div>))}
    </div>
  );
}
