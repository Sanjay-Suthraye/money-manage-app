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
    let info = null;
    try {
      const data = await res.json();
      if (typeof data.detail === "string") {
        detail = data.detail;
      } else if (data.detail) {
        /* Structured errors (402 out_of_credits, 429 goodwill_rate_limited)
           carry a message plus context the UI needs. Flattening them to a JSON
           blob in the toast, as this used to, made them unusable. */
        info = data.detail;
        detail = info.message || JSON.stringify(info);
      }
    } catch { /* keep statusText */ }
    const err = new Error(detail);
    err.status = res.status;
    err.info = info;
    throw err;
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

  /* Quick add: parse is a preview and never writes; confirm commits through the
     same validated path as the normal transaction form. */
  quickAddParse: (text) => request("/quickadd/parse", { method: "POST", body: { text } }),
  quickAddConfirm: (body) => request("/quickadd/confirm", { method: "POST", body }),

  credits: () => request("/credits"),
  creditsGoodwill: () => request("/credits/goodwill", { method: "POST", body: {} }),

  chatReset: () => request("/chat/reset", { method: "POST" }),
  chatHistory: () => request("/chat/history"),
  chatStop: () => request("/chat/stop", { method: "POST", body: {} }),
  conversations: () => request("/chat/conversations"),
  conversationMessages: (id) => request(`/chat/conversations/${id}/messages`),
  deleteConversation: (id) => request(`/chat/conversations/${id}`, { method: "DELETE" }),

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
      let info = null;
      try {
        const data = await res.json();
        if (typeof data.detail === "string") detail = data.detail;
        else if (data.detail) { info = data.detail; detail = info.message || detail; }
      } catch { /* ignore */ }
      settled = true;
      /* info is how the chat renders a calm "out of AI entries" card instead of
         a red error — see pages/chat.js onError. */
      onError?.(detail, info);
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
        else if (event === "error") {
          settled = true;
          /* An in-stream error may also be the structured out-of-credits body
             (a race against another tab), so pass it through the same way. */
          onError?.(payload.message || payload.error, payload.error ? payload : null);
        }
      }
    }
    // Stream ended without a done/error frame (connection drop, server restart).
    if (!settled) onError?.("Connection lost — the reply may be incomplete.");
  } catch (err) {
    if (err.name !== "AbortError" && !settled) onError?.(err.message);
  }
}
