import { html, useEffect, useState } from "../html.js";
import { api } from "../api.js";
import { fmt, shortDate, todayISO } from "../format.js";
import { Icon, P } from "../icons.js";
import { invalidate, store } from "../store.js";
import { AmountInput, Chips, Sheet, toast, useApi, useStore } from "../components/ui.js";
import { TxEditor, TxRow } from "./transactions.js";
import { isHidden, subscribePending } from "../pendingDelete.js";
import { SettingsSheet, SignedInCard } from "./settings.js";

/* Display groups, in page order (credit cards render as their own cards above
   these). Every ACCOUNT_TYPES value lands in exactly one place. */
const GROUPS = [
  { types: ["bank"], label: "Bank" },
  { types: ["wallet", "cash"], label: "Wallets & cash" },
];
const TYPE_CHOICES = [
  { id: "bank", name: "Bank" },
  { id: "credit_card", name: "Credit card" },
  { id: "wallet", name: "Wallet" },
  { id: "cash", name: "Cash" },
];
const TYPE_SUB = { bank: "Bank account", wallet: "Wallet", cash: "In hand", credit_card: "Credit card" };

/* "HDFC Savings" -> "HD"; cash gets the rupee sign. */
function monogram(acc) {
  if (acc.type === "cash") return "₹";
  const word = acc.name.replace(/[^\p{L}\p{N} ]/gu, "").trim().split(/\s+/)[0] || acc.name;
  return word.slice(0, 2).toUpperCase();
}

const DAY_MS = 86400000;
const isoDay = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const addDays = (iso, n) => { const d = new Date(`${iso}T00:00:00`); d.setDate(d.getDate() + n); return isoDay(d); };
const daysBetween = (a, b) => Math.round((new Date(`${b}T00:00:00`) - new Date(`${a}T00:00:00`)) / DAY_MS);
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

/* Credit card: outstanding, statement countdown, cycle progress, pay button.
   cycle.start is the last statement date, so the cycle runs start+1 .. next. */
function CardTile({ acc, onOpen, onPay }) {
  const c = acc.cycle;
  const cycleLen = c ? daysBetween(c.start, c.statement_date) : 0;
  const cycleDay = c ? cycleLen - c.statement_in_days : 0;
  const owes = acc.outstanding > 0;
  let when = "Set a statement day to track the cycle";
  if (c) {
    when = c.statement_in_days === 0 ? "Statement today" : `Statement in ${plural(c.statement_in_days, "day")}`;
    if (c.due_date) when += ` · Due ${shortDate(c.due_date)}`;
  }
  return html`
    <div class="card cc">
      <button class="cc-head" onClick=${onOpen}>
        <div class="grow">
          <div class="name ellipsis">${acc.name}</div>
          <div class="sub">${when}</div>
        </div>
        <div class="right">
          <div class="amt tnum">${fmt(owes ? acc.outstanding : Math.max(acc.balance, 0))}</div>
          <div class="sub">${owes ? "outstanding" : acc.balance > 0 ? "in credit" : "no dues"}</div>
        </div>
      </button>
      ${c &&
      html`<div>
        <div class="bar" role="img" aria-label=${`Day ${cycleDay} of ${cycleLen} in this statement cycle`}>
          <div class="fill warn" style=${`width:${cycleLen ? Math.min(cycleDay / cycleLen, 1) * 100 : 0}%`}></div>
        </div>
        <div class="cc-meta">
          <span>Cycle ${shortDate(addDays(c.start, 1))} – ${shortDate(c.statement_date)} · ${fmt(c.spend)} spent</span>
          <span style="white-space:nowrap">Day ${cycleDay} of ${cycleLen}</span>
        </div>
      </div>`}
      ${owes && html`<button class="btn sm" onClick=${onPay}>Pay bill from bank</button>`}
    </div>
  `;
}

function AccountRow({ acc, onOpen }) {
  return html`
    <button class="acct" onClick=${onOpen}>
      <span class="tile mono" aria-hidden="true">${monogram(acc)}</span>
      <span class="grow">
        <span class="name ellipsis" style="display:block">${acc.name}</span>
        <span class="sub" style="display:block">${TYPE_SUB[acc.type] || acc.type}</span>
      </span>
      <span class="bal tnum">${fmt(acc.balance)}</span>
    </button>
  `;
}

/* One-tap card repayment: a transfer bank -> card, prefilled to full outstanding. */
function PayCardSheet({ card, onClose, onSaved }) {
  const { accounts } = useStore();
  const banks = accounts.filter((a) => a.type !== "credit_card" && a.id !== card.id);
  const [fromId, setFromId] = useState(banks.find((b) => b.type === "bank")?.id ?? banks[0]?.id);
  const [mode, setMode] = useState("full");
  const [custom, setCustom] = useState("");
  const [saving, setSaving] = useState(false);

  const amount = mode === "full" ? card.outstanding : Number(custom);
  const valid = fromId && amount > 0;

  async function pay() {
    setSaving(true);
    try {
      await api.createTransaction({
        type: "transfer",
        date: todayISO(),
        account_id: fromId,
        to_account_id: card.id,
        amount,
        note: `${card.name} bill payment`,
      });
      invalidate();
      toast(`Paid ${card.name} · not counted as expense`);
      onSaved();
    } catch (err) {
      toast(err.message);
      setSaving(false);
    }
  }

  return html`
    <${Sheet} title=${`Pay ${card.name}`} onClose=${onClose}>
      <div class="field">
        <div class="field-label">Amount</div>
        <div class="chips" style="margin-bottom:10px">
          <button class="chip ${mode === "full" ? "sel" : ""}" onClick=${() => setMode("full")}>
            Full outstanding · ${fmt(card.outstanding)}
          </button>
          <button class="chip ${mode === "custom" ? "sel" : ""}" onClick=${() => setMode("custom")}>Custom</button>
        </div>
        ${mode === "custom" &&
        html`<${AmountInput} value=${custom} onChange=${setCustom} currency="INR" autofocus />`}
      </div>
      <div class="field">
        <div class="field-label">From account</div>
        <${Chips} items=${banks} value=${fromId} onChange=${setFromId} />
      </div>
      <button class="btn" disabled=${!valid || saving} onClick=${pay}>
        Pay ${amount > 0 ? fmt(amount) : ""}
      </button>
    <//>
  `;
}

/* Edit name/type/card fields, or archive. Small by design: balances are
   derived, so only metadata is editable here. */
function EditAccountSheet({ account, onClose, onSaved }) {
  const [name, setName] = useState(account.name);
  const [type, setType] = useState(account.type);
  const [statementDay, setStatementDay] = useState(account.statement_day ?? "");
  const [dueDay, setDueDay] = useState(account.due_day ?? "");
  const [creditLimit, setCreditLimit] = useState(account.credit_limit ?? "");
  const [saving, setSaving] = useState(false);

  async function save() {
    setSaving(true);
    try {
      const patch = {};
      if (name.trim() && name.trim() !== account.name) patch.name = name.trim();
      if (type !== account.type) patch.type = type;
      if (type === "credit_card") {
        if (String(account.statement_day ?? "") !== String(statementDay)) {
          patch.statement_day = statementDay ? Number(statementDay) : null;
        }
        if (String(account.due_day ?? "") !== String(dueDay)) {
          patch.due_day = dueDay ? Number(dueDay) : null;
        }
        if (String(account.credit_limit ?? "") !== String(creditLimit)) {
          patch.credit_limit = creditLimit ? Number(creditLimit) : null;
        }
      }
      if (Object.keys(patch).length) await api.updateAccount(account.id, patch);
      invalidate();
      toast("Account updated");
      onSaved();
    } catch (err) {
      toast(err.message);
      setSaving(false);
    }
  }

  async function toggleArchive() {
    setSaving(true);
    try {
      await api.updateAccount(account.id, { is_archived: !account.is_archived });
      invalidate();
      toast(account.is_archived ? "Account restored" : "Account archived");
      onSaved();
    } catch (err) {
      toast(err.message);
      setSaving(false);
    }
  }

  const dayInput = (label, value, setter) => html`
    <div class="field" style="flex:1">
      <label>${label}</label>
      <input type="number" min="1" max="31" value=${value} placeholder="—"
        onInput=${(e) => setter(e.target.value.replace(/[^\d]/g, ""))} />
    </div>
  `;

  return html`
    <${Sheet} title="Edit account" onClose=${onClose}>
      <div class="field">
        <label>Name</label>
        <input type="text" value=${name} onInput=${(e) => setName(e.target.value)} />
      </div>
      <div class="field">
        <div class="field-label">Type</div>
        <${Chips} items=${TYPE_CHOICES} value=${type} onChange=${setType} />
      </div>
      ${type === "credit_card" &&
      html`
        <div style="display:flex; gap:10px">
          ${dayInput("Statement day", statementDay, setStatementDay)}
          ${dayInput("Due day", dueDay, setDueDay)}
        </div>
        <div class="field">
          <label>Credit limit</label>
          <${AmountInput} value=${String(creditLimit)} onChange=${setCreditLimit} currency="INR" />
        </div>
      `}
      <div class="btn-row">
        <button class="btn danger" disabled=${saving} onClick=${toggleArchive}>
          ${account.is_archived ? "Unarchive" : "Archive"}
        </button>
        <button class="btn" disabled=${!name.trim() || saving} onClick=${save}>Save changes</button>
      </div>
    <//>
  `;
}

function AccountDetail({ account, onClose }) {
  const [editTx, setEditTx] = useState(null);
  const [editing, setEditing] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [, setTick] = useState(0);
  useEffect(() => subscribePending(() => setTick((t) => t + 1)), []);
  const { data } = useApi(
    () => api.transactions({ account_id: account.id, limit: 100 }),
    [account.id, refresh]
  );
  const txs = data?.filter((tx) => !isHidden(tx.id));
  return html`
    <${Sheet} onClose=${onClose}>
      <div class="sheet-head" style="margin-bottom:4px">
        <h2 class="ellipsis">${account.name}</h2>
        <div class="row">
          <div class="tnum" style="font-weight:700; font-size:18px">${fmt(account.balance)}</div>
          <button class="chip sm" onClick=${() => setEditing(true)}>Edit</button>
        </div>
      </div>
      ${account.cycle &&
      html`<div class="muted" style="font-size:13px; margin-bottom:6px">
        This cycle ${fmt(account.cycle.spend)} · statement ${shortDate(account.cycle.statement_date)}${account.cycle.due_date ? ` · due ${shortDate(account.cycle.due_date)}` : ""}
      </div>`}
      <div class="field-label" style="margin:14px 0 2px">Recent transactions</div>
      ${(txs || []).map((tx) => html`<${TxRow} key=${tx.id} tx=${tx} onClick=${() => setEditTx(tx)} />`)}
      ${txs?.length === 0 && html`<div class="muted" style="padding:16px 0">No transactions yet.</div>`}
      ${editTx &&
      html`<${TxEditor} tx=${editTx} onClose=${() => setEditTx(null)}
        onSaved=${() => { setEditTx(null); setRefresh((r) => r + 1); }} />`}
      ${editing &&
      html`<${EditAccountSheet} account=${account} onClose=${() => setEditing(false)}
        onSaved=${() => { setEditing(false); onClose(); }} />`}
    <//>
  `;
}

function NewAccountSheet({ onClose, onSaved }) {
  const [name, setName] = useState("");
  const [type, setType] = useState("bank");
  const [opening, setOpening] = useState("");
  const [saving, setSaving] = useState(false);
  async function save() {
    setSaving(true);
    try {
      const value = Number(opening) || 0;
      // A card's "current outstanding" is a liability -> negative opening balance.
      await api.createAccount({
        name, type,
        opening_balance: type === "credit_card" ? -value : value,
      });
      invalidate();
      toast("Account added");
      onSaved();
    } catch (err) {
      toast(err.message);
      setSaving(false);
    }
  }
  return html`
    <${Sheet} title="New account" onClose=${onClose}>
      <div class="field">
        <label>Name</label>
        <input type="text" value=${name} onInput=${(e) => setName(e.target.value)} placeholder="e.g. SBI Savings" />
      </div>
      <div class="field">
        <div class="field-label">Type</div>
        <${Chips} items=${TYPE_CHOICES} value=${type} onChange=${setType} />
      </div>
      <div class="field">
        <label>${type === "credit_card" ? "Current outstanding (optional)" : "Opening balance"}</label>
        <${AmountInput}
          value=${opening}
          onChange=${setOpening}
          currency="INR" />
      </div>
      <button class="btn" disabled=${!name.trim() || saving} onClick=${save}>Add account</button>
    <//>
  `;
}

export function AccountsPage() {
  const store = useStore();
  const [payCard, setPayCard] = useState(null);
  const [detail, setDetail] = useState(null);
  const [adding, setAdding] = useState(false);
  const [showArchived, setShowArchived] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const { data: accounts } = useApi(() => api.accounts({ include_archived: true }), [refresh, store.accounts]);

  const list = (accounts || []).filter((a) => !a.is_archived);
  const archived = (accounts || []).filter((a) => a.is_archived);
  const netWorth = list.reduce((sum, a) => sum + a.balance, 0);
  const assets = list.reduce((sum, a) => sum + Math.max(a.balance, 0), 0);
  const owed = list.reduce((sum, a) => sum + Math.max(-a.balance, 0), 0);
  // "Card dues" when every negative balance is a card; otherwise say what it is.
  const owedIsCards = list.every((a) => a.balance >= 0 || a.type === "credit_card");
  const cards = list.filter((a) => a.type === "credit_card");

  return html`
    <div class="page">
      <div class="topbar">
        <h1>Accounts</h1>
        <div class="topbar-actions">
          <button class="iconbtn" onClick=${() => setAdding(true)} aria-label="Add account">
            <${Icon} d=${P.plus} size=${19} sw=${2} />
          </button>
          <button class="iconbtn" onClick=${() => setSettingsOpen(true)} aria-label="Settings">
            <${Icon} d=${P.gear} size=${19} />
          </button>
        </div>
      </div>
      <div class="hero-card">
        <div class="lab">Net worth</div>
        <div class="hero-num tnum">${fmt(netWorth)}</div>
        <div class="statgrid">
          <div class="stat"><div class="lab">Assets</div><div class="val tnum">${fmt(assets)}</div></div>
          <div class="stat"><div class="lab">${owedIsCards ? "Card dues" : "Liabilities"}</div><div class="val tnum">${fmt(owed)}</div></div>
        </div>
      </div>
      ${cards.length > 0 &&
      html`
        <div class="section-title">Credit cards</div>
        ${cards.map((acc) => html`<${CardTile} key=${acc.id} acc=${acc}
          onOpen=${() => setDetail(acc)} onPay=${() => setPayCard(acc)} />`)}
      `}
      ${GROUPS.map((group) => {
        const items = list.filter((a) => group.types.includes(a.type));
        if (!items.length) return null;
        return html`
          <div key=${group.label}>
            <div class="section-title">${group.label}</div>
            <div class="listcard">
              ${items.map((acc) => html`<${AccountRow} key=${acc.id} acc=${acc} onOpen=${() => setDetail(acc)} />`)}
            </div>
          </div>
        `;
      })}
      ${archived.length > 0 &&
      html`
        <button class="archived-toggle ${showArchived ? "open" : ""}" aria-expanded=${showArchived}
          onClick=${() => setShowArchived(!showArchived)}>
          <${Icon} d=${P.chevR} size=${14} sw=${2.2} />
          Archived (${archived.length})
        </button>
        ${showArchived &&
        html`
          <div class="listcard" style="opacity:0.7">
            ${archived.map((acc) => html`<${AccountRow} key=${acc.id} acc=${acc} onOpen=${() => setDetail(acc)} />`)}
          </div>
        `}
      `}
      <div class="section-title">Signed in</div>
      <${SignedInCard} />
      ${payCard && html`<${PayCardSheet} card=${payCard} onClose=${() => setPayCard(null)}
        onSaved=${() => { setPayCard(null); setRefresh((r) => r + 1); }} />`}
      ${detail && html`<${AccountDetail} account=${detail} onClose=${() => setDetail(null)} />`}
      ${settingsOpen && html`<${SettingsSheet} onClose=${() => setSettingsOpen(false)} />`}
      ${adding && html`<${NewAccountSheet} onClose=${() => setAdding(false)}
        onSaved=${() => { setAdding(false); setRefresh((r) => r + 1); }} />`}
    </div>
  `;
}
