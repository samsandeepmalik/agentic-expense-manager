import { Fragment, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { del, get, patch, post, type Category, type Txn } from "../api";
import { Lightbox } from "../components/Lightbox";
import { CategoryPicker } from "../components/CategoryPicker";
import { groupByDate, money } from "../format";
import { categoryOptions } from "../components/categoryOptions";

export default function Transactions({ period }: { period: string }) {
  const queryClient = useQueryClient();
  const [filters, setFilters] = useState({ type: "", category: "", q: "" });
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [editing, setEditing] = useState<number | null>(null);
  const [draft, setDraft] = useState<Partial<Txn>>({});
  const [lightbox, setLightbox] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [bulkCatId, setBulkCatId] = useState<number | null>(null);
  const [bulkCatType, setBulkCatType] = useState<"expense" | "income">("expense");
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [totalDraftStr, setTotalDraftStr] = useState("");

  const categories = useQuery({ queryKey: ["categories"],
    queryFn: () => get<Category[]>("/api/categories") });
  const query = new URLSearchParams({ period, ...filters, limit: "200" });
  const txns = useQuery({ queryKey: ["transactions", period, filters],
    refetchInterval: 15000,
    queryFn: () => get<Txn[]>(`/api/transactions?${query}`) });

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["transactions"] });
    queryClient.invalidateQueries({ queryKey: ["dashboard"] });
  };
  const saveEdit = useMutation({
    mutationFn: (id: number) => patch(`/api/transactions/${id}`, draft),
    onSuccess: () => { setEditing(null); refresh(); },
    onError: (e: Error) => setError(e.message) });
  const bulk = useMutation({
    mutationFn: (body: { ids: number[]; action: string; category?: string; category_id?: number }) =>
      post("/api/transactions/bulk", body),
    onSuccess: () => { setSelected(new Set()); setBulkCatId(null); refresh(); },
    onError: (e: Error) => setError(e.message) });
  const remove = useMutation({
    mutationFn: (id: number) => del(`/api/transactions/${id}`),
    onSuccess: refresh,
    onError: (e: Error) => setError(e.message) });
  const [reuploadMsg, setReuploadMsg] = useState("");
  const reupload = useMutation({
    mutationFn: (id: number) => post<Txn>(`/api/transactions/${id}/reupload-receipt`),
    onSuccess: () => { setReuploadMsg(""); refresh(); },
    onError: (e: Error) => setReuploadMsg(e.message) });

  const toggle = (id: number) => setSelected((s) => {
    const next = new Set(s); next.has(id) ? next.delete(id) : next.add(id); return next; });
  const toggleExpand = (id: number) => setExpanded((s) => {
    const next = new Set(s); next.has(id) ? next.delete(id) : next.add(id); return next; });
  const setF = (k: string, v: string) => setFilters((f) => ({ ...f, [k]: v }));
  const groups = groupByDate(txns.data ?? []);

  // Draft total validation
  const draftTotalNum = parseFloat(totalDraftStr);
  const draftTotalValid = !isNaN(draftTotalNum) && draftTotalNum > 0;
  const showDraftTotalHint = totalDraftStr !== "" && !draftTotalValid;

  const taxCell = (t: Txn) => {
    const entries = Object.entries(t.tax_breakdown).filter(([, v]) => v);
    if (entries.length === 0) return <td className="num muted">—</td>;
    return (
      <td className="num muted">
        {entries.map(([name, v]) => (
          <div key={name}>{name} {money(v)}</div>
        ))}
      </td>
    );
  };

  return (
    <div className="card reveal">
      <div className="row" style={{ marginBottom: 14 }}>
        <select value={filters.type} onChange={(e) => setF("type", e.target.value)}>
          <option value="">All types</option>
          <option value="expense">Expense</option><option value="income">Income</option>
        </select>
        <select value={filters.category} onChange={(e) => setF("category", e.target.value)}>
          <option value="">All categories</option>
          {categoryOptions(categories.data ?? [])}
        </select>
        <input className="grow" placeholder="SEARCH MERCHANT/NOTE…" value={filters.q}
               onChange={(e) => setF("q", e.target.value)} />
        <a href="/api/transactions/export.csv" download
           title="Exports all transactions for this profile, ignoring filters">
          <button className="ghost">⬇ CSV (all)</button></a>
      </div>

      {selected.size > 0 && (
        <div className="row invert" style={{ marginBottom: 10, padding: "8px 12px", flexWrap: "wrap", gap: 6 }}>
          <b className="lbl">{selected.size} selected</b>
          <select value={bulkCatType}
                  style={{ width: 100 }}
                  onChange={(e) => { setBulkCatType(e.target.value as "expense" | "income"); setBulkCatId(null); }}>
            <option value="expense">expense</option>
            <option value="income">income</option>
          </select>
          <CategoryPicker
            categories={categories.data ?? []}
            type={bulkCatType}
            valueId={bulkCatId}
            onChange={setBulkCatId}
          />
          <button className="ghost"
                  disabled={bulkCatId === null || bulk.isPending}
                  onClick={() => {
                    if (bulkCatId !== null)
                      bulk.mutate({ ids: [...selected], action: "recategorize", category_id: bulkCatId });
                  }}>
            Apply</button>
          <button className="ghost danger"
                  onClick={() => {
                    if (window.confirm(`Delete ${selected.size} transaction(s)? This cannot be undone.`))
                      bulk.mutate({ ids: [...selected], action: "delete" });
                  }}>
            Delete</button>
        </div>)}
      {error && <p className="neg">{error}</p>}

      <table>
        <thead><tr><th title="Select for bulk actions">Sel</th><th>Category</th><th>Merchant</th><th className="num">Total</th>
                   <th className="num">Taxes</th><th className="num">Counted</th>
                   <th>Receipt</th><th></th></tr></thead>
        <tbody>
          {groups.map((g) => (
            <Fragment key={g.date}>
              <tr className="dayrow"><td colSpan={8}>
                <div className="daybar"><span>{g.label}</span>
                  <span className="mono">
                    {g.out === 0 && g.inn === 0
                      ? "—"
                      : [g.out > 0 ? `OUT ${money(g.out)}` : "", g.inn > 0 ? `IN ${money(g.inn)}` : ""]
                          .filter(Boolean).join("  ·  ")}
                  </span>
                </div></td></tr>
              {g.rows.map((t) => editing === t.id ? (
                <tr key={t.id} className="dayrow"><td colSpan={8}>
                  <div className="editbar">
                    <label>
                      <span className="lbl muted">Date</span>
                      <input type="date" defaultValue={t.date}
                             onChange={(e) => setDraft((d) => ({ ...d, date: e.target.value }))} />
                    </label>
                    <label>
                      <span className="lbl muted">Type</span>
                      <select defaultValue={t.type}
                          onChange={(e) => setDraft((d) => ({ ...d, type: e.target.value, category_id: undefined }))}>
                        <option value="expense">Expense</option>
                        <option value="income">Income</option>
                      </select>
                    </label>
                    <label>
                      <span className="lbl muted">Category</span>
                      <CategoryPicker
                        categories={categories.data ?? []}
                        type={(draft.type ?? t.type) as "income" | "expense"}
                        valueId={draft.category_id !== undefined ? (draft.category_id ?? null) : t.category_id}
                        onChange={(id) => setDraft((d) => ({ ...d, category_id: id ?? undefined }))}
                      />
                    </label>
                    <label className="grow">
                      <span className="lbl muted">Merchant</span>
                      <input defaultValue={t.merchant}
                             onChange={(e) => setDraft((d) => ({ ...d, merchant: e.target.value }))} />
                    </label>
                    <label className="grow">
                      <span className="lbl muted">Description</span>
                      <input defaultValue={t.description}
                             onChange={(e) => setDraft((d) => ({ ...d, description: e.target.value }))} />
                    </label>
                    <label>
                      <span className="lbl muted">Total ($)</span>
                      <input defaultValue={t.total} style={{ width: 100 }} inputMode="decimal"
                             onChange={(e) => {
                               setTotalDraftStr(e.target.value);
                               const v = parseFloat(e.target.value);
                               if (!Number.isNaN(v) && v > 0) setDraft((d) => ({ ...d, total: v }));
                             }} />
                      {showDraftTotalHint && (
                        <span className="neg" style={{ fontSize: "0.82em", marginLeft: 4 }}>Invalid</span>
                      )}
                    </label>
                    <label className="editbar-loan">
                      <span className="lbl muted">Loan</span>
                      <input type="checkbox" defaultChecked={t.loan}
                             onChange={(e) => setDraft((d) => ({ ...d, loan: e.target.checked }))} />
                    </label>
                    <label className="grow">
                      <span className="lbl muted">Notes</span>
                      <input defaultValue={t.notes}
                             onChange={(e) => setDraft((d) => ({ ...d, notes: e.target.value }))} />
                    </label>
                    <span className="lbl muted editbar-note">taxes recalc on save</span>
                    <button className="primary"
                            disabled={saveEdit.isPending || (totalDraftStr !== "" && !draftTotalValid)}
                            onClick={() => saveEdit.mutate(t.id)}>Save</button>
                    <button className="ghost" onClick={() => { setEditing(null); setTotalDraftStr(""); }}>Cancel</button>
                  </div>
                </td></tr>
              ) : (
                <Fragment key={t.id}>
                  <tr
                    style={{ cursor: "pointer" }}
                    onClick={() => toggleExpand(t.id)}
                  >
                    <td onClick={(e) => e.stopPropagation()}>
                      <input type="checkbox" checked={selected.has(t.id)}
                             onChange={() => toggle(t.id)} />
                    </td>
                    <td>
                      <span style={{ marginRight: 5 }}>
                        {expanded.has(t.id) ? "▾" : "▸"}
                      </span>
                      <span
                        style={{
                          display: "inline-block",
                          fontSize: "0.7em",
                          fontWeight: 700,
                          letterSpacing: "0.04em",
                          padding: "1px 5px",
                          borderRadius: 3,
                          marginRight: 5,
                          color: t.type === "income" ? "var(--pos, #2d7a4f)" : "var(--muted, #888)",
                          background: t.type === "income" ? "rgba(45,122,79,0.10)" : "rgba(0,0,0,0.06)",
                        }}
                      >
                        {t.type === "income" ? "IN" : "OUT"}
                      </span>
                      {t.category_parent
                        ? <>{t.category_parent} <span className="muted">›</span> {t.category}</>
                        : t.category}
                      {t.loan && <> <span className="tag">loan</span></>}
                      {t.notes && <span className="tag" title={t.notes}
                                        style={{ marginLeft: 4 }}>📝</span>}
                    </td>
                    <td className="muted">{t.merchant || t.description}</td>
                    <td className={`num${t.type === "income" ? " pos" : ""}`}>
                      {t.type === "income" ? `+${money(t.total)}` : money(t.total)}</td>
                    {taxCell(t)}
                    <td className="num muted">{money(t.counted)}</td>
                    <td onClick={(e) => e.stopPropagation()}>
                      {t.image_path
                        ? <button className="ghost" aria-label="View receipt"
                            onClick={() => setLightbox(t.id)}>🧾</button>
                        : t.receipt_link
                          ? <a href={t.receipt_link} target="_blank" rel="noreferrer"
                               title="Receipt on Drive">📎</a>
                          : null}
                    </td>
                    <td onClick={(e) => e.stopPropagation()}>
                      <button className="ghost" aria-label="Edit transaction"
                            onClick={() => { setEditing(t.id); setDraft({}); setTotalDraftStr(""); }}>✎</button>
                      <button className="ghost danger" aria-label="Delete transaction"
                            onClick={() => {
                              if (window.confirm(`Delete this transaction (${t.merchant || t.category} ${money(t.total)})? This cannot be undone.`))
                                remove.mutate(t.id);
                            }}>🗑</button>
                    </td>
                  </tr>
                  {expanded.has(t.id) && (
                    <tr>
                      <td colSpan={8} style={{ background: "var(--bg-card, #f0ece5)", padding: "10px 18px 14px" }}>
                        <div style={{
                          display: "grid",
                          gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))",
                          gap: "8px 20px",
                          fontSize: "0.88em",
                        }}>
                          <div>
                            <span className="lbl muted">Type</span><br />
                            <span>{t.type === "income" ? "Income" : "Expense"}</span>
                          </div>
                          <div>
                            <span className="lbl muted">Category</span><br />
                            <span>
                              {t.category_parent
                                ? <>{t.category_parent} <span className="muted">›</span> {t.category}</>
                                : t.category}
                            </span>
                          </div>
                          <div>
                            <span className="lbl muted">Loan</span><br />
                            <span>{t.loan ? "Yes" : "No"}</span>
                          </div>
                          <div>
                            <span className="lbl muted">Calculation</span><br />
                            <span>
                              Total ${money(t.total)} × {t.category_percent}% = Counted ${money(t.counted)}
                            </span>
                          </div>
                          <div>
                            <span className="lbl muted">Base (pre-tax)</span><br />
                            <span>${money(t.amount)}</span>
                          </div>
                          <div>
                            <span className="lbl muted">Tax breakdown</span><br />
                            {Object.entries(t.tax_breakdown).filter(([, v]) => v).length === 0
                              ? <span className="muted">No tax</span>
                              : Object.entries(t.tax_breakdown)
                                  .filter(([, v]) => v)
                                  .map(([name, v]) => (
                                    <div key={name}>{name}: ${money(v)}</div>
                                  ))
                            }
                          </div>
                          {t.notes && (
                            <div>
                              <span className="lbl muted">Notes</span><br />
                              <span>{t.notes}</span>
                            </div>
                          )}
                          <div>
                            <span className="lbl muted">Source</span><br />
                            <span className="muted">{t.source || "—"}</span>
                          </div>
                          <div>
                            <span className="lbl muted">Sync</span><br />
                            <span className="muted">{t.sync_status || "—"}</span>
                          </div>
                        </div>
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </Fragment>))}
          {txns.data !== undefined && txns.data.length === 0 && (
            <tr><td colSpan={8} className="muted">No transactions match.</td></tr>)}
        </tbody>
      </table>
      {reuploadMsg && <p className="neg">{reuploadMsg}</p>}
      {lightbox !== null && (
        <Lightbox txnId={lightbox} onClose={() => { setLightbox(null); setReuploadMsg(""); }}
                  onReupload={() => reupload.mutate(lightbox)} />
      )}
    </div>
  );
}
