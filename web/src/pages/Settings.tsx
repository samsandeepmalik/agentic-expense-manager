import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { del, get, post, type Category, type RecurringRule, type TaxProfile,
         type WaAccount } from "../api";
import { ImportReview } from "../components/ImportReview";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return <div className="card" style={{ marginBottom: 16 }}>
    <h3 style={{ marginTop: 0 }}>{title}</h3>{children}</div>;
}

export default function Settings() {
  const queryClient = useQueryClient();
  const invalidate = (key: string) => queryClient.invalidateQueries({ queryKey: [key] });

  // --- Categories ---
  const categories = useQuery({ queryKey: ["categories"],
    queryFn: () => get<Category[]>("/api/categories") });
  const saveCategory = useMutation({
    mutationFn: (c: Partial<Category>) => post("/api/categories", c),
    onSuccess: () => invalidate("categories") });
  const removeCategory = useMutation({
    mutationFn: (id: number) => del(`/api/categories/${id}`),
    onSuccess: () => invalidate("categories") });
  const [newCategory, setNewCategory] = useState({ name: "", type: "expense",
    percent: 100, taxable: true, budget_monthly: "" });

  // --- Tax profiles ---
  const profiles = useQuery({ queryKey: ["tax-profiles"],
    queryFn: () => get<TaxProfile[]>("/api/tax-profiles") });
  const activate = useMutation({
    mutationFn: (p: TaxProfile) => post("/api/tax-profiles",
      { name: p.name, components: p.components, activate: true }),
    onSuccess: () => invalidate("tax-profiles") });

  // --- Recurring ---
  const rules = useQuery({ queryKey: ["recurring"],
    queryFn: () => get<RecurringRule[]>("/api/recurring") });
  const removeRule = useMutation({ mutationFn: (id: number) => del(`/api/recurring/${id}`),
    onSuccess: () => invalidate("recurring") });

  // --- Connections ---
  const google = useQuery({ queryKey: ["google"],
    queryFn: () => get<{ configured: boolean; connected: boolean;
      sheet_url: string | null; pending: number }>("/api/google/status") });
  const whatsapp = useQuery({ queryKey: ["whatsapp"], refetchInterval: 4000,
    queryFn: () => get<WaAccount[]>("/api/whatsapp/accounts") });
  const addWa = useMutation({ mutationFn: () => post("/api/whatsapp/accounts"),
    onSuccess: () => invalidate("whatsapp") });
  const refreshWa = useMutation({
    mutationFn: (id: string) => post(`/api/whatsapp/accounts/${id}/refresh`),
    onSuccess: () => invalidate("whatsapp") });
  const removeWa = useMutation({
    mutationFn: (id: string) => del(`/api/whatsapp/accounts/${id}`),
    onSuccess: () => invalidate("whatsapp") });
  const allowed = useQuery({ queryKey: ["wa-allowed"],
    queryFn: () => get<{ allowed: string[] }>("/api/whatsapp/allowed") });
  const [newSender, setNewSender] = useState("");
  const addAllowed = useMutation({
    mutationFn: (number: string) => post("/api/whatsapp/allowed", { number }),
    onSuccess: () => { setNewSender(""); invalidate("wa-allowed"); } });
  const removeAllowed = useMutation({
    mutationFn: (number: string) => del(`/api/whatsapp/allowed/${number}`),
    onSuccess: () => invalidate("wa-allowed") });
  const syncNow = useMutation({ mutationFn: () => post("/api/sync/now"),
    onSuccess: () => invalidate("google") });

  return (
    <div>
      <Section title="Categories & budgets">
        <table>
          <thead><tr><th>Name</th><th>Type</th><th>% counted</th><th>Taxable</th>
                     <th>Budget/mo</th><th></th></tr></thead>
          <tbody>
            {(categories.data ?? []).map((c) => (
              <tr key={c.id}>
                <td>{c.name}</td>
                <td><span className={`tag ${c.type}`}>{c.type}</span></td>
                <td><input type="number" defaultValue={c.percent} min={0} max={100}
                      style={{ width: 70 }}
                      onBlur={(e) => saveCategory.mutate(
                        { ...c, percent: Number(e.target.value) })} /></td>
                <td><input type="checkbox" defaultChecked={c.taxable}
                      onChange={(e) => saveCategory.mutate(
                        { ...c, taxable: e.target.checked })} /></td>
                <td><input type="number" defaultValue={c.budget_monthly ?? ""}
                      placeholder="—" style={{ width: 90 }}
                      onBlur={(e) => saveCategory.mutate({ ...c,
                        budget_monthly: e.target.value ? Number(e.target.value) : null })} /></td>
                <td><button className="ghost" style={{ color: "var(--amber)" }}
                      onClick={() => removeCategory.mutate(c.id)}>✕</button></td>
              </tr>))}
            <tr>
              <td><input placeholder="New category" value={newCategory.name}
                    onChange={(e) => setNewCategory({ ...newCategory, name: e.target.value })} /></td>
              <td><select value={newCategory.type}
                    onChange={(e) => setNewCategory({ ...newCategory, type: e.target.value })}>
                  <option value="expense">expense</option>
                  <option value="income">income</option></select></td>
              <td colSpan={3}></td>
              <td><button className="ghost" disabled={!newCategory.name}
                    onClick={() => { saveCategory.mutate({ ...newCategory,
                      budget_monthly: newCategory.budget_monthly
                        ? Number(newCategory.budget_monthly) : null } as Partial<Category>);
                      setNewCategory({ ...newCategory, name: "" }); }}>＋ Add</button></td>
            </tr>
          </tbody>
        </table>
      </Section>

      <Section title="Tax profile">
        <p className="muted">Active profile drives tax back-calculation for taxable categories.</p>
        {(profiles.data ?? []).map((p) => (
          <label key={p.id} style={{ display: "block", marginTop: 8 }}>
            <input type="radio" name="tax" checked={p.is_active}
                   onChange={() => activate.mutate(p)} />{" "}
            <b>{p.name}</b>{" "}
            <span className="muted">
              {p.components.map((c) => `${c.name} ${c.rate}%`).join(" + ")}</span>
          </label>))}
      </Section>

      <Section title="Recurring rules">
        {(rules.data ?? []).length === 0 &&
          <p className="muted">None yet — ask the agent: “add recurring rent $1500 on the 1st”.</p>}
        {(rules.data ?? []).map((r) => (
          <p key={r.id}>{String(r.template.category)} ${String(r.template.total)} ·
            {r.frequency} · next {r.next_run}
            <button className="ghost" style={{ color: "var(--amber)" }}
                    onClick={() => removeRule.mutate(r.id)}>✕</button></p>))}
      </Section>

      <Section title="Google sync">
        {!google.data ? <p>Loading…</p>
          : !google.data.configured ? <p className="muted">
              Set GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET in .env to enable.</p>
          : google.data.connected ? (
            <p>✅ Connected — {google.data.pending} pending{" "}
              <button className="ghost" onClick={() => syncNow.mutate()}>Sync now</button>
              {google.data.sheet_url &&
                <a href={google.data.sheet_url} target="_blank" rel="noreferrer"> Open sheet ↗</a>}
            </p>)
          : <a href="/api/google/auth"><button className="primary">Connect Google</button></a>}
      </Section>

      <Section title="WhatsApp">
        {(whatsapp.data ?? []).map((a) => (
          <div key={a.id} style={{ borderBottom: "1px solid #f4efe7",
                                   padding: "10px 0" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <span style={{ width: 9, height: 9, borderRadius: 5,
                background: a.status === "connected" ? "var(--green)"
                  : a.status === "qr" ? "var(--amber)" : "#cfc6b8" }} />
              <b>{a.device || a.id}</b>
              <span className="muted">{a.status}</span>
              <span style={{ flex: 1 }} />
              {(a.status === "qr_expired" || a.status === "disconnected") && (
                <button className="ghost" disabled={refreshWa.isPending}
                        onClick={() => refreshWa.mutate(a.id)}>
                  {refreshWa.isPending ? "Refreshing…" : "↻ Refresh QR"}</button>)}
              <button className="ghost" style={{ color: "var(--amber)" }}
                      onClick={() => removeWa.mutate(a.id)}>
                {a.status === "connected" ? "Unpair" : "Remove"}</button>
            </div>
            {a.status === "connected" && (
              <p className="muted" style={{ margin: "6px 0 0 19px" }}>
                Self-chat is on — open WhatsApp, search your own name and use
                “Message yourself” to talk to the agent.</p>)}
            {a.qr && (
              <div style={{ margin: "8px 0 0 19px" }}>
                <p style={{ margin: "0 0 6px" }}>
                  Scan within 20s: WhatsApp → Settings → Linked devices → Link
                  a device <span className="muted">(use that screen's scanner,
                  not the phone camera)</span></p>
                <img src={a.qr} style={{ width: 220, background: "#fff",
                     padding: 8, borderRadius: 10 }} />
              </div>)}
            {a.status === "qr_expired" && (
              <p className="muted" style={{ margin: "6px 0 0 19px" }}>
                QR expired — refresh to get a new one.</p>)}
          </div>))}
        <button className="primary" style={{ marginTop: 12 }}
                disabled={addWa.isPending} onClick={() => addWa.mutate()}>
          ＋ Pair another account</button>

        <div style={{ marginTop: 18, borderTop: "1px solid #f4efe7", paddingTop: 12 }}>
          <b>Allowed senders</b>
          <p className="muted" style={{ margin: "4px 0 8px" }}>
            Other numbers that may talk to the agent by messaging your WhatsApp.
            Everyone else is ignored.</p>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
            {(allowed.data?.allowed ?? []).map((n) => (
              <span key={n} className="tag income">+{n}{" "}
                <button className="ghost" style={{ color: "var(--amber)", padding: 0 }}
                        onClick={() => removeAllowed.mutate(n)}>✕</button></span>))}
            <input placeholder="+1 514 555 1234" value={newSender}
                   style={{ width: 160 }}
                   onChange={(e) => setNewSender(e.target.value)}
                   onKeyDown={(e) => e.key === "Enter" && newSender.trim()
                     && addAllowed.mutate(newSender)} />
            <button className="ghost" disabled={!newSender.trim() || addAllowed.isPending}
                    onClick={() => addAllowed.mutate(newSender)}>＋ Allow</button>
          </div>
        </div>
      </Section>

      <Section title="Import statements & sheets"><ImportReview /></Section>
    </div>
  );
}
