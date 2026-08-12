/* Delayed transaction delete with an Undo window — no schema changes.
   The row is hidden optimistically; the DELETE fires after 6s unless undone.
   Backgrounding/closing the app flushes pending deletes with keepalive. */

import { api } from "./api.js";
import { invalidate } from "./store.js";
import { toast } from "./components/ui.js";

const hidden = new Set(); // tx ids removed from lists optimistically
const timers = new Map(); // id -> timeout
const listeners = new Set();

const emit = () => listeners.forEach((fn) => fn());

export function subscribePending(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export const isHidden = (id) => hidden.has(id);

export function scheduleDelete(id) {
  hidden.add(id);
  timers.set(id, setTimeout(() => commit(id), 6000));
  emit();
}

export function undoDelete(id) {
  clearTimeout(timers.get(id));
  timers.delete(id);
  hidden.delete(id);
  emit();
}

async function commit(id) {
  timers.delete(id);
  try {
    await api.deleteTransaction(id);
  } catch (err) {
    toast(`Delete failed: ${err.message}`);
    undoDelete(id);
    return;
  }
  invalidate();
  // Late unhide: by now every refetch has dropped the row, so this is a visual
  // no-op — and it bounds SQLite id-reuse from hiding a future row forever.
  setTimeout(() => { hidden.delete(id); emit(); }, 10000);
}

/* App going to background/closing: fire pending deletes immediately.
   (sendBeacon is POST-only; keepalive DELETE carries the session cookie.) */
function flushAll() {
  for (const [id, t] of timers) {
    clearTimeout(t);
    fetch(`/api/transactions/${id}`, { method: "DELETE", keepalive: true });
  }
  timers.clear();
}
window.addEventListener("pagehide", flushAll);
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "hidden") flushAll();
});
