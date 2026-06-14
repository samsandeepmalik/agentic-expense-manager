import type { Budget } from "../api";
import { money } from "../format";

export function BudgetRail({ budgets }: { budgets: Budget[] }) {
  return (
    <div className="card">
      <div className="lbl">Budgets</div>
      {budgets.length === 0 && (
        <p className="muted">Set monthly budgets per category in Settings.</p>)}
      {budgets.map((b) => (
        <div key={b.name} className="stack" style={{ gap: 4, marginTop: 12 }}>
          <div className="row" style={{ justifyContent: "space-between" }}>
            <span className="lbl">{b.name}</span>
            <span className={`mono${b.pct >= 90 ? " neg" : " muted"}`}>
              <span title="Counted spend — the attributable share of transactions after applying each category's % (not necessarily dollars paid)">{money(b.spent)}</span>
              {" / "}{money(b.budget)}</span>
          </div>
          <div className={`bar${b.pct >= 90 ? " warn" : ""}`}>
            <div style={{ width: `${Math.min(b.pct, 100)}%` }} />
          </div>
        </div>))}
    </div>
  );
}
