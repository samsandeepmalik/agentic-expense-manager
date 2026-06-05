import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { del, get, patch, post, type Category, type Txn } from "../api";
import { Lightbox } from "../components/Lightbox";

export default function Transactions({ period }: { period: string }) {
  const queryClient = useQueryClient();
  const [filters, setFilters] = useState({ type: "", category: "", q: "" });
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [editing, setEditing] = useState<number | null>(null);
  const [draft, setDraft] = useState<Partial<Txn>>({});
  const [lightbox, setLightbox] = useState<string | null>(null);
  const [error, setError] = useState("");

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
    mutationFn: (body: { ids: number[]; action: string; category?: string }) =>
      post("/api/transactions/bulk", body),
    onSuccess: () => { setSelected(new Set()); refresh(); } });
  const remove = useMutation({
    mutationFn: (id: number) => del(`/api/transactions/${id}`),
    onSuccess: refresh });

  const toggle = (id: number) => setSelected((s) => {
    const next = new Set(s); next.has(id) ? next.delete(id) : next.add(id); return next; });
  const setF = (k: string, v: string) => setFilters((f) => ({ ...f, [k]: v }));

  return (
    <div className="card">
      <div style={{ display: "flex", gap: 10, marginBottom: 14, alignItems: "center" }}>
        <select value={filters.type} onChange={(e) => setF("type", e.target.value)}>
          <option value="">All types</option>
          <option value="expense">Expense</option><option value="income">Income</option>
        </select>
        <select value={filters.category} onChange={(e) => setF("category", e.target.value)}>
          <option value="">All categories</option>
          {(categories.data ?? []).map((c) => <option key={c.id}>{c.name}</option>)}
        </select>
        <input placeholder="Search merchant/note…" value={filters.q}
               onChange={(e) => setF("q", e.target.value)} style={{ flex: 1 }} />
        <a href="/api/transactions/export.csv" download><button className="ghost">⬇ CSV</button></a>
      </div>

      {selected.size > 0 && (
        <div style={{ display: "flex", gap: 10, marginBottom: 10, alignItems: "center",
                      background: "var(--green-soft)", borderRadius: 10, padding: "8px 12px" }}>
          <b>{selected.size} selected</b>
          <select defaultValue="" onChange={(e) => e.target.value &&
              bulk.mutate({ ids: [...selected], action: "recategorize",
                            category: e.target.value })}>
            <option value="">Recategorize to…</option>
            {(categories.data ?? []).map((c) => <option key={c.id}>{c.name}</option>)}
          </select>
          <button className="ghost" style={{ color: "var(--amber)" }}
                  onClick={() => bulk.mutate({ ids: [...selected], action: "delete" })}>
            Delete</button>
        </div>)}
      {error && <p style={{ color: "var(--amber)" }}>{error}</p>}

      <table>
        <thead><tr><th></th><th>Date</th><th>Type</th><th>Category</th><th>Merchant</th>
                   <th>Total</th><th>Taxes</th><th>Counted</th><th>Receipt</th><th></th></tr></thead>
        <tbody>
          {(txns.data ?? []).map((t) => editing === t.id ? (
            <tr key={t.id}>
              <td></td>
              <td><input type="date" defaultValue={t.date}
                    onChange={(e) => setDraft((d) => ({ ...d, date: e.target.value }))} /></td>
              <td>{t.type}</td>
              <td><select defaultValue={t.category}
                    onChange={(e) => setDraft((d) => ({ ...d, category: e.target.value }))}>
                  {(categories.data ?? []).filter((c) => c.type === t.type)
                    .map((c) => <option key={c.id}>{c.name}</option>)}</select></td>
              <td><input defaultValue={t.merchant} style={{ width: 110 }}
                    onChange={(e) => setDraft((d) => ({ ...d, merchant: e.target.value }))} /></td>
              <td><input defaultValue={t.total} style={{ width: 80 }} inputMode="decimal"
                    onChange={(e) => setDraft((d) =>
                      ({ ...d, total: parseFloat(e.target.value) }))} /></td>
              <td className="muted">auto</td><td className="muted">auto</td><td></td>
              <td><button className="ghost" onClick={() => saveEdit.mutate(t.id)}>Save</button>
                  <button className="ghost" onClick={() => setEditing(null)}>✕</button></td>
            </tr>
          ) : (
            <tr key={t.id}>
              <td><input type="checkbox" checked={selected.has(t.id)}
                         onChange={() => toggle(t.id)} /></td>
              <td>{t.date}</td>
              <td><span className={`tag ${t.type}`}>{t.type}</span></td>
              <td>{t.category}</td><td>{t.merchant || t.description}</td>
              <td>${t.total.toFixed(2)}</td>
              <td className="muted">{Object.entries(t.tax_breakdown)
                .map(([k, v]) => `${k} $${v.toFixed(2)}`).join(", ") || "—"}</td>
              <td>${t.counted.toFixed(2)}</td>
              <td>{t.image_path
                ? <button className="ghost"
                    onClick={() => setLightbox(`/api/receipts/${t.id}`)}>🧾</button>
                : "—"}</td>
              <td><button className="ghost"
                    onClick={() => { setEditing(t.id); setDraft({}); }}>✎</button>
                  <button className="ghost" style={{ color: "var(--amber)" }}
                    onClick={() => remove.mutate(t.id)}>🗑</button></td>
            </tr>))}
          {(txns.data ?? []).length === 0 && (
            <tr><td colSpan={10} className="muted">No transactions match.</td></tr>)}
        </tbody>
      </table>
      {lightbox && <Lightbox src={lightbox} onClose={() => setLightbox(null)} />}
    </div>
  );
}
