/* Session expiry: main.js registers a handler that swaps the app for the
   login view. Failed logins themselves (401 on /auth/*) show inline instead. */
let onUnauthorized = null;
export function setUnauthorizedHandler(fn) {
  onUnauthorized = fn;
}

function notifyUnauthorized(res, path = "") {
  if (res.status === 401 && !path.startsWith("/auth/")) onUnauthorized?.();
}

async function request(path, options = {}) {
  const res = await fetch(`/api${path}`, {
    headers: options.body ? { "Content-Type": "application/json" } : {},
    ...options,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  notifyUnauthorized(res, path);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
    } catch { /* keep statusText */ }
    throw new Error(detail);
  }
  if (res.status === 204) return null;
  return res.json();
}

export const api = {
  authMe: () => request("/auth/me"),
  login: (username, password) => request("/auth/login", { method: "POST", body: { username, password } }),
  logout: () => request("/auth/logout", { method: "POST" }),

  accounts: (params = {}) => {
    const qs = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== "")
    );
    return request(`/accounts${qs.size ? `?${qs}` : ""}`);
  },
  createAccount: (data) => request("/accounts", { method: "POST", body: data }),
  updateAccount: (id, patch) => request(`/accounts/${id}`, { method: "PATCH", body: patch }),

  categories: () => request("/categories"),
  createCategory: (data) => request("/categories", { method: "POST", body: data }),
  updateCategory: (id, patch) => request(`/categories/${id}`, { method: "PATCH", body: patch }),
  deleteCategory: (id, reassignTo) =>
    request(`/categories/${id}${reassignTo ? `?reassign_to=${reassignTo}` : ""}`, { method: "DELETE" }),

  transactions: (params = {}) => {
    const qs = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== "")
    );
    return request(`/transactions?${qs}`);
  },
  createTransaction: (data) => request("/transactions", { method: "POST", body: data }),
  updateTransaction: (id, patch) => request(`/transactions/${id}`, { method: "PATCH", body: patch }),
  deleteTransaction: (id) => request(`/transactions/${id}`, { method: "DELETE" }),
  createSplit: (data) => request("/transactions/split", { method: "POST", body: data }),

  persons: () => request("/persons/balances"),
  createPerson: (data) => request("/persons", { method: "POST", body: data }),
  personLedger: (id) => request(`/persons/${id}/ledger`),
  addLoan: (data) => request("/persons/loan", { method: "POST", body: data }),

  budgets: (month) => request(`/budgets${month ? `?month=${month}` : ""}`),
  setBudget: (categoryId, amount) => request(`/budgets/${categoryId}`, { method: "PUT", body: { amount } }),
  deleteBudget: (categoryId) => request(`/budgets/${categoryId}`, { method: "DELETE" }),

  recurring: () => request("/recurring"),
  recurringDue: () => request("/recurring/due"),
  createRecurring: (data) => request("/recurring", { method: "POST", body: data }),
  updateRecurring: (id, patch) => request(`/recurring/${id}`, { method: "PATCH", body: patch }),
  deleteRecurring: (id) => request(`/recurring/${id}`, { method: "DELETE" }),
  confirmRecurring: (id, date) => request(`/recurring/${id}/confirm`, { method: "POST", body: { date } }),
  skipRecurring: (id) => request(`/recurring/${id}/skip`, { method: "POST", body: {} }),

  summaryNetworth: (months = 12) => request(`/summary/networth?months=${months}`),
  summaryMonthly: (months = 12) => request(`/summary/monthly?months=${months}`),
  summaryDays: (month) => request(`/summary/days?month=${month}`),
  summaryCategories: (from, to, kind = "expense") =>
    request(`/summary/categories?from=${from}&to=${to}&kind=${kind}`),

  chatReset: () => request("/chat/reset", { method: "POST" }),
  chatHistory: () => request("/chat/history"),

  uploadChatFile: async (file) => {
    const form = new FormData();
    form.append("file", file);
    const res = await fetch("/api/chat/upload", { method: "POST", body: form });
    notifyUnauthorized(res);
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch { /* ignore */ }
      throw new Error(detail);
    }
    return res.json();
  },
};

/* Chat streaming: POST + ReadableStream SSE parsing (EventSource is GET-only). */
export async function streamChat(message, attachments, { onDelta, onTool, onDone, onError }, signal) {
  let settled = false; // a done/error callback fired — the turn ended properly
  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, attachments: attachments || [] }),
      signal,
    });
    if (!res.ok || !res.body) {
      notifyUnauthorized(res);
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch { /* ignore */ }
      settled = true;
      onError?.(detail);
      return;
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      // sse-starlette uses \r\n line endings; normalize before framing.
      buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
      const frames = buffer.split("\n\n");
      buffer = frames.pop();
      for (const frame of frames) {
        let event = "message";
        let data = "";
        for (const line of frame.split("\n")) {
          if (line.startsWith("event:")) event = line.slice(6).trim();
          else if (line.startsWith("data:")) data += line.slice(5).trim();
        }
        if (!data) continue;
        const payload = JSON.parse(data);
        if (event === "delta") onDelta?.(payload.text);
        else if (event === "tool") onTool?.(payload);
        else if (event === "done") { settled = true; onDone?.(payload); }
        else if (event === "error") { settled = true; onError?.(payload.message); }
      }
    }
    // Stream ended without a done/error frame (connection drop, server restart).
    if (!settled) onError?.("Connection lost — the reply may be incomplete.");
  } catch (err) {
    if (err.name !== "AbortError" && !settled) onError?.(err.message);
  }
}
