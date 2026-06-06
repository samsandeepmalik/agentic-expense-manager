export interface Category { id: number; name: string; type: "income" | "expense";
  percent: number; taxable: boolean; budget_monthly: number | null; }
export interface Txn { id: number; date: string; type: string; category: string;
  description: string; merchant: string; amount: number;
  tax_breakdown: Record<string, number>; total: number; counted: number;
  image_path: string | null; source: string; sync_status: string;
  loan: boolean; receipt_link: string | null; }
export interface Budget { name: string; budget: number; spent: number; pct: number; }
export interface Dashboard {
  period: { start: string; end: string };
  metrics: { income: number; expenses: number; net: number; count: number };
  by_category: Record<string, number>;
  trend: { month: string; income: number; expenses: number }[];
  budgets: Budget[]; recent: Txn[];
}
export interface ChatSession { id: string; title: string; updated_at: string; }
export interface TaxProfile { id: number; name: string; is_active: boolean;
  components: { name: string; rate: number }[]; }
export interface RecurringRule { id: number; template: Record<string, unknown>;
  frequency: string; next_run: string; active: boolean; }
export interface WaAccount { id: string; device: string; status: string;
  qr: string | null; }
export interface AuditRow { id: number; ts: string; channel: string;
  event: string; ref: string; detail: string; }
export interface ImportRecord { id: number; filename: string; status: string;
  error: string | null;
  rows: { date: string; type: string; category: string; merchant: string;
          description: string; total: number; duplicate: boolean; skip: boolean;
          receipt_link?: string | null; }[]; }
export interface UiComponentSpec { type: string; title?: string; label?: string;
  value?: number | string; unit?: string; data?: Record<string, unknown>[];
  xKey?: string; series?: string[]; columns?: string[]; rows?: unknown[][]; }
export interface UiSpec { title?: string; components: UiComponentSpec[]; }
export type ChatEvent =
  | { type: "status"; text: string } | { type: "delta"; text: string }
  | { type: "tool"; name: string; status: string }
  | { type: "ui"; spec: UiSpec }
  | { type: "done"; text: string; error: string | null };

async function handle<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.error?.message ?? `Request failed (${response.status})`);
  }
  return response.json();
}
export const get = <T,>(url: string) => fetch(url).then((r) => handle<T>(r));
export const post = <T,>(url: string, body?: unknown) =>
  fetch(url, { method: "POST", headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body) }).then((r) => handle<T>(r));
export const patch = <T,>(url: string, body: unknown) =>
  fetch(url, { method: "PATCH", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body) }).then((r) => handle<T>(r));
export const del = <T,>(url: string) =>
  fetch(url, { method: "DELETE" }).then((r) => handle<T>(r));
export const upload = <T,>(url: string, form: FormData) =>
  fetch(url, { method: "POST", body: form }).then((r) => handle<T>(r));

export async function streamChat(sessionId: string, message: string,
    image: File | null, onEvent: (e: ChatEvent) => void): Promise<void> {
  const form = new FormData();
  form.set("message", message);
  if (image) form.set("image", image);
  const response = await fetch(`/api/chat/sessions/${sessionId}/messages`,
    { method: "POST", body: form });
  if (!response.ok || !response.body) throw new Error(`chat failed (${response.status})`);
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let cut: number;
    while ((cut = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      for (const line of frame.split("\n"))
        if (line.startsWith("data: ")) onEvent(JSON.parse(line.slice(6)));
    }
  }
}
