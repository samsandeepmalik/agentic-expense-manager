// API client — the frontend holds no business logic; everything comes from
// the backend API. This module is transport only.

export interface Category {
  name: string;
  type: "income" | "expense";
  percent: number;
}

export interface Transaction {
  date: string;
  type: string;
  category: string;
  description: string;
  merchant: string;
  amount: number;
  gst: number;
  qst: number;
  total: number;
  counted: number;
  image_link: string;
  source: string;
  recorded_at: string;
}

export interface Summary {
  income: number;
  expenses: number;
  net: number;
  by_category: Record<string, number>;
  trend: { month: string; income: number; expenses: number }[];
  count: number;
}

export interface DashboardData {
  summary: Summary;
  recent: Transaction[];
  sheet_url: string | null;
}

export interface UiComponentSpec {
  type: "metric" | "barChart" | "lineChart" | "pieChart" | "table";
  title?: string;
  label?: string;
  value?: number | string;
  unit?: string;
  data?: Record<string, unknown>[];
  xKey?: string;
  series?: string[];
  columns?: string[];
  rows?: unknown[][];
}

export interface UiSpec {
  title?: string;
  components: UiComponentSpec[];
}

export type ChatEvent =
  | { type: "session"; session_id: string }
  | { type: "status"; text: string }
  | { type: "delta"; text: string }
  | { type: "tool"; name: string; status: "start" | "end"; is_error?: boolean }
  | { type: "ui"; spec: UiSpec }
  | { type: "done"; text: string; error: string | null };

export async function getJson<T>(url: string): Promise<T> {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${url}: ${response.status}`);
  return response.json();
}

export async function postJson<T>(url: string, body: unknown): Promise<T> {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(`${url}: ${response.status}`);
  return response.json();
}

export async function deleteJson<T>(url: string): Promise<T> {
  const response = await fetch(url, { method: "DELETE" });
  if (!response.ok) throw new Error(`${url}: ${response.status}`);
  return response.json();
}

// POST + SSE-over-fetch (EventSource is GET-only)
export async function streamChat(
  message: string,
  sessionId: string,
  image: File | null,
  onEvent: (event: ChatEvent) => void,
): Promise<void> {
  const form = new FormData();
  form.set("message", message);
  form.set("session_id", sessionId);
  if (image) form.set("image", image);

  const response = await fetch("/api/chat", { method: "POST", body: form });
  if (!response.ok || !response.body) {
    throw new Error(`chat failed: ${response.status}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let separatorIndex: number;
    while ((separatorIndex = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, separatorIndex);
      buffer = buffer.slice(separatorIndex + 2);
      for (const line of frame.split("\n")) {
        if (line.startsWith("data: ")) {
          onEvent(JSON.parse(line.slice(6)) as ChatEvent);
        }
      }
    }
  }
}
