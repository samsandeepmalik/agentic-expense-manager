import { useEffect, useState } from "react";
import { getJson, type DashboardData } from "../api";
import { GenUI } from "./GenUI";

export function Dashboard({ refreshKey }: { refreshKey: number }) {
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    getJson<DashboardData>("/api/dashboard?months=6")
      .then(setData)
      .catch((err) => setError(String(err)));
  }, [refreshKey]);

  if (error) return <div className="panel error">Dashboard unavailable: {error}</div>;
  if (!data) return <div className="panel">Loading…</div>;

  const { summary, recent, sheet_url } = data;
  const byCategory = Object.entries(summary.by_category).map(([name, value]) => ({
    name,
    value,
  }));

  return (
    <div className="dashboard">
      <GenUI
        spec={{
          components: [
            { type: "metric", label: "Income (6 mo)", value: summary.income, unit: "$" },
            { type: "metric", label: "Expenses (6 mo)", value: summary.expenses, unit: "$" },
            { type: "metric", label: "Net", value: summary.net, unit: "$" },
            { type: "metric", label: "Transactions", value: summary.count },
            {
              type: "lineChart",
              title: "Income vs expenses — last 6 months",
              data: summary.trend,
              xKey: "month",
              series: ["income", "expenses"],
            },
            ...(byCategory.length > 0
              ? [
                  {
                    type: "pieChart" as const,
                    title: "Expenses by category",
                    data: byCategory,
                    xKey: "name",
                    series: ["value"],
                  },
                ]
              : []),
          ],
        }}
      />

      <div className="genui-block">
        <div className="block-title">
          Recent transactions
          {sheet_url && (
            <a className="sheet-link" href={sheet_url} target="_blank" rel="noreferrer">
              Open Google Sheet ↗
            </a>
          )}
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Date</th><th>Type</th><th>Category</th><th>Merchant</th>
                <th>Total</th><th>Counted</th><th>Receipt</th><th>Source</th>
              </tr>
            </thead>
            <tbody>
              {recent.map((transaction, index) => (
                <tr key={index}>
                  <td>{transaction.date}</td>
                  <td>
                    <span className={`tag ${transaction.type}`}>{transaction.type}</span>
                  </td>
                  <td>{transaction.category}</td>
                  <td>{transaction.merchant || transaction.description}</td>
                  <td>${transaction.total.toFixed(2)}</td>
                  <td>${transaction.counted.toFixed(2)}</td>
                  <td>
                    {transaction.image_link ? (
                      <a href={transaction.image_link} target="_blank" rel="noreferrer">view</a>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td>{transaction.source}</td>
                </tr>
              ))}
              {recent.length === 0 && (
                <tr><td colSpan={8}>No transactions yet — chat with the agent to add some.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
