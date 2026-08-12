import { html, useEffect, useState } from "../html.js";
import { monthLabel, shiftMonth } from "../format.js";
import { store, subscribe } from "../store.js";

/* ---- re-render a component whenever the shared store changes ---- */
export function useStore() {
  const [, setTick] = useState(0);
  useEffect(() => subscribe(() => setTick((t) => t + 1)), []);
  return store;
}

/* ---- data fetching hook: refetch on mount + when deps change ---- */
export function useApi(fetcher, deps = []) {
  const [state, setState] = useState({ data: null, error: null, loading: true });
  useEffect(() => {
    let alive = true;
    setState((s) => ({ ...s, loading: true }));
    fetcher()
      .then((data) => alive && setState({ data, error: null, loading: false }))
      .catch((error) => alive && setState({ data: null, error: error.message, loading: false }));
    return () => { alive = false; };
  }, deps);
  return state;
}

/* ---- toast singleton (optionally with an action button, e.g. Undo) ---- */
let toastSetter = null;
export function toast(msg, opts = {}) {
  toastSetter?.({ msg, ...opts });
}
export function ToastHost() {
  const [t, setT] = useState(null);
  useEffect(() => {
    toastSetter = setT;
    return () => { toastSetter = null; };
  }, []);
  useEffect(() => {
    if (!t) return;
    const timer = setTimeout(() => setT(null), t.duration ?? 2600);
    return () => clearTimeout(timer);
  }, [t]);
  if (!t) return null;
  return html`
    <div class="toast">
      ${t.msg}
      ${t.actionLabel &&
      html`<button class="toast-action" onClick=${() => { t.onAction?.(); setT(null); }}>
        ${t.actionLabel}
      </button>`}
    </div>
  `;
}

/* ---- bottom sheet ---- */
export function Sheet({ onClose, children }) {
  return html`
    <div class="sheet-backdrop" onClick=${(e) => e.target === e.currentTarget && onClose()}>
      <div class="sheet">
        <div class="grabber"></div>
        ${children}
      </div>
    </div>
  `;
}

/* ---- month navigation ---- */
export function MonthNav({ month, onChange }) {
  return html`
    <div class="monthnav">
      <button onClick=${() => onChange(shiftMonth(month, -1))} aria-label="Previous month">‹</button>
      <div class="label">${monthLabel(month)}</div>
      <button onClick=${() => onChange(shiftMonth(month, 1))} aria-label="Next month">›</button>
    </div>
  `;
}

/* ---- segmented control ---- */
export function Seg({ options, value, onChange }) {
  return html`
    <div class="seg">
      ${options.map(
        (opt) => html`
          <button
            class=${value === opt.value ? `active ${opt.cls || ""}` : ""}
            onClick=${() => onChange(opt.value)}
          >
            ${opt.label}
          </button>
        `
      )}
    </div>
  `;
}

/* ---- chip picker ---- */
export function Chips({ items, value, onChange, allowNone = false }) {
  return html`
    <div class="chips">
      ${items.map(
        (item) => html`
          <button
            class=${`chip ${value === item.id ? "sel" : ""}`}
            onClick=${() => onChange(allowNone && value === item.id ? null : item.id)}
          >
            ${item.name}
          </button>
        `
      )}
    </div>
  `;
}

/* ---- amount input ---- */
export function AmountInput({ value, onChange, currency, onCurrency, autofocus }) {
  return html`
    <div class="amount-wrap">
      <span class="cur">${currency === "INR" ? "₹" : currency}</span>
      <input
        type="text"
        inputmode="decimal"
        placeholder="0"
        value=${value}
        autofocus=${autofocus}
        onInput=${(e) => onChange(e.target.value.replace(/[^\d.]/g, ""))}
      />
      ${onCurrency &&
      html`
        <select value=${currency} onChange=${(e) => onCurrency(e.target.value)}>
          ${["INR", "USD", "EUR", "GBP", "AED", "SGD"].map((c) => html`<option value=${c}>${c}</option>`)}
        </select>
      `}
    </div>
  `;
}

export function EmptyState({ icon, title, sub, children }) {
  return html`
    <div class="empty">
      <div class="big">${icon}</div>
      <div style="font-weight:600; color:var(--ink-2); margin-bottom:4px">${title}</div>
      ${sub && html`<div style="font-size:13.5px">${sub}</div>`}
      ${children}
    </div>
  `;
}

/* ---- category color: fixed slot -> CSS series token ---- */
export function slotColor(slot) {
  return `var(--series-${(slot % 8) + 1})`;
}
