import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { get, post, ApiError, type Category, type DuplicateMatch } from "../api";
import { CategoryPicker } from "./CategoryPicker";

export function QuickAdd({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient();
  const categories = useQuery({ queryKey: ["categories"],
    queryFn: () => get<Category[]>("/api/categories") });
  const [form, setForm] = useState({ date: new Date().toISOString().slice(0, 10),
    type: "expense" as "income" | "expense", categoryId: null as number | null,
    total: "", merchant: "", description: "", loan: false, notes: "" });
  const [error, setError] = useState("");
  const [dup, setDup] = useState<DuplicateMatch | null>(null);

  const selected = categories.data?.find((c) => c.id === form.categoryId);
  const totalNum = parseFloat(form.total);
  const totalValid = !isNaN(totalNum) && totalNum > 0;

  // Tax breakdown comes from the server (same _compute path used on save) so the
  // preview can never diverge from what's actually recorded. No money math here.
  const preview = useQuery({
    queryKey: ["txn-preview", form.type, form.categoryId, totalNum],
    enabled: totalValid && !!form.categoryId && !!selected?.taxable,
    queryFn: () => post<{ breakdown: Record<string, number> }>(
      "/api/transactions/preview",
      { type: form.type, category_id: form.categoryId, total: totalNum }),
  });
  const taxPreview = Object.entries(preview.data?.breakdown ?? {})
    .map(([name, value]) => ({ name, value }));

  const save = useMutation({
    mutationFn: (confirm: boolean) => post("/api/transactions",
      { date: form.date, type: form.type, category_id: form.categoryId,
        total: totalNum, merchant: form.merchant, description: form.description,
        loan: form.loan, notes: form.notes, confirm_duplicate: confirm }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      queryClient.invalidateQueries({ queryKey: ["transactions"] });
      onClose();
    },
    onError: (e: Error) => {
      if (e instanceof ApiError && e.code === "duplicate_suspected") {
        setDup(e.details as DuplicateMatch);
        setError("");
      } else {
        setDup(null);
        setError(e.message);
      }
    },
  });

  const set = <K extends keyof typeof form>(k: K, v: typeof form[K]) =>
    setForm((f) => ({ ...f, [k]: v }));

  const totalRaw = form.total;
  const showTotalHint = totalRaw !== "" && !totalValid;

  return (
    <div className="overlay" onClick={onClose}>
      <div className="card" style={{ width: 420 }} onClick={(e) => e.stopPropagation()}>
        <h3 className="section-title">Add transaction</h3>
        {error && <p className="neg">{error}</p>}
        <div style={{ display: "grid", gap: 10 }}>
          <input type="date" value={form.date} onChange={(e) => set("date", e.target.value)} />
          <select value={form.type} onChange={(e) => {
            set("type", e.target.value as "income" | "expense");
            set("categoryId", null);
          }}>
            <option value="expense">Expense</option><option value="income">Income</option>
          </select>
          <CategoryPicker
            categories={categories.data ?? []}
            type={form.type}
            valueId={form.categoryId}
            onChange={(id) => set("categoryId", id)}
          />
          <input placeholder="Total paid ($)" inputMode="decimal" value={form.total}
                 onChange={(e) => set("total", e.target.value)} />
          {showTotalHint && <p className="neg" style={{ margin: 0, fontSize: "0.85em" }}>Enter a valid amount.</p>}
          {taxPreview.length > 0 && (
            <p className="muted">Includes {taxPreview.map((t) =>
              `${t.name} $${t.value.toFixed(2)}`).join(" + ")}</p>)}
          {selected && !selected.taxable && <p className="muted">No tax for {selected.name}.</p>}
          <input placeholder="Merchant" value={form.merchant}
                 onChange={(e) => set("merchant", e.target.value)} />
          <input placeholder="Description (optional)" value={form.description}
                 onChange={(e) => set("description", e.target.value)} />
          <textarea placeholder="Notes (optional)" rows={2} value={form.notes}
                    style={{ resize: "vertical" }}
                    onChange={(e) => set("notes", e.target.value)} />
          <label className="row">
            <input type="checkbox" checked={form.loan}
                   onChange={(e) => setForm((f) => ({ ...f, loan: e.target.checked }))} />
            Loan (money lent / borrowed)
          </label>
          {dup && (
            <div className="card" style={{ background: "#fff7ed", padding: 10 }}>
              <p style={{ margin: "0 0 6px" }}>
                Possible duplicate of {dup.txn.merchant} {dup.txn.date} (${dup.txn.total.toFixed(2)}).
                {dup.reason === "receipt" ? " Same receipt link." : ""}
              </p>
              <div className="row" style={{ gap: 8 }}>
                <button className="primary" disabled={save.isPending}
                        onClick={() => save.mutate(true)}>Add anyway</button>
                <button onClick={() => setDup(null)}>Cancel</button>
              </div>
            </div>
          )}
          <button className="primary"
                  disabled={!form.categoryId || !totalValid || save.isPending}
                  onClick={() => { setDup(null); save.mutate(false); }}>
            {save.isPending ? "Saving…" : "Save"}</button>
        </div>
      </div>
    </div>
  );
}
