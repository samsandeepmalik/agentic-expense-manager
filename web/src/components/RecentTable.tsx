import { Fragment, useState } from "react";
import { Link } from "react-router-dom";
import type { Txn } from "../api";
import { groupByDate, money } from "../format";
import { Lightbox } from "./Lightbox";

export function RecentTable({ rows, title = "Recent transactions" }:
    { rows: Txn[]; title?: string }) {
  const [lightbox, setLightbox] = useState<number | null>(null);
  const groups = groupByDate(rows);
  return (
    <div className="card">
      <div className="row" style={{ justifyContent: "space-between", marginBottom: 8 }}>
        <span className="lbl">{title}</span>
        <Link to="/transactions" className="lbl">View all →</Link>
      </div>
      <table>
        <thead><tr><th>Category</th><th>Merchant</th><th className="num">Total</th>
                   <th className="num">Counted</th><th>Receipt</th></tr></thead>
        <tbody>
          {groups.map((g) => (
            <Fragment key={g.date}>
              <tr className="dayrow"><td colSpan={5}>
                <div className="daybar"><span>{g.label}</span>
                  <span className="mono">
                    {g.out === 0 && g.inn === 0
                      ? "—"
                      : [g.out > 0 ? `OUT ${money(g.out)}` : "", g.inn > 0 ? `IN ${money(g.inn)}` : ""]
                          .filter(Boolean).join("  ·  ")}
                  </span>
                </div></td></tr>
              {g.rows.map((t) => (
                <tr key={t.id}>
                  <td>
                    {t.category_parent
                      ? <>{t.category_parent} <span className="muted">›</span> {t.category}</>
                      : t.category}
                    {t.loan && <> <span className="tag">loan</span></>}
                    {t.notes && <span className="tag" title={t.notes} style={{ marginLeft: 4 }}>📝</span>}
                  </td>
                  <td className="muted">{t.merchant || t.description}</td>
                  <td className={`num${t.type === "income" ? " pos" : ""}`}>
                    {t.type === "income" ? `+${money(t.total)}` : money(t.total)}</td>
                  <td className="num muted">{money(t.counted)}</td>
                  <td>
                    {t.image_path
                      ? <button className="ghost" onClick={() => setLightbox(t.id)}>🧾</button>
                      : t.receipt_link
                        ? <a href={t.receipt_link} target="_blank" rel="noreferrer"
                             title="Receipt on Drive">📎</a>
                        : <span className="muted">—</span>}
                  </td>
                </tr>))}
            </Fragment>))}
          {groups.length === 0 && (
            <tr><td colSpan={5} className="muted">
              Nothing yet — add your first expense with the + button or via chat.
            </td></tr>)}
        </tbody>
      </table>
      {lightbox !== null && <Lightbox txnId={lightbox} onClose={() => setLightbox(null)} />}
    </div>
  );
}
