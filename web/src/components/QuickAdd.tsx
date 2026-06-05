import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { get, post, type Category, type TaxProfile } from "../api";

export function QuickAdd({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient();
  const categories = useQuery({ queryKey: ["categories"],
    queryFn: () => get<Category[]>("/api/categories") });
  const profiles = useQuery({ queryKey: ["tax-profiles"],
    queryFn: () => get<TaxProfile[]>("/api/tax-profiles") });
  const [form, setForm] = useState({ date: new Date().toISOString().slice(0, 10),
    type: "expense", category: "", total: "", merchant: "", description: "" });
  const [error, setError] = useState("");

  const selected = categories.data?.find((c) => c.name === form.category);
  const active = profiles.data?.find((p) => p.is_active);
  const total = parseFloat(form.total) || 0;
  const rateSum = (active?.components ?? []).reduce((s, c) => s + c.rate, 0);
  const taxPreview = selected?.taxable && total > 0 && active
    ? active.components.map((c) => ({ name: c.name,
        value: (total / (1 + rateSum / 100)) * (c.rate / 100) }))
    : [];

  const save = useMutation({
    mutationFn: () => post("/api/transactions",
      { ...form, total: parseFloat(form.total) }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      queryClient.invalidateQueries({ queryKey: ["transactions"] });
      onClose();
    },
    onError: (e: Error) => setError(e.message),
  });

  const set = (k: string, v: string) => setForm((f) => ({ ...f, [k]: v }));
  return (
    <div style={{ position: "fixed", inset: 0, background: "rgba(45,42,36,.35)",
                  display: "flex", alignItems: "center", justifyContent: "center", zIndex: 50 }}
         onClick={onClose}>
      <div className="card" style={{ width: 420 }} onClick={(e) => e.stopPropagation()}>
        <h3 style={{ marginTop: 0 }}>Add transaction</h3>
        {error && <p style={{ color: "var(--amber)" }}>{error}</p>}
        <div style={{ display: "grid", gap: 10 }}>
          <input type="date" value={form.date} onChange={(e) => set("date", e.target.value)} />
          <select value={form.type} onChange={(e) => set("type", e.target.value)}>
            <option value="expense">Expense</option><option value="income">Income</option>
          </select>
          <select value={form.category} onChange={(e) => set("category", e.target.value)}>
            <option value="">Category…</option>
            {(categories.data ?? []).filter((c) => c.type === form.type)
              .map((c) => <option key={c.id}>{c.name}</option>)}
          </select>
          <input placeholder="Total paid ($)" inputMode="decimal" value={form.total}
                 onChange={(e) => set("total", e.target.value)} />
          {taxPreview.length > 0 && (
            <p className="muted">Includes {taxPreview.map((t) =>
              `${t.name} $${t.value.toFixed(2)}`).join(" + ")}</p>)}
          {selected && !selected.taxable && <p className="muted">No tax for {selected.name}.</p>}
          <input placeholder="Merchant" value={form.merchant}
                 onChange={(e) => set("merchant", e.target.value)} />
          <input placeholder="Note (optional)" value={form.description}
                 onChange={(e) => set("description", e.target.value)} />
          <button className="primary" disabled={!form.category || !total || save.isPending}
                  onClick={() => save.mutate()}>
            {save.isPending ? "Saving…" : "Save"}</button>
        </div>
      </div>
    </div>
  );
}
