import { html, useMemo, useState } from "../html.js";
import { api } from "../api.js";
import { fmt, dayLabel, monthBounds, splitCategoryName, todayISO } from "../format.js";
import { setMonth, invalidate } from "../store.js";
import {
  AmountInput, Chips, EmptyState, MonthNav, Seg, Sheet,
  slotColor, toast, useApi, useStore,
} from "../components/ui.js";

const TYPE_META = {
  income: { sign: "+", cls: "pos" },
  expense: { sign: "−", cls: "neg" },
  transfer: { sign: "", cls: "muted" },
  lend: { sign: "−", cls: "muted" },
  borrow: { sign: "+", cls: "muted" },
  repay_in: { sign: "+", cls: "muted" },
  repay_out: { sign: "−", cls: "muted" },
};

function txTitle(tx) {
  if (tx.type === "transfer") return `${tx.account.name} → ${tx.to_account?.name}`;
  if (tx.person) {
    const verbs = { lend: "Lent to", borrow: "Borrowed from", repay_in: "Repaid by", repay_out: "Repaid to" };
    return `${verbs[tx.type]} ${tx.person.name}`;
  }
  return tx.note || tx.category?.name || tx.type;
}

function txSub(tx) {
  const parts = [];
  if (tx.category && tx.note) parts.push(tx.category.name);
  if (tx.type !== "transfer") parts.push(tx.account.name);
  if (tx.currency !== "INR") parts.push(`${tx.currency} ${tx.amount}`);
  return parts.join(" · ");
}

function txBadge(tx) {
  if (tx.type === "transfer") return { text: "⇄", color: "var(--transfer)" };
  if (tx.person) return { text: tx.person.name[0].toUpperCase(), color: "var(--series-7)" };
  const name = tx.category?.name || "?";
  const slot = tx.category?.color_slot ?? 2;
  const { icon } = splitCategoryName(name);
  return { text: icon || name[0].toUpperCase(), color: slotColor(slot) };
}

export function TxRow({ tx, onClick }) {
  const meta = TYPE_META[tx.type];
  const badge = txBadge(tx);
  return html`
    <button class="txrow" onClick=${onClick}>
      <div class="dot" style="background:${badge.color}">${badge.text}</div>
      <div class="main">
        <div class="title ellipsis">${txTitle(tx)}</div>
        <div class="sub ellipsis">${txSub(tx)}</div>
      </div>
      <div class="amt ${meta.cls} tnum">${meta.sign}${fmt(tx.inr_amount)}</div>
    </button>
  `;
}

/* ---------- Add / Edit sheet ---------- */

export function TxSheet({ tx, onClose, onSaved }) {
  const { accounts, categories } = useStore();
  const isEdit = !!tx?.id;
  const [type, setType] = useState(tx?.type && ["income", "expense", "transfer"].includes(tx.type) ? tx.type : "expense");
  const [amount, setAmount] = useState(tx ? String(tx.amount) : "");
  const [currency, setCurrency] = useState(tx?.currency || "INR");
  const [inrAmount, setInrAmount] = useState(tx && tx.currency !== "INR" ? String(tx.inr_amount) : "");
  const [accountId, setAccountId] = useState(tx?.account?.id ?? accounts[0]?.id ?? null);
  const [toAccountId, setToAccountId] = useState(tx?.to_account?.id ?? null);
  const [categoryId, setCategoryId] = useState(tx?.category?.id ?? null);
  const [date, setDate] = useState(tx?.date || todayISO());
  const [note, setNote] = useState(tx?.note || "");
  const [saving, setSaving] = useState(false);

  const cats = categories.filter((c) => c.kind === type);
  const selectedParent = cats.find(
    (c) => c.id === categoryId || c.children.some((ch) => ch.id === categoryId)
  );

  const yesterday = useMemo(() => {
    const d = new Date();
    d.setDate(d.getDate() - 1);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  }, []);

  const valid =
    Number(amount) > 0 &&
    accountId &&
    (type !== "transfer" || (toAccountId && toAccountId !== accountId)) &&
    (currency === "INR" || Number(inrAmount) > 0);

  async function save() {
    setSaving(true);
    try {
      const payload = {
        type,
        date,
        account_id: accountId,
        to_account_id: type === "transfer" ? toAccountId : null,
        category_id: type === "transfer" ? null : categoryId,
        amount: Number(amount),
        currency,
        inr_amount: currency !== "INR" ? Number(inrAmount) : null,
        note: note || null,
      };
      if (isEdit) await api.updateTransaction(tx.id, payload);
      else await api.createTransaction(payload);
      invalidate();
      toast(isEdit ? "Transaction updated" : "Transaction added");
      onSaved();
    } catch (err) {
      toast(err.message);
    } finally {
      setSaving(false);
    }
  }

  async function remove() {
    setSaving(true);
    try {
      await api.deleteTransaction(tx.id);
      invalidate();
      toast("Transaction deleted");
      onSaved();
    } catch (err) {
      toast(err.message);
      setSaving(false);
    }
  }

  return html`
    <${Sheet} onClose=${onClose}>
      <h2>${isEdit ? "Edit transaction" : "New transaction"}</h2>
      <${Seg}
        options=${[
          { value: "expense", label: "Expense", cls: "exp" },
          { value: "income", label: "Income", cls: "inc" },
          { value: "transfer", label: "Transfer" },
        ]}
        value=${type}
        onChange=${(v) => { setType(v); setCategoryId(null); }}
      />
      <${AmountInput}
        value=${amount} onChange=${setAmount}
        currency=${currency} onCurrency=${setCurrency}
        autofocus=${!isEdit}
      />
      ${currency !== "INR" &&
      html`
        <div class="field">
          <label>Value in INR (manual conversion)</label>
          <input type="text" inputmode="decimal" value=${inrAmount}
            onInput=${(e) => setInrAmount(e.target.value.replace(/[^\d.]/g, ""))} />
        </div>
      `}
      <div class="field">
        <label>${type === "transfer" ? "From account" : "Account"}</label>
        <${Chips}
          items=${accounts}
          value=${accountId}
          onChange=${setAccountId}
        />
      </div>
      ${type === "transfer"
        ? html`
            <div class="field">
              <label>To account</label>
              <${Chips}
                items=${accounts.filter((a) => a.id !== accountId)}
                value=${toAccountId}
                onChange=${setToAccountId}
              />
            </div>
          `
        : html`
            <div class="field">
              <label>Category</label>
              <${Chips} items=${cats} value=${selectedParent?.id === categoryId ? categoryId : selectedParent?.id} allowNone
                onChange=${(id) => setCategoryId(id)} />
              ${selectedParent?.children.length > 0 &&
              html`
                <div style="margin-top:8px">
                  <${Chips}
                    items=${selectedParent.children}
                    value=${categoryId !== selectedParent.id ? categoryId : null}
                    allowNone
                    onChange=${(id) => setCategoryId(id ?? selectedParent.id)}
                  />
                </div>
              `}
            </div>
          `}
      <div class="field">
        <label>Date</label>
        <div class="chips">
          <button class="chip ${date === todayISO() ? "sel" : ""}" onClick=${() => setDate(todayISO())}>Today</button>
          <button class="chip ${date === yesterday ? "sel" : ""}" onClick=${() => setDate(yesterday)}>Yesterday</button>
          <input type="date" value=${date} onInput=${(e) => setDate(e.target.value)}
            style="flex:1; min-width:130px; padding:7px 10px; border:1px solid var(--border); border-radius:999px; background:var(--surface-2)" />
        </div>
      </div>
      <div class="field">
        <label>Note</label>
        <input type="text" value=${note} placeholder="Optional" onInput=${(e) => setNote(e.target.value)} />
      </div>
      <div class="btn-row">
        ${isEdit && html`<button class="btn danger" disabled=${saving} onClick=${remove}>Delete</button>`}
        <button class="btn" disabled=${!valid || saving} onClick=${save}>
          ${isEdit ? "Save changes" : "Save"}
        </button>
      </div>
    <//>
  `;
}

/* ---------- Page ---------- */

export function TransactionsPage() {
  const { month } = useStore();
  const [sheet, setSheet] = useState(null); // null | {} (new) | tx (edit)
  const [refresh, setRefresh] = useState(0);
  const [from, to] = monthBounds(month);

  const { data: txs, loading, error } = useApi(
    () => api.transactions({ from, to, limit: 1000 }),
    [month, refresh]
  );

  const groups = useMemo(() => {
    if (!txs) return [];
    const byDay = new Map();
    for (const tx of txs) {
      if (!byDay.has(tx.date)) byDay.set(tx.date, []);
      byDay.get(tx.date).push(tx);
    }
    return [...byDay.entries()];
  }, [txs]);

  const totals = useMemo(() => {
    let income = 0, expense = 0;
    for (const tx of txs || []) {
      if (tx.type === "income") income += tx.inr_amount;
      if (tx.type === "expense") expense += tx.inr_amount;
    }
    return { income, expense, net: income - expense };
  }, [txs]);

  return html`
    <div class="page">
      <div class="topbar">
        <${MonthNav} month=${month} onChange=${setMonth} />
      </div>
      <div class="sumstrip">
        <div><div class="lab">Income</div><div class="val pos tnum">${fmt(totals.income)}</div></div>
        <div><div class="lab">Expense</div><div class="val neg tnum">${fmt(totals.expense)}</div></div>
        <div><div class="lab">Net</div><div class="val tnum">${fmt(totals.net)}</div></div>
      </div>
      ${error && html`<div class="card neg">${error}</div>`}
      ${!loading && txs?.length === 0 &&
      html`<${EmptyState} icon="🧾" title="No transactions this month"
        sub="Tap + to add one, or tell the chat agent what you spent." />`}
      ${groups.map(([date, list]) => {
        const d = dayLabel(date);
        let dayIncome = 0, dayExpense = 0;
        for (const tx of list) {
          if (tx.type === "income") dayIncome += tx.inr_amount;
          if (tx.type === "expense") dayExpense += tx.inr_amount;
        }
        return html`
          <div key=${date}>
            <div class="dayhead">
              <span class="d">${d.day} · ${d.weekday}</span>
              <span class="t tnum">
                ${dayIncome > 0 && html`<span class="pos">+${fmt(dayIncome)}</span>`}
                ${dayIncome > 0 && dayExpense > 0 && "  "}
                ${dayExpense > 0 && html`<span class="neg">−${fmt(dayExpense)}</span>`}
              </span>
            </div>
            <div class="card txcard">
              ${list.map((tx) => html`<${TxRow} key=${tx.id} tx=${tx} onClick=${() => setSheet(tx)} />`)}
            </div>
          </div>
        `;
      })}
      <button class="fab" onClick=${() => setSheet({})} aria-label="Add transaction">+</button>
      ${sheet !== null &&
      html`<${TxSheet}
        tx=${sheet.id ? sheet : null}
        onClose=${() => setSheet(null)}
        onSaved=${() => { setSheet(null); setRefresh((r) => r + 1); }}
      />`}
    </div>
  `;
}
