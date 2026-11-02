import { html, useEffect, useState } from "../html.js";
import { friendlyDate, monthLabel, shiftMonth, splitCategoryName } from "../format.js";
import { store, subscribe } from "../store.js";
import { Icon, P, categoryGlyph } from "../icons.js";

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
    <div class="toast" role="status">
      ${t.msg}
      ${t.actionLabel &&
      html`<button class="toast-action" onClick=${() => { t.onAction?.(); setT(null); }}>
        ${t.actionLabel}
      </button>`}
    </div>
  `;
}

/* ---- bottom sheet ----
   With `title` it renders the design's header row (title + close button);
   sheets with a custom header pass none and lay out their own. */
export function Sheet({ title, onClose, children }) {
  return html`
    <div class="sheet-backdrop" onClick=${(e) => e.target === e.currentTarget && onClose()}>
      <div class="sheet" role="dialog" aria-modal="true" aria-label=${title || null}>
        <div class="grabber"></div>
        ${title &&
        html`<div class="sheet-head">
          <h2 class="ellipsis">${title}</h2>
          <button class="iconbtn well" onClick=${onClose} aria-label="Close"><${Icon} d=${P.close} size=${18} sw=${2.2} /></button>
        </div>`}
        ${children}
      </div>
    </div>
  `;
}

/* ---- month navigation ---- */
export function MonthNav({ month, onChange }) {
  return html`
    <div class="monthnav">
      <button class="iconbtn plain" onClick=${() => onChange(shiftMonth(month, -1))} aria-label="Previous month">
        <${Icon} d=${P.chevL} sw=${2} />
      </button>
      <div class="label">${monthLabel(month)}</div>
      <button class="iconbtn plain" onClick=${() => onChange(shiftMonth(month, 1))} aria-label="Next month">
        <${Icon} d=${P.chevR} sw=${2} />
      </button>
    </div>
  `;
}

/* ---- segmented control ---- */
export function Seg({ options, value, onChange }) {
  return html`
    <div class="seg" role="group">
      ${options.map(
        (opt) => html`
          <button
            class=${value === opt.value ? "active" : ""}
            aria-pressed=${value === opt.value}
            onClick=${() => onChange(opt.value)}
          >
            ${opt.label}
          </button>
        `
      )}
    </div>
  `;
}

/* ---- chip picker (children render after the items, e.g. a "+ New" chip) ---- */
export function Chips({ items, value, onChange, allowNone = false, children }) {
  return html`
    <div class="chips">
      ${items.map(
        (item) => html`
          <button
            class=${`chip ${value === item.id ? "sel" : ""}`}
            aria-pressed=${value === item.id}
            onClick=${() => onChange(allowNone && value === item.id ? null : item.id)}
          >
            ${item.name}
          </button>
        `
      )}
      ${children}
    </div>
  `;
}

const CURRENCIES = ["INR", "USD", "EUR", "GBP", "AED", "SGD"];

/* ---- amount input ----
   `hero` is the big centered amount that heads the add/loan sheets; the
   default is the compact field used inside other forms. */
export function AmountInput({ value, onChange, currency, onCurrency, autofocus, hero }) {
  const onInput = (e) => onChange(e.target.value.replace(/[^\d.]/g, ""));
  const picker = onCurrency &&
    html`<select aria-label="Currency" value=${currency} onChange=${(e) => onCurrency(e.target.value)}>
      ${CURRENCIES.map((c) => html`<option value=${c}>${c}</option>`)}
    </select>`;
  const symbol = currency === "INR" ? "₹" : currency;
  if (hero) {
    return html`
      <label class="amount-hero">
        <span class="lab">Amount</span>
        <span class="num">
          <span class="cur">${symbol}</span>
          <input type="text" inputmode="decimal" placeholder="0" value=${value} autofocus=${autofocus}
            style=${`width:${Math.max(1, String(value || "").length) + 0.4}ch`} onInput=${onInput} />
        </span>
        ${picker}
      </label>
    `;
  }
  return html`
    <div class="amount-wrap">
      <span class="cur">${symbol}</span>
      <input type="text" inputmode="decimal" placeholder="0" value=${value} autofocus=${autofocus}
        aria-label="Amount" onInput=${onInput} />
      ${picker}
    </div>
  `;
}

/* ---- date field: friendly label over the native picker ---- */
export function DateField({ value, onChange, label = "Date" }) {
  return html`
    <div class="datefield">
      <span>${friendlyDate(value)}</span>
      <${Icon} d=${P.calendar} size=${18} />
      <input
        type="date"
        aria-label=${label}
        value=${value}
        onInput=${(e) => e.target.value && onChange(e.target.value)}
        onClick=${(e) => { try { e.currentTarget.showPicker?.(); } catch { /* not a user gesture */ } }}
      />
    </div>
  `;
}

export function EmptyState({ icon, title, sub, children }) {
  return html`
    <div class="empty">
      <div class="big">${icon}</div>
      <div class="ttl">${title}</div>
      ${sub && html`<div class="sub">${sub}</div>`}
      ${children}
    </div>
  `;
}

/* ---- category color: fixed slot -> CSS series token ---- */
export function slotColor(slot) {
  return `var(--series-${(slot % 8) + 1})`;
}

/* Tinted category tile: the category's own emoji if it has one (Money Manager
   imports do), else a matching line icon, else its initial. */
export function CategoryTile({ name, slot = 0, small }) {
  const { icon, label } = splitCategoryName(name || "?");
  const glyph = icon ? null : categoryGlyph(label);
  return html`
    <span class=${`tile ${icon ? "emoji" : ""}`} style=${`--c:${slotColor(slot)}`} aria-hidden="true">
      ${icon || (glyph ? html`<${Icon} d=${glyph} size=${small ? 15 : 19} />` : (label || "?")[0].toUpperCase())}
    </span>
  `;
}
