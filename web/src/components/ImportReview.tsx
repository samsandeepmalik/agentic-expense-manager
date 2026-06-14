import { Fragment, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { get, post, upload,
         type Category, type ImportRecord, type Profile } from "../api";
import { money } from "../format";
import { CategoryPicker } from "./CategoryPicker";

// Editable copy of a parsed row — the review grid mutates this, approve sends it.
interface EditRow {
  date: string;
  type: "income" | "expense";
  category_id: number | null;
  category: string;          // original parsed name (fallback if no id chosen)
  merchant: string;
  description: string;
  total: number;
  loan: boolean;
  notes: string;
  receipt_link: string | null;
  duplicate: boolean;
}

export function ImportReview() {
  const queryClient = useQueryClient();
  const [record, setRecord] = useState<ImportRecord | null>(null);
  const [rows, setRows] = useState<EditRow[]>([]);
  const [skips, setSkips] = useState<Set<number>>(new Set());
  const [error, setError] = useState("");
  const [okMsg, setOkMsg] = useState("");
  const [fileKey, setFileKey] = useState(0);
  const [targetProfile, setTargetProfile] = useState<number | null>(null);

  const profiles = useQuery({ queryKey: ["profiles"],
    queryFn: () => get<Profile[]>("/api/profiles") });

  // Pre-select the active book; user can override before uploading.
  const profileId = targetProfile ??
    profiles.data?.find((p) => p.active)?.id ?? null;
  const multiProfile = (profiles.data?.length ?? 0) > 1;

  // Categories must come from the TARGET profile (category ids are per-profile);
  // re-fetch whenever the chosen import profile changes.
  const categories = useQuery({ queryKey: ["categories", profileId],
    queryFn: () => get<Category[]>(
      profileId != null ? `/api/categories?profile_id=${profileId}`
                        : "/api/categories") });

  // Map a parsed category name to an id WITHIN the row's type: prefer a
  // top-level match, else a same-type sub-category. Never cross types — the
  // type-filtered picker can't display an opposite-type id, and it would leave
  // the row silently mis-categorised. null = unresolved (user picks; hint shows).
  const nameToId = (name: string, type: string): number | null => {
    const cats = categories.data ?? [];
    const lower = (name ?? "").toLowerCase();
    const sameType = cats.filter((c) => c.type === type &&
                                        c.name.toLowerCase() === lower);
    const top = sameType.find((c) => c.parent_id === 0);
    return (top ?? sameType[0])?.id ?? null;
  };

  const reset = () => {
    setRecord(null);
    setRows([]);
    setSkips(new Set());
    setError("");
    setFileKey((k) => k + 1);
  };

  const uploadFile = useMutation({
    mutationFn: (file: File) => {
      const form = new FormData(); form.set("file", file);
      if (profileId != null) form.set("profile_id", String(profileId));
      return upload<ImportRecord>("/api/imports", form);
    },
    onSuccess: (r) => {
      setRecord(r);
      setRows(r.rows.map((row) => ({
        date: row.date,
        type: (row.type === "income" ? "income" : "expense"),
        category_id: row.category_id ?? nameToId(row.category, row.type),
        category: row.category,
        merchant: row.merchant ?? "",
        description: row.description ?? "",
        total: row.total,
        loan: !!row.loan,
        notes: row.notes ?? "",
        receipt_link: row.receipt_link ?? null,
        duplicate: !!row.duplicate,
      })));
      setSkips(new Set(r.rows.map((row, i) => row.skip ? i : -1).filter((i) => i >= 0)));
      if (r.status === "failed") setError(r.error ?? "Parse failed");
    },
    onError: (e: Error) => setError(e.message) });

  const approve = useMutation({
    mutationFn: () => {
      const edited = rows.map((r, i) => ({
        date: r.date, type: r.type,
        ...(r.category_id != null ? { category_id: r.category_id }
                                  : { category: r.category }),
        merchant: r.merchant, description: r.description,
        total: Number(r.total), loan: r.loan, notes: r.notes,
        receipt_link: r.receipt_link, skip: skips.has(i),
      }));
      return post<{ created: number; failed: { index: number }[] }>(
        `/api/imports/${record!.id}/approve`, {
        indexes: rows.map((_, i) => i).filter((i) => !skips.has(i)),
        rows: edited,
      });
    },
    onSuccess: (res: { created: number; failed: { index: number }[] }) => {
      reset();
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      queryClient.invalidateQueries({ queryKey: ["transactions"] });
      const skipped = res.failed?.length
        ? ` · ${res.failed.length} skipped (unresolved)` : "";
      setError("");
      setOkMsg(`Imported ${res.created} transaction(s)${skipped}.`);
    } });

  const patch = (index: number, fields: Partial<EditRow>) =>
    setRows((rs) => rs.map((r, i) => i === index ? { ...r, ...fields } : r));

  const toggleSkip = (index: number) => setSkips((s) => {
    const next = new Set(s); next.has(index) ? next.delete(index) : next.add(index);
    return next; });

  return (
    <div>
      <p className="muted">Upload a bank statement or sheet (CSV, XLSX, PDF).
        The agent parses and categorizes; likely duplicates are pre-skipped.
        Review and edit every field below before approving.</p>
      {multiProfile && (
        <label style={{ display: "block", marginBottom: 8 }}>
          Import into profile:{" "}
          <select value={profileId ?? ""}
                  onChange={(e) => setTargetProfile(Number(e.target.value))}>
            {profiles.data!.map((p) => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </select>
        </label>
      )}
      <div className="muted" style={{ fontSize: 13, margin: "8px 0",
           padding: "8px 10px", border: "1px solid var(--line, #e3ddd2)",
           borderRadius: 6, background: "var(--bg-soft, #faf8f3)" }}>
        <strong>ⓘ Receipt links in your file</strong>
        <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
          <li>Kept as clickable links on each transaction.</li>
          <li>Auto-copying the image into the app works only for Google Drive
              files this app created. Your own Drive files, Dropbox, or direct
              URLs stay as links only (sandboxed access can't read them).</li>
        </ul>
      </div>
      <input key={fileKey} type="file" accept=".csv,.xlsx,.xls,.pdf"
             onChange={(e) => e.target.files?.[0] && uploadFile.mutate(e.target.files[0])} />
      {uploadFile.isPending && <p>Parsing with the agent…</p>}
      {okMsg && <p className="pos">{okMsg}</p>}
      {error && <p className="neg">{error}</p>}
      {record?.status === "failed" && (
        <div style={{ marginTop: 10 }}>
          <p className="neg">Parse failed — try a different file.</p>
          <button className="ghost" onClick={reset}>Try another file</button>
        </div>
      )}
      {record?.status === "review" && (
        <>
          <table style={{ marginTop: 12, width: "100%" }}>
            <thead><tr>
              <th>Keep</th><th>Date</th><th>Type</th><th>Category ▸ Sub</th>
              <th>Merchant</th><th>Loan</th><th className="num">Total</th>
              <th>📎</th><th></th></tr></thead>
            <tbody>
              {rows.map((row, index) => (
                <Fragment key={index}>
                <tr style={{ opacity: skips.has(index) ? 0.45 : 1 }}>
                  <td><input type="checkbox" checked={!skips.has(index)}
                             onChange={() => toggleSkip(index)} /></td>
                  <td><input type="date" value={row.date}
                             onChange={(e) => patch(index, { date: e.target.value })}
                             style={{ width: 130 }} /></td>
                  <td>
                    <select value={row.type}
                            onChange={(e) => patch(index, {
                              type: e.target.value as "income" | "expense",
                              category_id: null })}>
                      <option value="expense">expense</option>
                      <option value="income">income</option>
                    </select>
                  </td>
                  <td style={{ minWidth: 240 }}>
                    <CategoryPicker categories={categories.data ?? []}
                                    type={row.type} valueId={row.category_id}
                                    onChange={(id) => patch(index, { category_id: id })} />
                    {row.category_id == null &&
                      <span className="neg lbl"> pick a category</span>}
                  </td>
                  <td><input value={row.merchant}
                             onChange={(e) => patch(index, { merchant: e.target.value })}
                             style={{ width: 120 }} /></td>
                  <td style={{ textAlign: "center" }}>
                    <input type="checkbox" checked={row.loan}
                           onChange={(e) => patch(index, { loan: e.target.checked })} /></td>
                  <td className="num"><input type="number" step="0.01" value={row.total}
                             onChange={(e) => patch(index, { total: Number(e.target.value) })}
                             style={{ width: 90, textAlign: "right" }} /></td>
                  <td>{row.receipt_link &&
                    <a href={row.receipt_link} target="_blank" rel="noreferrer">📎</a>}</td>
                  <td>{row.duplicate &&
                    <span className="neg lbl">possible duplicate</span>}</td>
                </tr>
                <tr style={{ opacity: skips.has(index) ? 0.45 : 1 }}>
                  <td></td>
                  <td colSpan={8} style={{ paddingBottom: 8 }}>
                    <input value={row.notes} placeholder="notes…"
                           onChange={(e) => patch(index, { notes: e.target.value })}
                           style={{ width: "90%" }} />
                  </td>
                </tr>
                </Fragment>))}
            </tbody>
          </table>
          <button className="primary" style={{ marginTop: 12 }}
                  disabled={approve.isPending} onClick={() => approve.mutate()}>
            Approve {rows.length - skips.size} rows</button>
        </>)}
    </div>
  );
}
