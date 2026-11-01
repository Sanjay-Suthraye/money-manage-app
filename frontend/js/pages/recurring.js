import { html, useState } from "../html.js";
import { api } from "../api.js";
import { fmt, shortDate } from "../format.js";
import { invalidate } from "../store.js";
import { AmountInput, Chips, Seg, Sheet, toast, useApi, useStore } from "../components/ui.js";
import { Icon, P } from "../icons.js";

/* Banner shown on Transactions when suggestions are due: "2 recurring due ·
   Rent, Netflix  Review". */
export function RecurringBanner({ items, onOpen }) {
  if (!items.length) return null;
  const names = [...new Set(items.map((i) => i.description))];
  return html`
    <button class="recur-banner" onClick=${onOpen}>
      <${Icon} d=${P.recur} size=${18} />
      <span class="msg ellipsis">${items.length} recurring due · ${names.join(", ")}</span>
      <span class="act">Review</span>
    </button>
  `;
}

function RuleSheet({ rule, onClose, onSaved }) {
  const { accounts, categories } = useStore();
  const isEdit = !!rule?.id;
  const [type, setType] = useState(rule?.type || "expense");
  const [description, setDescription] = useState(rule?.description || "");
  const [amount, setAmount] = useState(rule ? String(rule.amount) : "");
  const [accountId, setAccountId] = useState(rule?.account?.id ?? accounts[0]?.id ?? null);
  const [toAccountId, setToAccountId] = useState(rule?.to_account?.id ?? null);
  const [categoryId, setCategoryId] = useState(rule?.category?.id ?? null);
  const [day, setDay] = useState(rule ? String(rule.day_of_month) : "1");
  const [saving, setSaving] = useState(false);

  const cats = categories.filter((c) => c.kind === type);
  const valid =
    description.trim() && Number(amount) > 0 && accountId &&
    Number(day) >= 1 && Number(day) <= 31 &&
    (type !== "transfer" || (toAccountId && toAccountId !== accountId));

  async function run(fn, msg) {
    setSaving(true);
    try {
      await fn();
      toast(msg);
      onSaved();
    } catch (err) {
      toast(err.message);
      setSaving(false);
    }
  }

  const payload = () => ({
    description: description.trim(),
    type,
    account_id: accountId,
    to_account_id: type === "transfer" ? toAccountId : null,
    category_id: type !== "transfer" ? categoryId : null,
    amount: Number(amount),
    day_of_month: Number(day),
  });

  const save = () =>
    run(() => (isEdit ? api.updateRecurring(rule.id, payload()) : api.createRecurring(payload())),
        isEdit ? "Rule updated" : "Recurring rule added");
  const togglePause = () =>
    run(() => api.updateRecurring(rule.id, { is_paused: !rule.is_paused }),
        rule?.is_paused ? "Rule resumed" : "Rule paused");
  const remove = () => run(() => api.deleteRecurring(rule.id), "Rule deleted");

  return html`
    <${Sheet} title=${isEdit ? "Edit rule" : "New recurring rule"} onClose=${onClose}>
      <${Seg}
        options=${[
          { value: "expense", label: "Expense" },
          { value: "income", label: "Income" },
          { value: "transfer", label: "Transfer" },
        ]}
        value=${type}
        onChange=${(v) => { setType(v); setCategoryId(null); }}
      />
      <div class="field">
        <label>Description</label>
        <input type="text" value=${description} placeholder="e.g. Rent"
          onInput=${(e) => setDescription(e.target.value)} />
      </div>
      <div class="field">
        <label>Amount</label>
        <${AmountInput} value=${amount} onChange=${setAmount} currency="INR" />
      </div>
      <div class="field">
        <label>${type === "transfer" ? "From account" : "Account"}</label>
        <${Chips} items=${accounts} value=${accountId} onChange=${setAccountId} />
      </div>
      ${type === "transfer"
        ? html`
            <div class="field">
              <label>To account</label>
              <${Chips} items=${accounts.filter((a) => a.id !== accountId)} value=${toAccountId} onChange=${setToAccountId} />
            </div>
          `
        : html`
            <div class="field">
              <label>Category</label>
              <${Chips} items=${cats} value=${categoryId} onChange=${setCategoryId} allowNone />
            </div>
          `}
      <div class="field">
        <label>Day of month</label>
        <input type="number" min="1" max="31" value=${day}
          onInput=${(e) => setDay(e.target.value.replace(/[^\d]/g, ""))} />
      </div>
      <div class="btn-row">
        ${isEdit && html`<button class="btn danger" disabled=${saving} onClick=${remove}>Delete</button>`}
        <button class="btn" disabled=${!valid || saving} onClick=${save}>${isEdit ? "Save changes" : "Add rule"}</button>
      </div>
      ${isEdit &&
      html`<button class="chip" style="margin-top:12px" disabled=${saving} onClick=${togglePause}>
        ${rule.is_paused ? "▶ Resume" : "⏸ Pause"}
      </button>`}
    <//>
  `;
}

export function RecurringSheet({ onClose, onChanged }) {
  const [refresh, setRefresh] = useState(0);
  const [editing, setEditing] = useState(null); // null | {} (new) | rule
  const { data: due } = useApi(() => api.recurringDue(), [refresh]);
  const { data: all } = useApi(() => api.recurring(), [refresh]);

  function bump() {
    setRefresh((r) => r + 1);
    onChanged?.();
  }

  async function act(fn, msg) {
    try {
      await fn();
      invalidate();
      toast(msg);
      bump();
    } catch (err) {
      toast(err.message);
    }
  }

  return html`
    <${Sheet} title="Recurring" onClose=${onClose}>
      ${due?.items?.length > 0 &&
      html`
        <div class="field">
          <div class="field-label">Due</div>
          <div class="list-group">
          ${due.items.map(
            (item) => html`
              <div class="recur-row" key="${item.rule_id}-${item.due_date}">
                <div class="grow">
                  <div class="ttl">${item.description}</div>
                  <div class="sub">${fmt(item.amount)} · ${item.account?.name} · ${shortDate(item.due_date)}</div>
                </div>
                <button class="chip sm" onClick=${() => act(() => api.skipRecurring(item.rule_id), "Skipped")}>Skip</button>
                <button class="chip sm sel" onClick=${() => act(() => api.confirmRecurring(item.rule_id, item.due_date), `Added ${fmt(item.amount)} · ${item.description}`)}>Confirm</button>
              </div>
            `
          )}
          </div>
        </div>
      `}
      <div class="field">
        <div class="spread" style="margin-bottom:8px">
          <div class="field-label" style="margin:0">All rules</div>
          <button class="linkbtn" onClick=${() => setEditing({})}>+ New rule</button>
        </div>
        ${all?.rules?.length > 0 &&
        html`<div class="list-group">
          ${all.rules.map(
            (r) => html`
              <button class="recur-row" key=${r.id} onClick=${() => setEditing(r)}>
                <div class="grow">
                  <div class="ttl">${r.description}${r.is_paused && html`<span class="muted"> · paused</span>`}</div>
                  <div class="sub">${fmt(r.amount)} · day ${r.day_of_month} monthly · next ${shortDate(r.next_due)}</div>
                </div>
                <${Icon} d=${P.chevR} size=${16} sw=${2} />
              </button>
            `
          )}
        </div>`}
        ${all?.rules?.length === 0 && html`<div class="muted" style="padding:10px 0; font-size:13px">
          No rules yet — rent, SIPs, subscriptions live here.
        </div>`}
      </div>
      ${editing !== null &&
      html`<${RuleSheet} rule=${editing.id ? editing : null}
        onClose=${() => setEditing(null)}
        onSaved=${() => { setEditing(null); bump(); }} />`}
    <//>
  `;
}
