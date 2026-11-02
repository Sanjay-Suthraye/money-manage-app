import { html, useEffect, useMemo, useState } from "../html.js";
import { api } from "../api.js";
import { currentMonth, dayHeading, fmt, fmtSigned, monthBounds, monthLabel, todayISO } from "../format.js";
import { setMonth, invalidate } from "../store.js";
import {
  AmountInput, CategoryTile, Chips, DateField, EmptyState, MonthNav, Seg, Sheet,
  toast, useApi, useStore,
} from "../components/ui.js";
import { Icon, P } from "../icons.js";
import { NewCategoryInline } from "./categories.js";
import { RecurringBanner, RecurringSheet } from "./recurring.js";
import { isHidden, scheduleDelete, subscribePending, undoDelete } from "../pendingDelete.js";
import { QuickAdd } from "../components/quickadd.js";

/* Shared delayed-delete flow: hide now, DELETE in 6s unless undone. */
export function deleteWithUndo(txId, onDone) {
  scheduleDelete(txId);
  onDone();
  toast("Transaction deleted", {
    actionLabel: "Undo",
    duration: 6000,
    onAction: () => undoDelete(txId),
  });
}

/* Spending reads in ink with a minus sign; only income is colored. */
const TYPE_META = {
  income: { sign: "+", cls: "pos" },
  expense: { sign: "−", cls: "" },
  transfer: { sign: "", cls: "muted" },
  lend: { sign: "−", cls: "muted" },
  borrow: { sign: "+", cls: "muted" },
  repay_in: { sign: "+", cls: "muted" },
  repay_out: { sign: "−", cls: "muted" },
};

const LOAN_TYPES = ["lend", "borrow", "repay_in", "repay_out"];
const LOAN_VERBS = { lend: "Lent to", borrow: "Borrowed from", repay_in: "Repaid by", repay_out: "Repaid to" };

function txTitle(tx) {
  if (tx.type === "transfer") return tx.note || "Transfer";
  if (tx.person) return `${LOAN_VERBS[tx.type]} ${tx.person.name}`;
  return tx.note || tx.category?.name || tx.type;
}

function txSub(tx) {
  const parts = [];
  if (tx.type === "transfer") {
    parts.push(`${tx.account.name} → ${tx.to_account?.name}`);
  } else {
    if (tx.category && tx.note) parts.push(tx.category.name);
    parts.push(tx.account.name);
  }
  if (tx.currency !== "INR") parts.push(`${tx.currency} ${tx.amount}`);
  return parts.join(" · ");
}

function TxTile({ tx }) {
  if (tx.type === "transfer") {
    return html`<span class="tile neutral" aria-hidden="true"><${Icon} d=${P.transfer} size=${19} /></span>`;
  }
  if (tx.person) {
    return html`<span class="tile" style="--c:var(--series-7)" aria-hidden="true">${tx.person.name[0].toUpperCase()}</span>`;
  }
  // Uncategorized rows fall back to the note, so "Swiggy" still gets a fork.
  return html`<${CategoryTile} name=${tx.category?.name || tx.note || tx.type} slot=${tx.category?.color_slot ?? 7} />`;
}

export function TxRow({ tx, onClick }) {
  const meta = TYPE_META[tx.type];
  return html`
    <button class="txrow" onClick=${onClick}>
      <${TxTile} tx=${tx} />
      <div class="main">
        <div class="title ellipsis">${txTitle(tx)}${tx.split_group_id && html`<span class="split-badge">split</span>`}</div>
        <div class="sub ellipsis">${txSub(tx)}</div>
      </div>
      <div class="amt ${meta.cls} tnum">${meta.sign}${fmt(tx.inr_amount)}</div>
    </button>
  `;
}

/* ---------- Add / Edit sheet ---------- */

const ACCOUNT_LABEL = { expense: "Paid from", income: "Received in", transfer: "From account" };

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
  const [addingCategory, setAddingCategory] = useState(false);
  const [date, setDate] = useState(tx?.date || todayISO());
  const [note, setNote] = useState(tx?.note || "");
  const [saving, setSaving] = useState(false);
  const [splitPersonId, setSplitPersonId] = useState(null);
  const [friendAmount, setFriendAmount] = useState("");
  const [makeRecurring, setMakeRecurring] = useState(false);

  const canSplit = type === "expense" && !isEdit && currency === "INR";
  const splitOn = canSplit && splitPersonId !== null;
  const myShare = splitOn ? Number(amount) - Number(friendAmount || 0) : Number(amount);
  const splitPerson = persons.find((p) => p.id === splitPersonId);

  const cats = categories.filter((c) => c.kind === type);
  const selectedParent = cats.find(
    (c) => c.id === categoryId || c.children.some((ch) => ch.id === categoryId)
  );

  const valid =
    Number(amount) > 0 &&
    accountId &&
    (type !== "transfer" || (toAccountId && toAccountId !== accountId)) &&
    (currency === "INR" || Number(inrAmount) > 0) &&
    (!splitOn || (Number(friendAmount) > 0 && Number(friendAmount) < Number(amount)));

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

  function remove() {
    deleteWithUndo(tx.id, onSaved);
  }

  return html`
    <${Sheet} title=${isEdit ? "Edit transaction" : "New transaction"} onClose=${onClose}>
      <${Seg}
        options=${[
          { value: "expense", label: "Expense" },
          { value: "income", label: "Income" },
          { value: "transfer", label: "Transfer" },
        ]}
        value=${type}
        onChange=${(v) => { setType(v); setCategoryId(null); setAddingCategory(false); }}
      />
      <${AmountInput}
        hero
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
      ${type !== "transfer" &&
      html`
        <div class="field">
          <div class="field-label">Category</div>
          <${Chips} items=${cats} value=${selectedParent?.id === categoryId ? categoryId : selectedParent?.id} allowNone
            onChange=${(id) => setCategoryId(id)}>
            ${!addingCategory &&
            html`<button class="chip add" onClick=${() => setAddingCategory(true)}>
              <${Icon} d=${P.plus} size=${15} sw=${2.2} />New
            </button>`}
          <//>
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
          ${addingCategory &&
          html`<${NewCategoryInline} kind=${type} parent=${selectedParent}
            onCreated=${(cat) => { setCategoryId(cat.id); setAddingCategory(false); }}
            onCancel=${() => setAddingCategory(false)} />`}
        </div>
      `}
      <div class="field">
        <div class="field-label">${ACCOUNT_LABEL[type]}</div>
        <${Chips} items=${accounts} value=${accountId} onChange=${setAccountId} />
      </div>
      ${type === "transfer" &&
      html`
        <div class="field">
          <div class="field-label">To account</div>
          <${Chips}
            items=${accounts.filter((a) => a.id !== accountId)}
            value=${toAccountId}
            onChange=${setToAccountId}
          />
        </div>
      `}
      <div class=${canSplit ? "grid2" : ""}>
        <div class="field">
          <div class="field-label">Date</div>
          <${DateField} value=${date} onChange=${setDate} />
        </div>
        ${canSplit &&
        html`
          <div class="field">
            <label for="split-with">Split with</label>
            <select id="split-with" value=${splitPersonId ?? ""}
              onChange=${(e) => setSplitPersonId(e.target.value ? Number(e.target.value) : null)}>
              <option value="">No one</option>
              ${persons.map((p) => html`<option value=${p.id}>${p.name}</option>`)}
              ${persons.length === 0 && html`<option disabled>Add people on the People tab</option>`}
            </select>
          </div>
        `}
      </div>
      ${splitOn &&
      html`
        <div class="field">
          <label for="split-share">${splitPerson?.name}'s share (of ₹${amount || "…"} total)</label>
          <input id="split-share" type="text" inputmode="decimal" value=${friendAmount} placeholder="0"
            onInput=${(e) => setFriendAmount(e.target.value.replace(/[^\d.]/g, ""))} />
          ${Number(friendAmount) > 0 && Number(friendAmount) < Number(amount) &&
          html`<div class="muted" style="font-size:12.5px; margin-top:6px">
            You pay ${fmt(myShare)} · ${splitPerson?.name} owes ${fmt(Number(friendAmount))}
          </div>`}
        </div>
      `}
      <div class="field">
        <label for="tx-note">Note</label>
        <input id="tx-note" type="text" value=${note} placeholder="Optional" onInput=${(e) => setNote(e.target.value)} />
      </div>
      ${!isEdit && !splitOn &&
      html`
        <div class="field">
          <button class="chip ${makeRecurring ? "sel" : ""}" aria-pressed=${makeRecurring}
            onClick=${() => setMakeRecurring(!makeRecurring)}>
            <${Icon} d=${P.recur} size=${16} /> Repeat monthly on day ${Number(date.slice(8, 10))}
          </button>
        </div>
      `}
      <div class="btn-row">
        ${isEdit && html`<button class="btn danger" disabled=${saving} onClick=${remove}>Delete</button>`}
        <button class="btn" disabled=${!valid || saving} onClick=${save}>
          ${isEdit ? "Save changes" : splitOn ? "Save split" : `Save ${type}`}
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

  function remove() {
    deleteWithUndo(tx.id, onSaved);
  }

  return html`
    <${Sheet} title=${`${LOAN_VERBS[tx.type]} ${tx.person?.name}`} onClose=${onClose}>
      <${AmountInput} hero value=${amount} onChange=${setAmount} currency="INR" />
      <div class="field">
        <div class="field-label">Account</div>
        <${Chips} items=${accounts} value=${accountId} onChange=${setAccountId} />
      </div>
      <div class="field">
        <div class="field-label">Date</div>
        <${DateField} value=${date} onChange=${setDate} />
      </div>
      <div class="field">
        <label for="loan-note">Note</label>
        <input id="loan-note" type="text" value=${note} placeholder="Optional" onInput=${(e) => setNote(e.target.value)} />
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
    <${Sheet} title="Filters" onClose=${onClose}>
      <div class="field">
        <div class="field-label">Account</div>
        <${Chips} items=${accounts} value=${filters.account_id} allowNone
          onChange=${(id) => onChange({ ...filters, account_id: id })} />
      </div>
      <div class="field">
        <div class="field-label">Person</div>
        ${persons.length === 0
          ? html`<div class="muted" style="font-size:13px">No people yet.</div>`
          : html`<${Chips} items=${persons} value=${filters.person_id} allowNone
              onChange=${(id) => onChange({ ...filters, person_id: id })} />`}
      </div>
      <div class="btn-row">
        <button class="btn ghost" onClick=${() => { onChange({ type: filters.type, account_id: null, person_id: null }); onClose(); }}>Clear</button>
        <button class="btn" onClick=${onClose}>Done</button>
      </div>
    <//>
  `;
}

function SummaryCard({ month, totals, budgetLimit }) {
  const pct = budgetLimit > 0 ? totals.expense / budgetLimit : 0;
  const label = month === currentMonth() ? "Spent this month" : `Spent in ${monthLabel(month).split(" ")[0]}`;
  return html`
    <div class="card sumcard">
      <div class="lab">${label}</div>
      <div class="hero-row">
        <div class="hero-num tnum">${fmt(totals.expense)}</div>
        ${budgetLimit > 0 && html`<div class="muted tnum" style="font-size:13px">of ${fmt(budgetLimit)}</div>`}
      </div>
      ${budgetLimit > 0 &&
      html`<div class="bar" role="img" aria-label=${`${Math.round(pct * 100)}% of the month's budgets`}>
        <div class="fill ${pct > 1 ? "over" : pct > 0.9 ? "warn" : ""}" style=${`width:${Math.min(pct, 1) * 100}%`}></div>
      </div>`}
      <div class="statgrid">
        <div class="stat">
          <div class="lab">Income</div>
          <div class="val pos tnum">${totals.income > 0 ? `+${fmt(totals.income)}` : fmt(0)}</div>
        </div>
        <div class="stat">
          <div class="lab">Net</div>
          <div class="val tnum">${fmtSigned(totals.net)}</div>
        </div>
      </div>
    </div>
  `;
}

const TYPE_PILLS = [
  { id: null, name: "All" },
  { id: "expense", name: "Expense" },
  { id: "income", name: "Income" },
  { id: "transfer", name: "Transfer" },
];
const TYPE_PLURAL = { expense: "expenses", income: "income", transfer: "transfers" };

export function TransactionsPage() {
  const { month } = useStore();
  const [sheet, setSheet] = useState(null); // null | {} (new) | tx (edit)
  const [refresh, setRefresh] = useState(0);
  const [searchOpen, setSearchOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [q, setQ] = useState("");
  const [filters, setFilters] = useState({ type: null, account_id: null, person_id: null });
  const [filterOpen, setFilterOpen] = useState(false);
  const [recurringOpen, setRecurringOpen] = useState(false);
  const [from, to] = monthBounds(month);

  const { data: due } = useApi(() => api.recurringDue(), [refresh]);
  const { data: budgets } = useApi(() => api.budgets(month), [month, refresh]);
  const [pendingTick, setPendingTick] = useState(0);
  useEffect(() => subscribePending(() => setPendingTick((t) => t + 1)), []);

  useEffect(() => {
    const t = setTimeout(() => setQ(query.trim()), 300);
    return () => clearTimeout(t);
  }, [query]);

  /* Search text, account or person switch to all-time results. The type pills
     only narrow whatever list is showing, so they never leave the month. */
  const active = !!(q || filters.account_id || filters.person_id);
  const filterCount = ["account_id", "person_id"].filter((k) => filters[k]).length;

  const { data: txs, loading, error } = useApi(
    () =>
      active
        ? api.transactions({ q, type: filters.type, account_id: filters.account_id, person_id: filters.person_id, limit: 500 })
        : api.transactions({ from, to, limit: 1000 }),
    [month, refresh, q, active ? filters.type : null, filters.account_id, filters.person_id]
  );

  function closeSearch() {
    setQuery("");
    setQ("");
    setSearchOpen(false);
  }

  function clearAll() {
    closeSearch();
    setFilters({ type: null, account_id: null, person_id: null });
  }

  const shown = useMemo(() => (txs || []).filter((tx) => !isHidden(tx.id)), [txs, pendingTick]);
  const visible = useMemo(
    () => (filters.type ? shown.filter((tx) => tx.type === filters.type) : shown),
    [shown, filters.type]
  );

  const groups = useMemo(() => {
    const byDay = new Map();
    for (const tx of visible) {
      if (!byDay.has(tx.date)) byDay.set(tx.date, []);
      byDay.get(tx.date).push(tx);
    }
    return [...byDay.entries()];
  }, [visible]);

  const totals = useMemo(() => {
    let income = 0, expense = 0;
    for (const tx of shown) {
      if (tx.type === "income") income += tx.inr_amount;
      if (tx.type === "expense") expense += tx.inr_amount;
    }
    return { income, expense, net: income - expense };
  }, [shown]);

  const saved = () => { setSheet(null); setRefresh((r) => r + 1); };

  return html`
    <div class="page has-dock">
      <div class="topbar">
        ${searchOpen
          ? html`<div class="searchbar">
              <${Icon} d=${P.search} size=${18} />
              <input type="search" placeholder="Notes, categories, accounts, people" autofocus
                aria-label="Search transactions" value=${query} onInput=${(e) => setQuery(e.target.value)} />
            </div>`
          : html`<${MonthNav} month=${month} onChange=${setMonth} />`}
        <div class="topbar-actions">
          <button class="iconbtn" onClick=${() => (searchOpen ? closeSearch() : setSearchOpen(true))}
            aria-label=${searchOpen ? "Close search" : "Search"}>
            <${Icon} d=${searchOpen ? P.close : P.search} size=${19} />
          </button>
          <button class="iconbtn" onClick=${() => setFilterOpen(true)}
            aria-label=${filterCount ? `Filters, ${filterCount} on` : "Filters"}>
            <${Icon} d=${P.filter} size=${19} />
            ${filterCount > 0 && html`<span class="badge">${filterCount}</span>`}
          </button>
        </div>
      </div>
      ${active
        ? html`
            <div class="resultline">
              <span class="muted">${visible.length} results · all time</span>
              <button class="linkbtn" onClick=${clearAll}>Clear all</button>
            </div>
          `
        : html`
            <${SummaryCard} month=${month} totals=${totals} budgetLimit=${budgets?.total_limit || 0} />
            <${RecurringBanner} items=${due?.items || []} onOpen=${() => setRecurringOpen(true)} />
          `}
      <div class="pills" role="group" aria-label="Type">
        ${TYPE_PILLS.map(
          (t) => html`
            <button class="pill ${filters.type === t.id ? "sel" : ""}" aria-pressed=${filters.type === t.id}
              onClick=${() => setFilters({ ...filters, type: t.id })}>
              ${t.name}
            </button>
          `
        )}
      </div>
      ${error && html`<div class="card danger-text">${error}</div>`}
      ${!loading && txs && visible.length === 0 &&
      html`<${EmptyState}
        icon=${active ? "🔍" : "🧾"}
        title=${active ? "No matches" : filters.type && shown.length ? `No ${TYPE_PLURAL[filters.type]} this month` : "No transactions this month"}
        sub=${active ? "Try different words or clear the filters." : "Type one in the bar below — “450 swiggy yesterday” — or tap +."} />`}
      ${groups.map(([date, list]) => {
        let dayIncome = 0, dayExpense = 0;
        for (const tx of list) {
          if (tx.type === "income") dayIncome += tx.inr_amount;
          if (tx.type === "expense") dayExpense += tx.inr_amount;
        }
        return html`
          <div key=${date}>
            <div class="dayhead">
              <span class="d">${dayHeading(date)}</span>
              <span class="t tnum">
                ${dayIncome > 0 && `+${fmt(dayIncome)}`}
                ${dayIncome > 0 && dayExpense > 0 && " · "}
                ${dayExpense > 0 && `−${fmt(dayExpense)}`}
              </span>
            </div>
            <div class="listcard">
              ${list.map((tx) => html`<${TxRow} key=${tx.id} tx=${tx} onClick=${() => setSheet(tx)} />`)}
            </div>
          </div>
        `;
      })}
      <${QuickAdd} onSaved=${() => setRefresh((r) => r + 1)} onManual=${() => setSheet({})} />
      ${sheet !== null &&
      (sheet.id
        ? html`<${TxEditor} tx=${sheet} onClose=${() => setSheet(null)} onSaved=${saved} />`
        : html`<${TxSheet} tx=${null} onClose=${() => setSheet(null)} onSaved=${saved} />`)}
      ${filterOpen && html`<${FilterSheet} filters=${filters} onChange=${setFilters} onClose=${() => setFilterOpen(false)} />`}
      ${recurringOpen && html`<${RecurringSheet} onClose=${() => setRecurringOpen(false)} onChanged=${() => setRefresh((r) => r + 1)} />`}
    </div>
  `;
}
