// Display helpers shared by Dashboard / Transactions / RecentTable.
import type { Txn } from "./api";

export function money(n: number): string {
  return n.toLocaleString("en", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export function signedMoney(n: number): string {
  if (n === 0) return money(0);
  return `${n < 0 ? "−" : "+"}${money(Math.abs(n))}`;
}

export function dayLabel(date: string): string {
  const d = new Date(`${date}T00:00:00`);
  return d.toLocaleDateString("en", { month: "short", day: "numeric" }).toUpperCase()
    + " — " + d.toLocaleDateString("en", { weekday: "long" }).toUpperCase();
}

// Preserves incoming order (API returns date-desc). Day totals: out = sum of
// expense totals, inn = sum of income totals for the day.
export function groupByDate(rows: Txn[]) {
  const map = new Map<string, Txn[]>();
  for (const t of rows) {
    if (!map.has(t.date)) map.set(t.date, []);
    map.get(t.date)!.push(t);
  }
  return [...map.entries()].map(([date, list]) => ({
    date,
    label: dayLabel(date),
    out: list.filter((t) => t.type === "expense").reduce((s, t) => s + t.total, 0),
    inn: list.filter((t) => t.type === "income").reduce((s, t) => s + t.total, 0),
    rows: list,
  }));
}
