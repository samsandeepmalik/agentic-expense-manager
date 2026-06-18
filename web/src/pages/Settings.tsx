import { useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { del, get, patch, post, put, type AuditRow, type Category,
         type Profile, type RecurringRule, type TaxProfile, type WaAccount } from "../api";
import { money } from "../format";
import { ImportReview } from "../components/ImportReview";
import { CategoryPicker } from "../components/CategoryPicker";

function Section({ title, children, note }: { title: string; note?: string; children: React.ReactNode }) {
  return <div className="card">
    <h3 className="section-title" style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
      <span>{title}</span>
      {note && <span className="muted" style={{ fontSize: 11, fontWeight: 400, textTransform: "none",
        letterSpacing: 0 }}>— for profile: {note}</span>}
    </h3>
    {children}
  </div>;
}

const SECTIONS = [
  { id: "categories", label: "Categories" },
  { id: "tax", label: "Tax profile" },
  { id: "recurring", label: "Recurring" },
  { id: "ocr", label: "Receipt OCR" },
  { id: "profiles", label: "Profiles" },
  { id: "google", label: "Google sync" },
  { id: "whatsapp", label: "WhatsApp" },
  { id: "imports", label: "Imports" },
  { id: "activity", label: "Activity" },
] as const;
type SectionId = typeof SECTIONS[number]["id"];

// --- Sheet preview helpers ---
const SAMPLE_TAX: Record<string, number> = { GST: 4.43, QST: 8.84 };
// Keys MUST match the backend column registry keys (subcategory, counted_pct,
// receipt_name, …) so the preview sample row isn't blank for those columns.
const SAMPLE_ROW: Record<string, string> = {
  id: "1",
  date: "2026-06-07",
  type: "expense",
  category: "Transport",
  subcategory: "Petrol",
  description: "Weekly fill-up",
  merchant: "Shell",
  amount: "88.50",
  tax: "13.27",
  total: "101.77",
  counted_pct: "20%",
  counted: "20.35",
  receipt_name: "2026-06-07_shell",
  receipt_link: "drive/link",
  source: "ui",
  loan: "",
  notes: "sample note",
  created: "2026-06-07 09:12",
  updated: "2026-06-07 09:12",
};

function SheetPreview({ selected, available, taxComponents }: {
  selected: string[];
  available: { key: string; label: string }[];
  taxComponents: { name: string; rate: number }[];
}) {
  const labelFor = (k: string) => available.find((a) => a.key === k)?.label ?? k;

  // Expand "tax" key into individual tax component columns
  const expandedCols: { key: string; label: string }[] = [];
  for (const k of selected) {
    if (k === "tax" && taxComponents.length > 0) {
      for (const tc of taxComponents) {
        expandedCols.push({ key: `_tax_${tc.name}`, label: tc.name });
      }
    } else {
      expandedCols.push({ key: k, label: labelFor(k) });
    }
  }

  // Build sample values row
  const sampleRow: string[] = expandedCols.map(({ key }) => {
    if (key.startsWith("_tax_")) {
      const name = key.slice(5);
      const val = SAMPLE_TAX[name];
      return val != null ? `$${val.toFixed(2)}` : "$0.00";
    }
    const v = SAMPLE_ROW[key] ?? "";
    if (key === "amount" || key === "total" || key === "counted") return `$${v}`;
    return v;
  });

  // Totals row: sum money columns
  const moneyKeys = new Set(["amount", "total", "counted", "tax"]);
  const totalsRow: string[] = expandedCols.map(({ key }) => {
    if (key === "id") return "TOTALS";
    if (key.startsWith("_tax_")) {
      const name = key.slice(5);
      const val = SAMPLE_TAX[name];
      return val != null ? `$${val.toFixed(2)}` : "";
    }
    if (moneyKeys.has(key)) {
      const v = SAMPLE_ROW[key];
      return v ? `$${v}` : "";
    }
    return "";
  });

  const thStyle: React.CSSProperties = {
    background: "#d4d4c8", fontWeight: 700, fontSize: 11,
    padding: "4px 8px", border: "1px solid #b0b09a", textAlign: "left",
    whiteSpace: "nowrap",
  };
  const tdStyle: React.CSSProperties = {
    padding: "3px 8px", border: "1px solid #d0d0c0", fontSize: 11,
    whiteSpace: "nowrap",
  };
  const tdTotalStyle: React.CSSProperties = {
    ...tdStyle, fontWeight: 700, color: "var(--green)", background: "#eaf4ee",
  };

  return (
    <div style={{ overflowX: "auto", marginTop: 10, marginBottom: 4 }}>
      <p className="muted" style={{ fontSize: 11, marginBottom: 6 }}>
        Sheet preview — one example row with plausible values:
      </p>
      <table style={{ borderCollapse: "collapse", fontSize: 11, width: "auto" }}>
        <thead>
          <tr>
            {expandedCols.map(({ key, label }) => (
              <th key={key} style={thStyle}>{label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          <tr>
            {sampleRow.map((v, i) => (
              <td key={expandedCols[i].key} style={tdStyle}>{v || <span className="muted">—</span>}</td>
            ))}
          </tr>
          <tr>
            {totalsRow.map((v, i) => (
              <td key={`tot-${expandedCols[i].key}`} style={tdTotalStyle}>{v || ""}</td>
            ))}
          </tr>
        </tbody>
      </table>
    </div>
  );
}

export default function Settings() {
  const queryClient = useQueryClient();
  const invalidate = (key: string) => queryClient.invalidateQueries({ queryKey: [key] });

  const location = useLocation();
  const active: SectionId = (SECTIONS.find((s) => s.id === location.hash.slice(1))?.id
    ?? "categories");

  // --- Categories ---
  const categories = useQuery({ queryKey: ["categories"],
    queryFn: () => get<Category[]>("/api/categories") });
  const [saveCategoryError, setSaveCategoryError] = useState<string | null>(null);
  const saveCategory = useMutation({
    mutationFn: (c: Partial<Category>) => post("/api/categories", c),
    onSuccess: () => { setSaveCategoryError(null); invalidate("categories"); },
    onError: (e: Error) => setSaveCategoryError(e.message),
  });
  const [removeCategoryError, setRemoveCategoryError] = useState<string | null>(null);
  const removeCategory = useMutation({
    mutationFn: (id: number) => del(`/api/categories/${id}`),
    onSuccess: () => { setRemoveCategoryError(null); invalidate("categories"); },
    onError: (e: Error) => setRemoveCategoryError(e.message),
  });
  const [reparentError, setReparentError] = useState<string | null>(null);
  const reparent = useMutation({
    mutationFn: ({ id, parent_id }: { id: number; parent_id: number }) =>
      patch(`/api/categories/${id}`, { parent_id }),
    onSuccess: () => { setReparentError(null); invalidate("categories"); },
    onError: (e: Error) => setReparentError(e.message),
  });
  const [newCategory, setNewCategory] = useState({ name: "", type: "expense",
    percent: 100, taxable: true, budget_monthly: "", parent_id: 0 });

  // --- Tax profiles ---
  const taxProfiles = useQuery({ queryKey: ["tax-profiles"],
    queryFn: () => get<TaxProfile[]>("/api/tax-profiles") });
  const [activateError, setActivateError] = useState<string | null>(null);
  const activate = useMutation({
    mutationFn: (p: TaxProfile) => post("/api/tax-profiles",
      { name: p.name, components: p.components, activate: true }),
    onSuccess: () => { setActivateError(null); invalidate("tax-profiles"); },
    onError: (e: Error) => setActivateError(e.message),
  });

  // Tax profile create/edit form
  const [taxForm, setTaxForm] = useState({
    name: "",
    components: [{ name: "", rate: "" }] as { name: string; rate: string }[],
    activate: false,
  });
  const [taxFormSuccess, setTaxFormSuccess] = useState(false);
  const [saveTaxError, setSaveTaxError] = useState<string | null>(null);
  const saveTax = useMutation({
    mutationFn: (body: { name: string; components: { name: string; rate: number }[]; activate: boolean }) =>
      post("/api/tax-profiles", body),
    onSuccess: () => {
      setSaveTaxError(null);
      setTaxForm({ name: "", components: [{ name: "", rate: "" }], activate: false });
      setTaxFormSuccess(true);
      setTimeout(() => setTaxFormSuccess(false), 2500);
      invalidate("tax-profiles");
    },
    onError: (e: Error) => setSaveTaxError(e.message),
  });

  // Active tax profile components (for sheet preview)
  const activeTaxComponents = taxProfiles.data?.find((p) => p.is_active)?.components ?? [];

  // --- Recurring ---
  const rules = useQuery({ queryKey: ["recurring"],
    queryFn: () => get<RecurringRule[]>("/api/recurring") });
  const [removeRuleError, setRemoveRuleError] = useState<string | null>(null);
  const removeRule = useMutation({
    mutationFn: (id: number) => del(`/api/recurring/${id}`),
    onSuccess: () => { setRemoveRuleError(null); invalidate("recurring"); },
    onError: (e: Error) => setRemoveRuleError(e.message),
  });
  const [toggleRuleError, setToggleRuleError] = useState<string | null>(null);
  const toggleRule = useMutation({
    mutationFn: ({ id, active }: { id: number; active: boolean }) =>
      patch(`/api/recurring/${id}`, { active }),
    onSuccess: () => { setToggleRuleError(null); invalidate("recurring"); },
    onError: (e: Error) => setToggleRuleError(e.message),
  });
  const [recurringForm, setRecurringForm] = useState({
    categoryId: null as number | null,
    type: "expense",
    total: "",
    merchant: "",
    frequency: "monthly",
    next_run: "",
  });
  const [recurringEditId, setRecurringEditId] = useState<number | null>(null);
  const [recurringFormSuccess, setRecurringFormSuccess] = useState(false);
  const [saveRuleError, setSaveRuleError] = useState<string | null>(null);
  const saveRule = useMutation({
    mutationFn: (body: { template: { category_id: number | null; type: string; total: number; merchant: string; description: string }; frequency: string; next_run: string }) =>
      recurringEditId != null
        ? patch(`/api/recurring/${recurringEditId}`, body)
        : post("/api/recurring", body),
    onSuccess: () => {
      setSaveRuleError(null);
      setRecurringForm({ categoryId: null, type: "expense", total: "", merchant: "", frequency: "monthly", next_run: "" });
      setRecurringEditId(null);
      setRecurringFormSuccess(true);
      setTimeout(() => setRecurringFormSuccess(false), 2500);
      invalidate("recurring");
    },
    onError: (e: Error) => setSaveRuleError(e.message),
  });

  // --- OCR provider ---
  const ocr = useQuery({ queryKey: ["ocr"],
    queryFn: () => get<{ provider: string; available: Record<string, boolean> }>(
      "/api/settings/ocr") });
  const [setOcrError, setSetOcrError] = useState<string | null>(null);
  const setOcr = useMutation({
    mutationFn: (provider: string) => post("/api/settings/ocr", { provider }),
    onSuccess: () => { setSetOcrError(null); invalidate("ocr"); },
    onError: (e: Error) => setSetOcrError(e.message),
  });

  // --- Activity ---
  const activity = useQuery({ queryKey: ["audit"], refetchInterval: 10000,
    queryFn: () => get<AuditRow[]>("/api/audit?limit=50") });

  // --- Connections ---
  const google = useQuery({ queryKey: ["google"], refetchInterval: 4000,
    queryFn: () => get<{
      configured: boolean; connected: boolean;
      redirect_uri: string; pending: number;
      last_error: string | null; last_synced_at: string | null;
      last_synced_count: number | null;
      folder_name: string | null; scope_version: "legacy" | "sandboxed" | null;
      profiles: Array<{
        id: number; name: string;
        sheet_url: string | null; drive_folder_url: string | null;
        sheet_in_drive: boolean; pending: number;
        sync_error?: string | null;
      }>;
    }>("/api/google/status") });
  const whatsapp = useQuery({ queryKey: ["whatsapp"], refetchInterval: 4000,
    queryFn: () => get<WaAccount[]>("/api/whatsapp/accounts") });
  const [addWaError, setAddWaError] = useState<string | null>(null);
  const addWa = useMutation({
    mutationFn: () => post("/api/whatsapp/accounts"),
    onSuccess: () => { setAddWaError(null); invalidate("whatsapp"); },
    onError: (e: Error) => setAddWaError(e.message),
  });
  const [refreshWaError, setRefreshWaError] = useState<string | null>(null);
  const refreshWa = useMutation({
    mutationFn: (id: string) => post(`/api/whatsapp/accounts/${id}/refresh`),
    onSuccess: () => { setRefreshWaError(null); invalidate("whatsapp"); },
    onError: (e: Error) => setRefreshWaError(e.message),
  });
  const [removeWaError, setRemoveWaError] = useState<string | null>(null);
  const removeWa = useMutation({
    mutationFn: (id: string) => del(`/api/whatsapp/accounts/${id}`),
    onSuccess: () => { setRemoveWaError(null); invalidate("whatsapp"); },
    onError: (e: Error) => setRemoveWaError(e.message),
  });
  const allowed = useQuery({ queryKey: ["wa-allowed"],
    queryFn: () => get<{ allowed: string[] }>("/api/whatsapp/allowed") });
  const [newSender, setNewSender] = useState("");
  const [addAllowedError, setAddAllowedError] = useState<string | null>(null);
  const addAllowed = useMutation({
    mutationFn: (number: string) => post("/api/whatsapp/allowed", { number }),
    onSuccess: () => { setNewSender(""); setAddAllowedError(null); invalidate("wa-allowed"); },
    onError: (e: Error) => setAddAllowedError(e.message),
  });
  const [removeAllowedError, setRemoveAllowedError] = useState<string | null>(null);
  const removeAllowed = useMutation({
    mutationFn: (number: string) => del(`/api/whatsapp/allowed/${number}`),
    onSuccess: () => { setRemoveAllowedError(null); invalidate("wa-allowed"); },
    onError: (e: Error) => setRemoveAllowedError(e.message),
  });
  const [syncNowError, setSyncNowError] = useState<string | null>(null);
  const syncNow = useMutation({
    mutationFn: () => post("/api/sync/now"),
    onSuccess: () => { setSyncNowError(null); invalidate("google"); },
    onError: (e: Error) => setSyncNowError(e.message),
    onSettled: () => invalidate("google"),
  });
  const [resyncError, setResyncError] = useState<string | null>(null);
  const resyncNow = useMutation({
    mutationFn: () => post("/api/sync/resync"),
    onSuccess: () => { setResyncError(null); invalidate("google"); },
    onError: (e: Error) => setResyncError(e.message),
    onSettled: () => invalidate("google"),
  });
  const [resetSheetError, setResetSheetError] = useState<string | null>(null);
  const resetSheet = useMutation({
    mutationFn: (id: number) => post(`/api/google/profiles/${id}/reset-sheet`),
    onSuccess: () => { setResetSheetError(null); invalidate("google"); },
    onError: (e: Error) => setResetSheetError(e.message),
  });
  const [showSetupGuide, setShowSetupGuide] = useState(false);
  const [showColumnPreview, setShowColumnPreview] = useState(false);

  // --- Sheet column layout (per profile) ---
  const [columnsProfileId, setColumnsProfileId] = useState<number | null>(null);
  const [selectedCols, setSelectedCols] = useState<string[] | null>(null);
  const [saveColumnsSuccess, setSaveColumnsSuccess] = useState(false);
  const [saveColumnsError, setSaveColumnsError] = useState<string | null>(null);
  const columns = useQuery({
    queryKey: ["sheet-columns", columnsProfileId],
    enabled: columnsProfileId != null,
    queryFn: () => get<{
      available: { key: string; label: string }[];
      selected: string[]; profile_id: number;
    }>(`/api/google/columns?profile_id=${columnsProfileId}`),
  });
  const saveColumns = useMutation({
    mutationFn: (cols: string[]) =>
      put("/api/google/columns", { profile_id: columnsProfileId, columns: cols }),
    onSuccess: () => {
      setSaveColumnsError(null);
      setSaveColumnsSuccess(true);
      setTimeout(() => setSaveColumnsSuccess(false), 2500);
      queryClient.invalidateQueries({ queryKey: ["sheet-columns"] });
      invalidate("google");
    },
    onError: (e: Error) => setSaveColumnsError(e.message),
  });

  // --- Account profiles ---
  const profilesQ = useQuery({ queryKey: ["profiles"],
    queryFn: () => get<Profile[]>("/api/profiles") });
  const activeProfileName = profilesQ.data?.find((p) => p.active)?.name ?? null;
  const [newProfile, setNewProfile] = useState({ name: "", kind: "personal" });
  const addProfile = useMutation({
    mutationFn: () => post("/api/profiles", newProfile),
    onSuccess: () => { setNewProfile({ name: "", kind: "personal" });
      queryClient.invalidateQueries({ queryKey: ["profiles"] }); } });
  const activateProfile = useMutation({
    mutationFn: (id: number) => post(`/api/profiles/${id}/activate`),
    onSuccess: () => queryClient.invalidateQueries() });
  const removeProfile = useMutation({
    mutationFn: (id: number) => del(`/api/profiles/${id}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["profiles"] }) });
  const updateProfile = useMutation({
    mutationFn: ({ id, prompt_loan }: { id: number; prompt_loan: boolean }) =>
      patch(`/api/profiles/${id}`, { prompt_loan }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["profiles"] }),
  });

  // --- Drive folder name ---
  const [folderName, setFolderName] = useState("");
  const [saveFolderNameSuccess, setSaveFolderNameSuccess] = useState(false);
  const [saveFolderNameError, setSaveFolderNameError] = useState<string | null>(null);
  const saveFolderName = useMutation({
    mutationFn: (name: string) => post("/api/google/folder-name", { name }),
    onSuccess: () => {
      setSaveFolderNameError(null);
      setSaveFolderNameSuccess(true);
      setTimeout(() => setSaveFolderNameSuccess(false), 2500);
      queryClient.invalidateQueries({ queryKey: ["google"] });
    },
    onError: (e: Error) => setSaveFolderNameError(e.message),
  });

  // --- Google OAuth client credentials (one-time setup) ---
  const [credTab, setCredTab] = useState<"json" | "manual">("json");
  const [jsonText, setJsonText] = useState("");
  const [jsonError, setJsonError] = useState("");
  const [creds, setCreds] = useState({ id: "", secret: "" });
  const [showTooltip, setShowTooltip] = useState(false);
  const [showResyncTooltip, setShowResyncTooltip] = useState(false);
  const saveCreds = useMutation({
    mutationFn: (payload: { client_id: string; client_secret: string }) =>
      post("/api/google/credentials", payload),
    onSuccess: () => {
      setCreds({ id: "", secret: "" });
      setJsonText("");
      queryClient.invalidateQueries({ queryKey: ["google"] });
    },
  });

  const hints: Record<SectionId, string> = {
    categories: String(categories.data?.length ?? ""),
    tax: taxProfiles.data?.find((p) => p.is_active)?.name?.toUpperCase() ?? "",
    recurring: String(rules.data?.length ?? ""),
    ocr: (ocr.data?.provider ?? "").toUpperCase(),
    profiles: String(profilesQ.data?.length ?? ""),
    google: !google.data ? "" : !google.data.configured ? "SETUP"
      : google.data.connected ? "CONNECTED" : "OFF",
    whatsapp: String(whatsapp.data?.length ?? ""),
    imports: "",
    activity: String(activity.data?.length ?? ""),
  };

  return (
    <div className="settings reveal">
      <nav className="rail">
        {SECTIONS.map((s) => (
          <Link key={s.id} to={`#${s.id}`}
                className={`rail-item${active === s.id ? " invert" : ""}`}>
            <span>{s.label}</span>
            <span className="mono rail-hint">{hints[s.id]}</span>
          </Link>))}
      </nav>
      <div>
        {active === "categories" && (
          <Section title="Categories & budgets" note={activeProfileName ?? undefined}>
            {(saveCategoryError || reparentError || removeCategoryError) && (
              <p className="neg" style={{ marginBottom: 8 }}>
                {saveCategoryError ?? reparentError ?? removeCategoryError}
              </p>
            )}
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Parent</th>
                  <th>Type</th>
                  <th>% counted</th>
                  <th>Taxable</th>
                  <th>Budget/mo</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {(() => {
                  const cats = categories.data ?? [];
                  const childrenOf = (id: number) =>
                    cats.filter((c) => c.parent_id === id);
                  const renderRow = (c: Category, child: boolean) => {
                    const hasChildren = childrenOf(c.id).length > 0;
                    return (
                      <tr key={c.id}>
                        <td style={child ? { paddingLeft: 24 } : undefined}>
                          {child ? "↳ " : ""}{c.name}
                        </td>
                        <td>
                          <select className="reparent" value={c.parent_id}
                                  disabled={hasChildren}
                                  title={hasChildren ? "Promote sub-categories first" : undefined}
                                  style={{ opacity: hasChildren ? 0.45 : 1 }}
                                  onChange={(e) => reparent.mutate(
                                    { id: c.id, parent_id: Number(e.target.value) })}>
                            <option value={0}>— top level —</option>
                            {cats.filter((p) => p.parent_id === 0 && p.id !== c.id)
                              .map((p) => (
                                <option key={p.id} value={p.id}>↳ under {p.name}</option>))}
                          </select>
                        </td>
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
                        <td><button className="ghost danger"
                              onClick={() => {
                                if (window.confirm(`Delete category '${c.name}'? This cannot be undone.`))
                                  removeCategory.mutate(c.id);
                              }}>✕</button></td>
                      </tr>
                    );
                  };
                  return cats.filter((c) => c.parent_id === 0).flatMap((t) =>
                    [renderRow(t, false), ...childrenOf(t.id).map((ch) => renderRow(ch, true))]);
                })()}
                {/* Add-row: columns aligned to header */}
                <tr>
                  <td>
                    <input placeholder="New category" value={newCategory.name}
                          onChange={(e) => setNewCategory({ ...newCategory, name: e.target.value })} />
                  </td>
                  <td>
                    <select value={newCategory.parent_id}
                            onChange={(e) => setNewCategory({ ...newCategory,
                              parent_id: Number(e.target.value) })}>
                      <option value={0}>— none —</option>
                      {(categories.data ?? []).filter((c) => c.parent_id === 0).map((c) => (
                        <option key={c.id} value={c.id}>{c.name}</option>))}
                    </select>
                  </td>
                  <td>
                    <select value={newCategory.type}
                          onChange={(e) => setNewCategory({ ...newCategory, type: e.target.value })}>
                      <option value="expense">expense</option>
                      <option value="income">income</option>
                    </select>
                  </td>
                  <td></td>
                  <td></td>
                  <td></td>
                  <td>
                    <button className="ghost" disabled={!newCategory.name}
                          onClick={() => { saveCategory.mutate({ ...newCategory,
                            budget_monthly: newCategory.budget_monthly
                              ? Number(newCategory.budget_monthly) : null } as Partial<Category>);
                            setNewCategory({ ...newCategory, name: "", parent_id: 0 }); }}>
                      ＋ Add
                    </button>
                  </td>
                </tr>
              </tbody>
            </table>
          </Section>
        )}

        {active === "tax" && (
          <Section title="Tax profile" note={activeProfileName ?? undefined}>
            <p className="muted">Active profile drives tax back-calculation for taxable categories.</p>
            {activateError && <p className="neg">{activateError}</p>}
            {(taxProfiles.data ?? []).map((p) => (
              <div key={p.id} className="row" style={{ marginTop: 8, alignItems: "center", gap: 8 }}>
                <label style={{ display: "flex", alignItems: "center", gap: 6, flex: 1 }}>
                  <input type="radio" name="tax" checked={p.is_active}
                         onChange={() => activate.mutate(p)} />
                  <b>{p.name}</b>{" "}
                  <span className="muted">
                    {p.components.map((c) => `${c.name} ${c.rate}%`).join(" + ")}</span>
                </label>
                <button className="ghost" style={{ fontSize: 12, padding: "2px 8px" }}
                        onClick={() => setTaxForm({
                          name: p.name,
                          components: p.components.map((c) => ({ name: c.name, rate: String(c.rate) })),
                          activate: p.is_active,
                        })}>
                  Edit
                </button>
              </div>))}

            <div style={{ marginTop: 16, borderTop: "1px solid var(--hairline)", paddingTop: 14 }}>
              <b style={{ fontSize: 13 }}>{taxForm.name && (taxProfiles.data ?? []).some(p => p.name === taxForm.name) ? "Edit" : "Add"} tax profile</b>
              <div className="row" style={{ marginTop: 8, alignItems: "center", gap: 8 }}>
                <input placeholder="Profile name" value={taxForm.name}
                       style={{ width: 180 }}
                       onChange={(e) => setTaxForm((f) => ({ ...f, name: e.target.value }))} />
              </div>
              <div style={{ marginTop: 10 }}>
                <span className="muted" style={{ fontSize: 12 }}>Components</span>
                {taxForm.components.map((comp, i) => (
                  <div key={i} className="row" style={{ marginTop: 6, gap: 6, alignItems: "center" }}>
                    <input placeholder="Name (e.g. GST)" value={comp.name}
                           style={{ width: 130 }}
                           onChange={(e) => {
                             const next = [...taxForm.components];
                             next[i] = { ...next[i], name: e.target.value };
                             setTaxForm((f) => ({ ...f, components: next }));
                           }} />
                    <input type="number" placeholder="Rate %" value={comp.rate}
                           min={0} max={100} step={0.01}
                           style={{ width: 80 }}
                           onChange={(e) => {
                             const next = [...taxForm.components];
                             next[i] = { ...next[i], rate: e.target.value };
                             setTaxForm((f) => ({ ...f, components: next }));
                           }} />
                    <span className="muted" style={{ fontSize: 12 }}>%</span>
                    <button className="ghost danger" style={{ padding: "2px 6px" }}
                            disabled={taxForm.components.length === 1}
                            onClick={() => {
                              const next = taxForm.components.filter((_, j) => j !== i);
                              setTaxForm((f) => ({ ...f, components: next }));
                            }}>✕</button>
                  </div>
                ))}
                <button className="ghost" style={{ marginTop: 8, fontSize: 12 }}
                        onClick={() => setTaxForm((f) => ({
                          ...f, components: [...f.components, { name: "", rate: "" }]
                        }))}>
                  + Add component
                </button>
              </div>
              <div className="row" style={{ marginTop: 10, alignItems: "center", gap: 10 }}>
                <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 13 }}>
                  <input type="checkbox" checked={taxForm.activate}
                         onChange={(e) => setTaxForm((f) => ({ ...f, activate: e.target.checked }))} />
                  Activate on save
                </label>
                <button className="primary"
                        disabled={
                          !taxForm.name.trim() ||
                          !taxForm.components.some((c) => c.name.trim() && c.rate !== "") ||
                          saveTax.isPending
                        }
                        onClick={() => saveTax.mutate({
                          name: taxForm.name.trim(),
                          components: taxForm.components
                            .filter((c) => c.name.trim() && c.rate !== "")
                            .map((c) => ({ name: c.name.trim(), rate: Number(c.rate) })),
                          activate: taxForm.activate,
                        })}>
                  {saveTax.isPending ? "Saving…" : "Save profile"}
                </button>
                {taxFormSuccess && (
                  <span style={{ color: "var(--green)", fontSize: 12, fontWeight: 600 }}>Saved ✓</span>
                )}
              </div>
              {saveTaxError && <p className="neg" style={{ marginTop: 6 }}>{saveTaxError}</p>}
            </div>
          </Section>
        )}

        {active === "recurring" && (
          <Section title="Recurring rules" note={activeProfileName ?? undefined}>
            {(removeRuleError || toggleRuleError) && (
              <p className="neg">{removeRuleError ?? toggleRuleError}</p>
            )}
            {(rules.data ?? []).length === 0 &&
              <p className="muted">None yet.</p>}
            {(rules.data ?? []).length > 0 && (
              <table>
                <thead><tr>
                  <th>Active</th>
                  <th>Category</th>
                  <th className="num">Amount</th>
                  <th>Frequency</th>
                  <th>Next run</th>
                  <th></th>
                </tr></thead>
                <tbody>
                  {(rules.data ?? []).map((r) => {
                    const cats = categories.data ?? [];
                    const catId = r.template.category_id as number | undefined;
                    let catLabel: string;
                    if (catId != null) {
                      const cat = cats.find((c) => c.id === catId);
                      if (cat) {
                        if (cat.parent_id !== 0) {
                          const parent = cats.find((c) => c.id === cat.parent_id);
                          catLabel = parent ? `${parent.name} › ${cat.name}` : cat.name;
                        } else {
                          catLabel = cat.name;
                        }
                      } else {
                        catLabel = String(catId);
                      }
                    } else {
                      catLabel = String(r.template.category ?? "—");
                    }
                    const resolvedCatId = catId != null ? catId
                      : (() => {
                          const name = String(r.template.category ?? "");
                          return cats.find((c) => c.name === name)?.id ?? null;
                        })();
                    return (
                    <tr key={r.id} style={{ opacity: r.active ? 1 : 0.5 }}>
                      <td>
                        <input type="checkbox" checked={r.active}
                               onChange={(e) => toggleRule.mutate({ id: r.id, active: e.target.checked })} />
                      </td>
                      <td><b>{catLabel}</b></td>
                      <td className="num">{money(Number(r.template.total ?? 0))}</td>
                      <td><span className="tag">{r.frequency}</span></td>
                      <td className="mono muted">{r.next_run}</td>
                      <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                        <button className="ghost" style={{ fontSize: 12, padding: "2px 6px", marginRight: 4 }}
                                onClick={() => {
                                  setRecurringEditId(r.id);
                                  setRecurringForm({
                                    categoryId: resolvedCatId,
                                    type: String(r.template.type ?? "expense"),
                                    total: String(r.template.total ?? ""),
                                    merchant: String(r.template.merchant ?? ""),
                                    frequency: r.frequency,
                                    next_run: r.next_run,
                                  });
                                }}>Edit</button>
                        <button className="ghost danger" aria-label="Delete rule"
                                onClick={() => {
                                  if (window.confirm(`Delete this recurring rule (${r.frequency} · ${catLabel})? This cannot be undone.`))
                                    removeRule.mutate(r.id);
                                }}>✕</button>
                      </td>
                    </tr>);
                  })}
                </tbody>
              </table>)}

            <div style={{ marginTop: 16, borderTop: "1px solid var(--hairline)", paddingTop: 14 }}>
              <b style={{ fontSize: 13 }}>{recurringEditId != null ? "Edit" : "Add"} recurring rule</b>
              {recurringEditId != null && (
                <button className="ghost" style={{ marginLeft: 10, fontSize: 12, padding: "2px 8px" }}
                        onClick={() => {
                          setRecurringEditId(null);
                          setRecurringForm({ categoryId: null, type: "expense", total: "", merchant: "", frequency: "monthly", next_run: "" });
                        }}>
                  Cancel edit
                </button>
              )}
              <div className="row" style={{ marginTop: 10, flexWrap: "wrap", gap: 8, alignItems: "center" }}>
                <select value={recurringForm.type}
                        onChange={(e) => setRecurringForm((f) => ({ ...f, type: e.target.value, categoryId: null }))}>
                  <option value="expense">expense</option>
                  <option value="income">income</option>
                </select>
                <div style={{ minWidth: 220 }}>
                  <CategoryPicker
                    categories={categories.data ?? []}
                    type={recurringForm.type as "expense" | "income"}
                    valueId={recurringForm.categoryId}
                    onChange={(id) => setRecurringForm((f) => ({ ...f, categoryId: id }))}
                  />
                </div>
                <input type="number" placeholder="Total paid" value={recurringForm.total}
                       min={0} step={0.01}
                       style={{ width: 110 }}
                       onChange={(e) => setRecurringForm((f) => ({ ...f, total: e.target.value }))} />
                <input placeholder="Merchant (optional)" value={recurringForm.merchant}
                       style={{ width: 150 }}
                       onChange={(e) => setRecurringForm((f) => ({ ...f, merchant: e.target.value }))} />
                <select value={recurringForm.frequency}
                        onChange={(e) => setRecurringForm((f) => ({ ...f, frequency: e.target.value }))}>
                  <option value="weekly">weekly</option>
                  <option value="biweekly">biweekly</option>
                  <option value="monthly">monthly</option>
                </select>
                <input type="date" value={recurringForm.next_run}
                       min={new Date().toISOString().slice(0, 10)}
                       title="First run date — a past date back-fills missed periods"
                       onChange={(e) => setRecurringForm((f) => ({ ...f, next_run: e.target.value }))} />
              </div>
              <div className="row" style={{ marginTop: 10, gap: 8, alignItems: "center" }}>
                <button className="primary"
                        disabled={
                          recurringForm.categoryId === null ||
                          !recurringForm.total ||
                          !recurringForm.next_run ||
                          saveRule.isPending
                        }
                        onClick={() => saveRule.mutate({
                          template: {
                            category_id: recurringForm.categoryId,
                            type: recurringForm.type,
                            total: Number(recurringForm.total),
                            merchant: recurringForm.merchant,
                            description: "",
                          },
                          frequency: recurringForm.frequency,
                          next_run: recurringForm.next_run,
                        })}>
                  {saveRule.isPending ? "Saving…" : recurringEditId != null ? "Update rule" : "Add rule"}
                </button>
                {recurringFormSuccess && (
                  <span style={{ color: "var(--green)", fontSize: 12, fontWeight: 600 }}>Saved ✓</span>
                )}
              </div>
              {saveRuleError && <p className="neg" style={{ marginTop: 6 }}>{saveRuleError}</p>}
            </div>
          </Section>
        )}

        {active === "ocr" && (
          <Section title="Receipt OCR">
            <p className="muted">Which model reads text out of receipt photos.</p>
            {setOcrError && <p className="neg">{setOcrError}</p>}
            {([["nvidia", "NVIDIA PaddleOCR", "NVIDIA_API_KEY"],
               ["claude", "Claude vision", "CLAUDE_CODE_OAUTH_TOKEN / ANTHROPIC_API_KEY"],
               ["openai", "OpenAI vision", "OPENAI_API_KEY"]] as const).map(
              ([key, label, env]) => {
                const configured = ocr.data?.available?.[key] ?? false;
                return (
                  <label key={key} style={{ display: "block", marginTop: 8,
                                            opacity: configured ? 1 : 0.55 }}>
                    <input type="radio" name="ocr" disabled={!configured}
                           checked={ocr.data?.provider === key}
                           onChange={() => setOcr.mutate(key)} />{" "}
                    <b>{label}</b>{" "}
                    {!configured && <span className="muted">set {env} in .env</span>}
                  </label>);
              })}
          </Section>
        )}

        {active === "profiles" && (
          <Section title="Profiles">
            <p className="muted">Separate books — each profile has its own transactions,
              categories, tax profile and Google sheet/folder.</p>
            {(profilesQ.data ?? []).map((p) => (
              <div key={p.id} style={{ padding: "6px 0", borderBottom: "1px solid var(--hairline)" }}>
                <div className="row">
                  <b>{p.kind === "incorporation" ? "🏢" : "👤"} {p.name}</b>
                  {p.active
                    ? <span className="tag income">active</span>
                    : <button className="ghost"
                              onClick={() => activateProfile.mutate(p.id)}>Switch to</button>}
                  <span className="grow" />
                  {!p.active && (
                    <button className="ghost danger"
                            onClick={() => {
                              if (window.confirm(`Delete profile '${p.name}'? Its categories and tax settings will be lost.`))
                                removeProfile.mutate(p.id);
                            }}>Delete</button>)}
                </div>
                <label style={{ display: "flex", alignItems: "center", gap: 6,
                                fontSize: 12, color: "var(--muted)", marginTop: 4,
                                cursor: "pointer" }}>
                  <input
                    type="checkbox"
                    checked={!!p.prompt_loan}
                    onChange={(e) => updateProfile.mutate({ id: p.id, prompt_loan: e.target.checked })}
                  />
                  Ask "paid from personal pocket?" when recording expenses (chat &amp; WhatsApp)
                </label>
              </div>))}
            {removeProfile.error && <p className="neg">
              {(removeProfile.error as Error).message}</p>}
            <div className="row" style={{ marginTop: 10 }}>
              <input placeholder="Profile name" value={newProfile.name}
                     onChange={(e) => setNewProfile((f) => ({ ...f, name: e.target.value }))} />
              <select value={newProfile.kind}
                      onChange={(e) => setNewProfile((f) => ({ ...f, kind: e.target.value }))}>
                <option value="personal">Personal</option>
                <option value="incorporation">Incorporation</option>
                <option value="other">Other</option>
              </select>
              <button className="primary" disabled={!newProfile.name.trim() || addProfile.isPending}
                      onClick={() => addProfile.mutate()}>＋ Create</button>
            </div>
            {addProfile.error && <p className="neg">
              {(addProfile.error as Error).message}</p>}
          </Section>
        )}

        {active === "google" && (
          <Section title="Google sync">
            {!google.data ? <p>Loading…</p>
              : !google.data.configured ? (
                <>
                  <div className="row" style={{ alignItems: "center", marginBottom: 8 }}>
                    <button className={credTab === "json" ? "primary" : "ghost"}
                            onClick={() => setCredTab("json")}>JSON key</button>
                    <button className={credTab === "manual" ? "primary" : "ghost"}
                            onClick={() => setCredTab("manual")}>Manual</button>
                    <span style={{ position: "relative", marginLeft: 8 }}>
                      <span style={{ cursor: "help", fontWeight: "bold", fontSize: 13 }}
                            onMouseEnter={() => setShowTooltip(true)}
                            onMouseLeave={() => setShowTooltip(false)}>?</span>
                      {showTooltip && (
                        <div className="card" style={{
                          position: "absolute", top: 24, left: 0, zIndex: 10,
                          width: 320, padding: 12, fontSize: 13 }}>
                          <p style={{ margin: "0 0 6px" }}>
                            <b>JSON key</b> — Easiest. Use <b>Desktop app</b> type in Google Cloud
                            Console — no redirect URI needed. Download JSON and paste below.
                          </p>
                          <p style={{ margin: 0 }}>
                            <b>Manual</b> — Advanced. Use <b>Web application</b> type.
                            Must pre-register the redirect URI shown exactly.
                          </p>
                        </div>
                      )}
                    </span>
                  </div>

                  {credTab === "json" ? (
                    <>
                      <ol className="muted" style={{ paddingLeft: 18, marginBottom: 10, fontSize: 13 }}>
                        <li>Go to <a href="https://console.cloud.google.com/apis/credentials"
                                     target="_blank" rel="noreferrer">Google Cloud Console</a> → create a project</li>
                        <li>APIs &amp; Services → Library → enable <b>Google Drive API</b></li>
                        <li>APIs &amp; Services → Library → enable <b>Google Sheets API</b></li>
                        <li>OAuth consent screen → User type: <b>External</b> → fill name + email → add yourself as test user</li>
                        <li>Credentials → <b>Create credentials</b> → OAuth 2.0 Client ID → <b>Desktop app</b></li>
                        <li>Download JSON → paste below</li>
                      </ol>
                      <textarea
                        placeholder="Paste the downloaded JSON key here…"
                        value={jsonText}
                        rows={6}
                        style={{ width: "100%", fontFamily: "monospace", fontSize: 12 }}
                        onChange={(e) => { setJsonText(e.target.value); setJsonError(""); }}
                      />
                      {jsonError && <p className="neg">{jsonError}</p>}
                      <button
                        className="primary"
                        style={{ marginTop: 8 }}
                        disabled={!jsonText.trim() || saveCreds.isPending}
                        onClick={() => {
                          try {
                            const parsed = JSON.parse(jsonText);
                            const block = parsed.installed ?? parsed.web;
                            if (!block?.client_id || !block?.client_secret) {
                              setJsonError("Not a valid Google OAuth JSON key — missing client_id or client_secret.");
                              return;
                            }
                            saveCreds.mutate(
                              { client_id: block.client_id, client_secret: block.client_secret },
                              { onSuccess: () => { window.location.href = "/api/google/auth"; } }
                            );
                          } catch {
                            setJsonError("Invalid JSON — paste the full file contents.");
                          }
                        }}>
                        Connect Google
                      </button>
                    </>
                  ) : (
                    <>
                      <p className="muted" style={{ fontSize: 13 }}>
                        Web application type — add redirect URI exactly:{" "}
                        <code>{google.data.redirect_uri}</code>
                      </p>
                      <div className="row">
                        <input placeholder="Client ID" value={creds.id} className="grow"
                               onChange={(e) => setCreds((f) => ({ ...f, id: e.target.value }))} />
                        <input placeholder="Client secret" type="password" value={creds.secret}
                               style={{ width: 180 }}
                               onChange={(e) => setCreds((f) => ({ ...f, secret: e.target.value }))} />
                        <button className="primary"
                                disabled={!creds.id.trim() || !creds.secret.trim() || saveCreds.isPending}
                                onClick={() => saveCreds.mutate({ client_id: creds.id, client_secret: creds.secret })}>
                          Save
                        </button>
                      </div>
                      {saveCreds.isSuccess && (
                        <a href="/api/google/auth">
                          <button className="primary" style={{ marginTop: 8 }}>Connect Google</button>
                        </a>
                      )}
                    </>
                  )}
                  {saveCreds.error && <p className="neg">{(saveCreds.error as Error).message}</p>}
                </>
              )
              : google.data.connected ? (
                <>
                  {google.data.scope_version === "legacy" && (
                    <p className="neg" style={{ marginBottom: 8 }}>
                      Connected with full Drive access.{" "}
                      <a href="/api/google/auth">Reconnect</a> to switch to sandboxed access (recommended).
                    </p>
                  )}
                  <div className="row" style={{ alignItems: "center", gap: 8, marginBottom: 6 }}>
                    <span className="muted" style={{ fontSize: 13 }}>
                      {google.data.last_synced_at
                        ? `Up to date · ${google.data.pending} pending`
                        : google.data.pending > 0
                          ? `Never synced · ${google.data.pending} pending`
                          : `Up to date · 0 pending`}
                    </span>
                    <button className="ghost" disabled={resyncNow.isPending}
                            onClick={() => resyncNow.mutate()}>
                      {resyncNow.isPending ? "Re-syncing…" : "Re-sync now"}
                    </button>
                    <span style={{ position: "relative" }}>
                      <span style={{ cursor: "help", fontWeight: "bold", fontSize: 13 }}
                            onMouseEnter={() => setShowResyncTooltip(true)}
                            onMouseLeave={() => setShowResyncTooltip(false)}>?</span>
                      {showResyncTooltip && (
                        <div className="card" style={{
                          position: "absolute", top: 24, left: 0, zIndex: 10,
                          width: 300, padding: 12, fontSize: 13 }}>
                          <p style={{ margin: "0 0 6px" }}>
                            <b>Re-sync now</b> — Clears and rewrites the <b>active profile's</b> sheet
                            from scratch in date order.
                          </p>
                          <p style={{ margin: 0 }}>
                            Use this to restore deleted rows, fix row ordering, or recover
                            after manual sheet edits. Other profiles are not affected.
                          </p>
                        </div>
                      )}
                    </span>
                  </div>
                  {resyncError && <p className="neg" style={{ fontSize: 13 }}>{resyncError}</p>}
                  {syncNowError && <p className="neg" style={{ fontSize: 13 }}>{syncNowError}</p>}
                  {google.data.last_error && (
                    <p className="neg" style={{ fontSize: 13, marginBottom: 6 }}>
                      Sync error: {google.data.last_error}
                    </p>
                  )}
                  <table style={{ fontSize: 13, borderCollapse: "collapse", marginBottom: 10 }}>
                    <tbody>
                      {google.data.profiles.map(p => (
                        <tr key={p.id}>
                          <td style={{ paddingRight: 14, paddingBottom: 3 }}>{p.name}</td>
                          <td style={{ paddingRight: 14, paddingBottom: 3 }}>
                            {p.sheet_url
                              ? <a href={p.sheet_url} target="_blank" rel="noreferrer">Sheet ↗</a>
                              : <span className="muted">No sheet</span>}
                          </td>
                          <td style={{ paddingRight: 14, paddingBottom: 3 }}>
                            {p.drive_folder_url
                              ? <a href={p.drive_folder_url} target="_blank" rel="noreferrer">Drive ↗</a>
                              : <span className="muted">No folder</span>}
                          </td>
                          <td style={{ paddingRight: 14, paddingBottom: 3 }}>
                            {p.pending > 0
                              ? <span style={{ color: "var(--amber, #d97706)", fontWeight: 600 }}>{p.pending} pending</span>
                              : <span className="muted" style={{ fontSize: 12 }}>0 pending</span>}
                          </td>
                          <td style={{ paddingBottom: 3 }}>
                            {p.sheet_in_drive
                              ? <span style={{ color: "var(--pos)" }}>✓ organized</span>
                              : p.sheet_url
                                ? <button className="ghost danger" style={{ fontSize: 12, padding: "2px 6px" }}
                                          disabled={resetSheet.isPending}
                                          onClick={() => {
                                            if (window.confirm(`Reset sheet for '${p.name}'? A NEW sheet is created; the old one stays in Drive but is no longer updated.`))
                                              resetSheet.mutate(p.id);
                                          }}
                                          title="Sheet exists but not in Drive folder. Reset to let next sync create a fresh one inside the folder.">
                                    Reset sheet
                                  </button>
                                : <span className="muted">—</span>}
                          </td>
                          {p.sync_error && (
                            <td style={{ color: "var(--neg)", fontSize: 12, paddingBottom: 3 }}>
                              {p.sync_error}
                            </td>
                          )}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {resetSheetError && <p className="neg" style={{ fontSize: 13 }}>{resetSheetError}</p>}
                  <p className="muted" style={{ fontSize: 13 }}>
                    App only accesses files it creates — your other Drive files are untouched.
                    Each profile gets its own sheet and Drive folder.
                  </p>
                  <div className="row" style={{ marginTop: 10, alignItems: "center" }}>
                    <b>App folder name</b>
                    <input
                      placeholder={google.data.folder_name ?? "Expense Manager"}
                      value={folderName}
                      style={{ width: 220, marginLeft: 8 }}
                      onChange={(e) => setFolderName(e.target.value)}
                    />
                    <button className="ghost"
                            disabled={!folderName.trim() || saveFolderName.isPending}
                            onClick={() => {
                              const current = google.data?.folder_name ?? "Expense Manager";
                              if (folderName.trim() === current) {
                                saveFolderName.mutate(folderName);
                                return;
                              }
                              if (window.confirm("Change the app folder name? Existing profile Drive folders are detached and recreated under the new name. Receipt links already written into existing sheet rows will keep pointing at the OLD Drive folder — they are not rewritten."))
                                saveFolderName.mutate(folderName);
                            }}>
                      Save
                    </button>
                    {saveFolderNameSuccess && (
                      <span style={{ color: "var(--green)", fontSize: 12, fontWeight: 600 }}>Saved ✓</span>
                    )}
                  </div>
                  {saveFolderNameError && <p className="neg" style={{ fontSize: 13 }}>{saveFolderNameError}</p>}
                  <p className="muted" style={{ fontSize: 12, marginTop: 4 }}>
                    Drive layout: <i>{google.data.folder_name ?? "Expense Manager"} / Profile / 2026 / receipts</i>. Sheet lives inside the profile folder.
                  </p>
                  <p className="muted" style={{ marginTop: 12, marginBottom: 4 }}>
                    <span style={{ cursor: "pointer", textDecoration: "underline" }}
                          onClick={() => {
                            const next = !showColumnPreview;
                            setShowColumnPreview(next);
                            if (next && columnsProfileId == null) {
                              const first = google.data?.profiles?.[0]?.id ?? null;
                              setColumnsProfileId(first);
                            }
                          }}>
                      {showColumnPreview ? "Hide" : "Show"} sheet column layout
                    </span>
                  </p>
                  {showColumnPreview && (() => {
                    if (!columns.data) return <p className="muted">Loading…</p>;
                    const selected = selectedCols ?? columns.data.selected;
                    const available = columns.data.available;
                    const labelFor = (k: string) =>
                      available.find((a) => a.key === k)?.label ?? k;
                    const toggle = (k: string) => {
                      if (k === "id") return;
                      setSelectedCols(
                        selected.includes(k)
                          ? selected.filter((c) => c !== k)
                          : [...selected, k]);
                    };
                    const move = (i: number, dir: -1 | 1) => {
                      const j = i + dir;
                      if (i <= 0 || j <= 0 || j >= selected.length) return;
                      const next = [...selected];
                      [next[i], next[j]] = [next[j], next[i]];
                      setSelectedCols(next);
                    };
                    const unselected = available
                      .map((a) => a.key)
                      .filter((k) => !selected.includes(k));
                    return (
                      <div style={{ marginBottom: 10 }}>
                        {google.data!.profiles.length > 1 && (
                          <div className="row" style={{ marginBottom: 8, gap: 6 }}>
                            <span className="muted" style={{ fontSize: 13 }}>Profile</span>
                            <select value={columnsProfileId ?? ""}
                                    onChange={(e) => {
                                      setColumnsProfileId(Number(e.target.value));
                                      setSelectedCols(null);
                                    }}>
                              {google.data!.profiles.map((p) => (
                                <option key={p.id} value={p.id}>{p.name}</option>
                              ))}
                            </select>
                          </div>
                        )}
                        <p className="muted" style={{ fontSize: 12, marginBottom: 6 }}>
                          Selected columns (top → left in the sheet). The ID column is
                          required and always first. Use ↑ ↓ to reorder.
                        </p>
                        <ol style={{ listStyle: "none", padding: 0, margin: 0,
                                     fontSize: 13 }}>
                          {selected.map((k, i) => (
                            <li key={k} className="row"
                                style={{ gap: 6, padding: "2px 0", alignItems: "center" }}>
                              <input type="checkbox" checked readOnly={k === "id"}
                                     disabled={k === "id"}
                                     onChange={() => toggle(k)} />
                              <span style={{ minWidth: 140 }}>{labelFor(k)}</span>
                              <button className="ghost" style={{ padding: "0 6px" }}
                                      disabled={i <= 1} onClick={() => move(i, -1)}>↑</button>
                              <button className="ghost" style={{ padding: "0 6px" }}
                                      disabled={i === 0 || i >= selected.length - 1}
                                      onClick={() => move(i, 1)}>↓</button>
                            </li>
                          ))}
                        </ol>
                        {unselected.length > 0 && (
                          <>
                            <p className="muted" style={{ fontSize: 12, margin: "8px 0 4px" }}>
                              Available
                            </p>
                            <div className="row" style={{ flexWrap: "wrap", gap: 8,
                                                          fontSize: 13 }}>
                              {unselected.map((k) => (
                                <label key={k} className="row" style={{ gap: 4 }}>
                                  <input type="checkbox" checked={false}
                                         onChange={() => toggle(k)} />
                                  {labelFor(k)}
                                </label>
                              ))}
                            </div>
                          </>
                        )}
                        <SheetPreview
                          selected={selected}
                          available={available}
                          taxComponents={activeTaxComponents}
                        />
                        <div className="row" style={{ marginTop: 10, gap: 8 }}>
                          <button className="primary"
                                  disabled={saveColumns.isPending}
                                  onClick={() => saveColumns.mutate(selected)}>
                            {saveColumns.isPending ? "Saving…" : "Save layout"}
                          </button>
                          <button className="ghost"
                                  onClick={() => setSelectedCols(null)}>Reset</button>
                          {saveColumnsSuccess && (
                            <span style={{ color: "var(--green)", fontSize: 12, fontWeight: 600 }}>Saved ✓</span>
                          )}
                        </div>
                        {saveColumnsError &&
                          <p className="neg">{saveColumnsError}</p>}
                      </div>
                    );
                  })()}
                  <p className="muted" style={{ marginTop: 8 }}>
                    <a href="/api/google/auth">Reconnect Google</a>
                    {" · "}
                    <span style={{ cursor: "pointer", textDecoration: "underline" }}
                          onClick={() => setShowSetupGuide(g => !g)}>
                      Setup guide
                    </span>
                  </p>
                  {showSetupGuide && (
                    <ol className="muted" style={{ paddingLeft: 18, marginTop: 8, fontSize: 13 }}>
                      <li>Go to <a href="https://console.cloud.google.com/apis/credentials"
                                   target="_blank" rel="noreferrer">Google Cloud Console</a> → create a project</li>
                      <li>APIs &amp; Services → Library → enable <b>Google Drive API</b></li>
                      <li>APIs &amp; Services → Library → enable <b>Google Sheets API</b></li>
                      <li>OAuth consent screen → User type: <b>External</b> → fill name + email → add yourself as test user</li>
                      <li>Credentials → <b>Create credentials</b> → OAuth 2.0 Client ID → <b>Desktop app</b></li>
                      <li>Download JSON → paste in JSON key tab above (after Reconnect)</li>
                    </ol>
                  )}
                </>
              )
              : <a href="/api/google/auth"><button className="primary">Connect Google</button></a>}
          </Section>
        )}

        {active === "whatsapp" && (
          <Section title="WhatsApp">
            {(addWaError || refreshWaError || removeWaError || addAllowedError || removeAllowedError) && (
              <p className="neg" style={{ marginBottom: 8 }}>
                {addWaError ?? refreshWaError ?? removeWaError ?? addAllowedError ?? removeAllowedError}
              </p>
            )}
            {(whatsapp.data ?? []).map((a) => (
              <div key={a.id} style={{ borderBottom: "1px solid var(--hairline)",
                                       padding: "10px 0" }}>
                <div className="row">
                  <span className="syncdot" data-state={a.status === "connected" ? "ok" : a.status === "qr" ? "pending" : "off"} />
                  <b>{a.device || a.id}</b>
                  <span className="muted">{a.status}</span>
                  <span className="grow" />
                  {(a.status === "qr_expired" || a.status === "disconnected") && (
                    <button className="ghost" disabled={refreshWa.isPending}
                            onClick={() => refreshWa.mutate(a.id)}>
                      {refreshWa.isPending ? "Refreshing…" : "↻ Refresh QR"}</button>)}
                  <button className="ghost danger"
                          onClick={() => {
                            const msg = a.status === "connected"
                              ? "Unpair this WhatsApp account?"
                              : "Remove this account?";
                            if (window.confirm(msg))
                              removeWa.mutate(a.id);
                          }}>
                    {a.status === "connected" ? "Unpair" : "Remove"}</button>
                </div>
                {a.status === "connected" && (
                  <p className="muted" style={{ margin: "6px 0 0 19px" }}>
                    Self-chat is on — open WhatsApp, search your own name and use
                    "Message yourself" to talk to the agent.</p>)}
                {a.qr && (
                  <div style={{ margin: "8px 0 0 19px" }}>
                    <p style={{ margin: "0 0 6px" }}>
                      Scan within 20s: WhatsApp → Settings → Linked devices → Link
                      a device <span className="muted">(use that screen's scanner,
                      not the phone camera)</span></p>
                    <img src={a.qr} alt="WhatsApp QR code — scan within 20 seconds"
                         style={{ width: 220, background: "#fff", padding: 8 }} />
                  </div>)}
                {a.status === "qr_expired" && (
                  <p className="muted" style={{ margin: "6px 0 0 19px" }}>
                    QR expired — refresh to get a new one.</p>)}
              </div>))}
            <button className="primary" style={{ marginTop: 12 }}
                    disabled={addWa.isPending} onClick={() => addWa.mutate()}>
              ＋ Pair another account</button>

            <div style={{ marginTop: 18, borderTop: "1px solid var(--hairline)", paddingTop: 12 }}>
              <b>Allowed senders</b>
              <p className="muted" style={{ margin: "4px 0 8px" }}>
                Other numbers that may talk to the agent by messaging your WhatsApp.
                Everyone else is ignored.</p>
              <div className="row" style={{ flexWrap: "wrap" }}>
                {(allowed.data?.allowed ?? []).map((n) => (
                  <span key={n} className="tag">+{n}{" "}
                    <button className="ghost danger" style={{ padding: 0 }}
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
        )}

        {active === "imports" && (
          <Section title="Import statements & sheets"><ImportReview /></Section>
        )}

        {active === "activity" && (
          <Section title="Activity">
            {(activity.data ?? []).length === 0 &&
              <p className="muted">No activity yet.</p>}
            <table>
              <tbody>
                {(activity.data ?? []).map((a) => (
                  <tr key={a.id}>
                    <td className="muted" style={{ whiteSpace: "nowrap" }}>{a.ts}</td>
                    <td><span className="tag">{a.channel || "—"}</span></td>
                    <td>{a.event}</td>
                    <td className="muted">{a.detail}{a.ref ? ` (#${a.ref})` : ""}</td>
                  </tr>))}
              </tbody>
            </table>
          </Section>
        )}
      </div>
    </div>
  );
}
