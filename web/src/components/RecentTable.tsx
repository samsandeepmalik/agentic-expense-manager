import { useState } from "react";
import { Link } from "react-router-dom";
import type { Txn } from "../api";
import { Lightbox } from "./Lightbox";

export function RecentTable({ rows, title = "Recent transactions" }:
    { rows: Txn[]; title?: string }) {
  const [lightbox, setLightbox] = useState<string | null>(null);
  return (
    <div className="card">
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
        <b>{title}</b><Link to="/transactions" className="muted">View all →</Link>
      </div>
      <table>
        <thead><tr><th>Date</th><th>Type</th><th>Category</th><th>Merchant</th>
                   <th>Total</th><th>Counted</th><th>Receipt</th></tr></thead>
        <tbody>
          {rows.map((t) => (
            <tr key={t.id}>
              <td>{t.date}</td>
              <td><span className={`tag ${t.type}`}>{t.type}</span></td>
              <td>{t.category}</td><td>{t.merchant || t.description}</td>
              <td>${t.total.toFixed(2)}</td><td>${t.counted.toFixed(2)}</td>
              <td>{t.image_path
                ? <button className="ghost" onClick={() => setLightbox(`/api/receipts/${t.id}`)}>🧾</button>
                : <span className="muted">—</span>}</td>
            </tr>))}
          {rows.length === 0 && (
            <tr><td colSpan={7} className="muted">
              Nothing yet — add your first expense with the + button or via chat.
            </td></tr>)}
        </tbody>
      </table>
      {lightbox && <Lightbox src={lightbox} onClose={() => setLightbox(null)} />}
    </div>
  );
}
