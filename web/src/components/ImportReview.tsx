import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { post, upload, type ImportRecord } from "../api";

export function ImportReview() {
  const queryClient = useQueryClient();
  const [record, setRecord] = useState<ImportRecord | null>(null);
  const [skips, setSkips] = useState<Set<number>>(new Set());
  const [error, setError] = useState("");

  const uploadFile = useMutation({
    mutationFn: (file: File) => {
      const form = new FormData(); form.set("file", file);
      return upload<ImportRecord>("/api/imports", form);
    },
    onSuccess: (r) => {
      setRecord(r);
      setSkips(new Set(r.rows.map((row, i) => row.skip ? i : -1).filter((i) => i >= 0)));
      if (r.status === "failed") setError(r.error ?? "Parse failed");
    },
    onError: (e: Error) => setError(e.message) });

  const approve = useMutation({
    mutationFn: () => post(`/api/imports/${record!.id}/approve`, {
      indexes: record!.rows.map((_, i) => i).filter((i) => !skips.has(i)) }),
    onSuccess: () => {
      setRecord(null);
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      queryClient.invalidateQueries({ queryKey: ["transactions"] });
    } });

  const toggleSkip = (index: number) => setSkips((s) => {
    const next = new Set(s); next.has(index) ? next.delete(index) : next.add(index);
    return next; });

  return (
    <div>
      <p className="muted">Upload a bank statement or sheet (CSV, XLSX, PDF).
        The agent parses and categorizes; likely duplicates are pre-skipped.</p>
      <input type="file" accept=".csv,.xlsx,.xls,.pdf"
             onChange={(e) => e.target.files?.[0] && uploadFile.mutate(e.target.files[0])} />
      {uploadFile.isPending && <p>Parsing with the agent…</p>}
      {error && <p style={{ color: "var(--amber)" }}>{error}</p>}
      {record?.status === "review" && (
        <>
          <table style={{ marginTop: 12 }}>
            <thead><tr><th>Keep</th><th>Date</th><th>Type</th><th>Category</th>
                       <th>Merchant</th><th>Total</th><th></th><th></th></tr></thead>
            <tbody>
              {record.rows.map((row, index) => (
                <tr key={index} style={{ opacity: skips.has(index) ? 0.45 : 1 }}>
                  <td><input type="checkbox" checked={!skips.has(index)}
                             onChange={() => toggleSkip(index)} /></td>
                  <td>{row.date}</td>
                  <td><span className={`tag ${row.type}`}>{row.type}</span></td>
                  <td>{row.category}</td><td>{row.merchant}</td>
                  <td>${row.total.toFixed(2)}</td>
                  <td>{row.receipt_link &&
                    <a href={row.receipt_link} target="_blank" rel="noreferrer">📎</a>}</td>
                  <td>{row.duplicate &&
                    <span style={{ color: "var(--amber)" }}>possible duplicate</span>}</td>
                </tr>))}
            </tbody>
          </table>
          <button className="primary" style={{ marginTop: 12 }}
                  disabled={approve.isPending} onClick={() => approve.mutate()}>
            Approve {record.rows.length - skips.size} rows</button>
        </>)}
    </div>
  );
}
