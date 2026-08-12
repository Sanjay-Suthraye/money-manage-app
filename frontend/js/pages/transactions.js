import { html, useEffect, useMemo, useState } from "../html.js";
import { api } from "../api.js";
import { fmt, dayLabel, monthBounds, splitCategoryName, todayISO } from "../format.js";
import { setMonth, invalidate } from "../store.js";
import {
  AmountInput, Chips, EmptyState, MonthNav, Seg, Sheet,
  slotColor, toast, useApi, useStore,
} from "../components/ui.js";
import { RecurringBanner, RecurringSheet } from "./recurring.js";

const TYPE_META = {
  income: { sign: "+", cls: "pos" },
  expense: { sign: "−", cls: "neg" },
  transfer: { sign: "", cls: "muted" },
  lend: { sign: "−", cls: "muted" },
  borrow: { sign: "+", cls: "muted" },
  repay_in: { sign: "+", cls: "muted" },
  repay_out: { sign: "−", cls: "muted" },
};

const LOAN_TYPES = ["lend", "borrow", "repay_in", "repay_out"];
const LOAN_VERBS = { lend: "Lent to", borrow: "Borrowed from", repay_in: "Repaid by", repay_out: "Repaid to" };

function txTitle(tx) {
  if (tx.type === "transfer") return `${tx.account.name} → ${tx.to_account?.name}`;
  if (tx.person) return `${LOAN_VERBS[tx.type]} ${tx.person.name}`;
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
        <div class="title ellipsis">${txTitle(tx)}${tx.split_group_id && html`<span class="split-badge">split</span>`}</div>
        <div class="sub ellipsis">${txSub(tx)}</div>
      </div>
      <div class="amt ${meta.cls} tnum">${meta.sign}${fmt(tx.inr_amount)}</div>
    </button>
  `;
}

/* ---------- Add / Edit sheet ---------- */

export function TxSheet({ tx, onClose, onSaved }) {
  const { accounts, categories, persons } = useStore();
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
  const [split, setSplit] = useState(false);
  const [splitPersonId, setSplitPersonId] = useState(null);
  const [friendAmount, setFriendAmount] = useState("");
  const [makeRecurring, setMakeRecurring] = useState(false);

  const canSplit = type === "expense" && !isEdit && currency === "INR";
  const splitOn = canSplit && split;
  const myShare = splitOn ? Number(amount) - Number(friendAmount || 0) : Number(amount);
  const splitPerson = persons.find((p) => p.id === splitPersonId);

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
    (currency === "INR" || Number(inrAmount) > 0) &&
    (!splitOn || (splitPersonId && Number(friendAmount) > 0 && Number(friendAmount) < Number(amount)));

  async function save() {
    setSaving(true);
    try {
      if (splitOn) {
        await api.createSplit({
          date,
          account_id: accountId,
          category_id: categoryId,
          person_id: splitPersonId,
          my_amount: myShare,
          friend_amount: Number(friendAmount),
          note: note || null,
        });
        invalidate();
        toast(`Split saved — ${fmt(myShare)} expense + ${fmt(Number(friendAmount))} lent`);
        onSaved();
        return;
      }
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
      if (makeRecurring && !isEdit) {
        // Best-effort: the transaction is already saved; a rule failure only toasts.
        const cat = categories.flatMap((c) => [c, ...c.children]).find((c) => c.id === categoryId);
        try {
          await api.createRecurring({
            description: note.trim() || cat?.name || type,
            type,
            account_id: accountId,
            to_account_id: type === "transfer" ? toAccountId : null,
            category_id: type !== "transfer" ? categoryId : null,
            amount: Number(amount),
            day_of_month: Number(date.slice(8, 10)),
          });
          toast("Saved + recurring rule created");
        } catch (err) {
          toast(`Saved, but rule failed: ${err.message}`);
        }
      } else {
        toast(isEdit ? "Transaction updated" : "Transaction added");
      }
      invalidate();
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
      ${canSplit &&
      html`
        <div class="field">
          <button class="chip ${split ? "sel" : ""}" onClick=${() => setSplit(!split)}>
            ⑃ Split with someone
          </button>
          ${splitOn &&
          html`
            <div style="margin-top:10px">
              ${persons.length === 0
                ? html`<div class="muted" style="font-size:13px">Add people on the People tab first.</div>`
                : html`
                    <label>Who owes you?</label>
                    <${Chips} items=${persons} value=${splitPersonId} onChange=${setSplitPersonId} />
                    <label style="margin-top:8px">Their share (of ₹${amount || "…"} total)</label>
                    <input type="text" inputmode="decimal" value=${friendAmount} placeholder="0"
                      onInput=${(e) => setFriendAmount(e.target.value.replace(/[^\d.]/g, ""))} />
                    ${Number(friendAmount) > 0 && Number(friendAmount) < Number(amount) && splitPerson &&
                    html`<div class="muted" style="font-size:12.5px; margin-top:6px">
                      You pay ${fmt(myShare)} · ${splitPerson.name} owes ${fmt(Number(friendAmount))}
                    </div>`}
                  `}
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
      ${!isEdit && !splitOn &&
      html`
        <div class="field">
          <button class="chip ${makeRecurring ? "sel" : ""}" onClick=${() => setMakeRecurring(!makeRecurring)}>
            ↻ Repeat monthly on day ${Number(date.slice(8, 10))}
          </button>
        </div>
      `}
      <div class="btn-row">
        ${isEdit && html`<button class="btn danger" disabled=${saving} onClick=${remove}>Delete</button>`}
        <button class="btn" disabled=${!valid || saving} onClick=${save}>
          ${isEdit ? "Save changes" : "Save"}
        </button>
      </div>
    <//>
  `;
}

/* ---------- Loan edit (amount/account/date/note only — kind + person are fixed) ---------- */

function LoanEditSheet({ tx, onClose, onSaved }) {
  const { accounts } = useStore();
  const [amount, setAmount] = useState(String(tx.amount));
  const [accountId, setAccountId] = useState(tx.account?.id ?? null);
  const [date, setDate] = useState(tx.date);
  const [note, setNote] = useState(tx.note || "");
  const [saving, setSaving] = useState(false);

  async function save() {
    setSaving(true);
    try {
      // Never send type/person_id: the PATCH merge keeps them, so the loan
      // can't be silently converted into an expense.
      await api.updateTransaction(tx.id, {
        amount: Number(amount),
        account_id: accountId,
        date,
        note,
      });
      invalidate();
      toast("Loan entry updated");
      onSaved();
    } catch (err) {
      toast(err.message);
      setSaving(false);
    }
  }

  async function remove() {
    setSaving(true);
    try {
      await api.deleteTransaction(tx.id);
      invalidate();
      toast("Loan entry deleted");
      onSaved();
    } catch (err) {
      toast(err.message);
      setSaving(false);
    }
  }

  return html`
    <${Sheet} onClose=${onClose}>
      <h2>${LOAN_VERBS[tx.type]} ${tx.person?.name}</h2>
      <${AmountInput} value=${amount} onChange=${setAmount} currency="INR" />
      <div class="field" style="margin-top:10px">
        <label>Account</label>
        <${Chips} items=${accounts} value=${accountId} onChange=${setAccountId} />
      </div>
      <div class="field">
        <label>Date</label>
        <input type="date" value=${date} onInput=${(e) => setDate(e.target.value)} />
      </div>
      <div class="field">
        <label>Note</label>
        <input type="text" value=${note} placeholder="Optional" onInput=${(e) => setNote(e.target.value)} />
      </div>
      <div class="btn-row">
        <button class="btn danger" disabled=${saving} onClick=${remove}>Delete</button>
        <button class="btn" disabled=${!(Number(amount) > 0) || !accountId || saving} onClick=${save}>
          Save changes
        </button>
      </div>
    <//>
  `;
}

/* Route edits to the right sheet: loans get their own editor so their kind and
   person can never be clobbered by the income/expense form. */
export function TxEditor({ tx, onClose, onSaved }) {
  if (LOAN_TYPES.includes(tx.type)) {
    return html`<${LoanEditSheet} tx=${tx} onClose=${onClose} onSaved=${onSaved} />`;
  }
  return html`<${TxSheet} tx=${tx} onClose=${onClose} onSaved=${onSaved} />`;
}

/* ---------- Page ---------- */

function FilterSheet({ filters, onChange, onClose }) {
  const { accounts, persons } = useStore();
  return html`
    <${Sheet} onClose=${onClose}>
      <h2>Filters</h2>
      <div class="field">
        <label>Account</label>
        <${Chips} items=${accounts} value=${filters.account_id} allowNone
          onChange=${(id) => onChange({ ...filters, account_id: id })} />
      </div>
      <div class="field">
        <label>Person</label>
        ${persons.length === 0
          ? html`<div class="muted" style="font-size:13px">No people yet.</div>`
          : html`<${Chips} items=${persons} value=${filters.person_id} allowNone
              onChange=${(id) => onChange({ ...filters, person_id: id })} />`}
      </div>
      <div class="btn-row">
        <button class="btn danger" onClick=${() => { onChange({ type: filters.type, account_id: null, person_id: null }); onClose(); }}>Clear</button>
        <button class="btn" onClick=${onClose}>Done</button>
      </div>
    <//>
  `;
}

export function TransactionsPage() {
  const { month } = useStore();
  const [sheet, setSheet] = useState(null); // null | {} (new) | tx (edit)
  const [refresh, setRefresh] = useState(0);
  const [query, setQuery] = useState("");
  const [q, setQ] = useState("");
  const [filters, setFilters] = useState({ type: null, account_id: null, person_id: null });
  const [filterOpen, setFilterOpen] = useState(false);
  const [recurringOpen, setRecurringOpen] = useState(false);
  const [from, to] = monthBounds(month);

  const { data: due } = useApi(() => api.recurringDue(), [refresh]);

  useEffect(() => {
    const t = setTimeout(() => setQ(query.trim()), 300);
    return () => clearTimeout(t);
  }, [query]);

  const active = !!(q || filters.type || filters.account_id || filters.person_id);
  const filterCount = ["account_id", "person_id"].filter((k) => filters[k]).length;

  const { data: txs, loading, error } = useApi(
    () =>
      active
        ? api.transactions({ q, type: filters.type, account_id: filters.account_id, person_id: filters.person_id, limit: 500 })
        : api.transactions({ from, to, limit: 1000 }),
    [month, refresh, q, filters.type, filters.account_id, filters.person_id]
  );

  function clearAll() {
    setQuery("");
    setQ("");
    setFilters({ type: null, account_id: null, person_id: null });
  }

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
      ${!active &&
      html`
        <div class="topbar">
          <${MonthNav} month=${month} onChange=${setMonth} />
          <button class="gearbtn" onClick=${() => setRecurringOpen(true)} aria-label="Recurring transactions">↻</button>
        </div>
      `}
      <div class="searchrow">
        <div class="searchbar">
          <input type="text" placeholder="Search notes, categories, accounts, people"
            value=${query} onInput=${(e) => setQuery(e.target.value)} />
          ${query && html`<button class="clear" onClick=${() => { setQuery(""); setQ(""); }}>×</button>`}
        </div>
        <button class="chip ${filterCount ? "sel" : ""}" onClick=${() => setFilterOpen(true)}>
          Filters${filterCount ? ` · ${filterCount}` : ""}
        </button>
      </div>
      <div class="chips" style="padding:0 16px 8px">
        ${[{ id: "expense", name: "Expense" }, { id: "income", name: "Income" }, { id: "transfer", name: "Transfer" }].map(
          (t) => html`
            <button class="chip ${filters.type === t.id ? "sel" : ""}"
              onClick=${() => setFilters({ ...filters, type: filters.type === t.id ? null : t.id })}>
              ${t.name}
            </button>
          `
        )}
      </div>
      ${active
        ? html`
            <div class="resultline">
              <span class="muted">${txs?.length ?? 0} results · all time</span>
              <button class="chip" onClick=${clearAll}>Clear all</button>
            </div>
          `
        : html`
            <div class="sumstrip">
              <div><div class="lab">Income</div><div class="val pos tnum">${fmt(totals.income)}</div></div>
              <div><div class="lab">Expense</div><div class="val neg tnum">${fmt(totals.expense)}</div></div>
              <div><div class="lab">Net</div><div class="val tnum">${fmt(totals.net)}</div></div>
            </div>
          `}
      ${!active && html`<${RecurringBanner} count=${due?.count || 0} onOpen=${() => setRecurringOpen(true)} />`}
      ${error && html`<div class="card neg">${error}</div>`}
      ${!loading && txs?.length === 0 &&
      html`<${EmptyState} icon=${active ? "🔍" : "🧾"} title=${active ? "No matches" : "No transactions this month"}
        sub=${active ? "Try different words or clear the filters." : "Tap + to add one, or tell the chat agent what you spent."} />`}
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
      (sheet.id
        ? html`<${TxEditor} tx=${sheet}
            onClose=${() => setSheet(null)}
            onSaved=${() => { setSheet(null); setRefresh((r) => r + 1); }} />`
        : html`<${TxSheet} tx=${null}
            onClose=${() => setSheet(null)}
            onSaved=${() => { setSheet(null); setRefresh((r) => r + 1); }} />`)}
      ${filterOpen && html`<${FilterSheet} filters=${filters} onChange=${setFilters} onClose=${() => setFilterOpen(false)} />`}
      ${recurringOpen && html`<${RecurringSheet} onClose=${() => setRecurringOpen(false)} onChanged=${() => setRefresh((r) => r + 1)} />`}
    </div>
  `;
}
