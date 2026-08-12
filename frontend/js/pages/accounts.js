import { html, useEffect, useState } from "../html.js";
import { api } from "../api.js";
import { fmt, shortDate, todayISO } from "../format.js";
import { invalidate, store } from "../store.js";
import { AmountInput, Chips, Sheet, toast, useApi, useStore } from "../components/ui.js";
import { TxEditor, TxRow } from "./transactions.js";
import { isHidden, subscribePending } from "../pendingDelete.js";

const GROUPS = [
  { type: "cash", label: "Cash" },
  { type: "bank", label: "Bank" },
  { type: "credit_card", label: "Credit cards" },
  { type: "wallet", label: "Wallets" },
];

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
    <${Sheet} onClose=${onClose}>
      <h2>Pay ${card.name}</h2>
      <div class="field">
        <label>Amount</label>
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
        <label>From account</label>
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
    <${Sheet} onClose=${onClose}>
      <h2>Edit account</h2>
      <div class="field">
        <label>Name</label>
        <input type="text" value=${name} onInput=${(e) => setName(e.target.value)} />
      </div>
      <div class="field">
        <label>Type</label>
        <${Chips} items=${GROUPS.map((g) => ({ id: g.type, name: g.label }))} value=${type} onChange=${setType} />
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
      <div class="spread" style="margin-bottom:4px">
        <h2 style="margin:0">${account.name}</h2>
        <div class="row" style="display:flex; align-items:center; gap:10px">
          <button class="chip" onClick=${() => setEditing(true)}>Edit</button>
          <div class="amt tnum" style="font-weight:700; font-size:18px">${fmt(account.balance)}</div>
        </div>
      </div>
      ${account.cycle &&
      html`<div class="muted" style="font-size:13px; margin-bottom:6px">
        This cycle ${fmt(account.cycle.spend)} · statement ${shortDate(account.cycle.statement_date)}${account.cycle.due_date ? ` · due ${shortDate(account.cycle.due_date)}` : ""}
      </div>`}
      <div class="muted" style="font-size:13px; margin-bottom:10px">Recent transactions</div>
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
    <${Sheet} onClose=${onClose}>
      <h2>New account</h2>
      <div class="field">
        <label>Name</label>
        <input type="text" value=${name} onInput=${(e) => setName(e.target.value)} placeholder="e.g. SBI Savings" />
      </div>
      <div class="field">
        <label>Type</label>
        <${Chips}
          items=${GROUPS.map((g) => ({ id: g.type, name: g.label }))}
          value=${type} onChange=${setType} />
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
  const [refresh, setRefresh] = useState(0);
  const { data: accounts } = useApi(() => api.accounts({ include_archived: true }), [refresh, store.accounts]);

  const list = (accounts || []).filter((a) => !a.is_archived);
  const archived = (accounts || []).filter((a) => a.is_archived);
  const netWorth = list.reduce((sum, a) => sum + a.balance, 0);

  return html`
    <div class="page">
      <div class="topbar"><h1>Accounts</h1></div>
      <div class="networth card">
        <div class="lab">Net worth</div>
        <div class="val tnum">${fmt(netWorth)}</div>
      </div>
      ${GROUPS.map((group) => {
        const items = list.filter((a) => a.type === group.type);
        if (!items.length) return null;
        return html`
          <div key=${group.type}>
            <div class="section-title">${group.label}</div>
            <div class="card txcard">
              ${items.map((acc) => html`
                <div class="acct" key=${acc.id}>
                  <button class="grow row" style="text-align:left" onClick=${() => setDetail(acc)}>
                    <div class="grow">
                      <div class="name">${acc.name}</div>
                      ${acc.type === "credit_card" && acc.outstanding > 0 &&
                      html`<div class="sub neg">Outstanding ${fmt(acc.outstanding)}</div>`}
                      ${acc.cycle &&
                      html`<div class="sub">cycle ${fmt(acc.cycle.spend)} · closes in ${acc.cycle.statement_in_days}d${acc.cycle.due_date ? ` · due ${shortDate(acc.cycle.due_date)}` : ""}</div>`}
                    </div>
                    <div class="bal tnum ${acc.balance < 0 ? "neg" : ""}">${fmt(acc.balance)}</div>
                  </button>
                  ${acc.type === "credit_card" && acc.outstanding > 0 &&
                  html`<button class="paybtn" onClick=${() => setPayCard(acc)}>Pay</button>`}
                </div>
              `)}
            </div>
          </div>
        `;
      })}
      ${archived.length > 0 &&
      html`
        <button class="section-title" style="background:none; width:100%; text-align:left; cursor:pointer"
          onClick=${() => setShowArchived(!showArchived)}>
          Archived (${archived.length}) ${showArchived ? "▾" : "▸"}
        </button>
        ${showArchived &&
        html`
          <div class="card txcard" style="opacity:0.65">
            ${archived.map((acc) => html`
              <div class="acct" key=${acc.id}>
                <button class="grow row" style="text-align:left" onClick=${() => setDetail(acc)}>
                  <div class="grow"><div class="name">${acc.name}</div></div>
                  <div class="bal tnum ${acc.balance < 0 ? "neg" : ""}">${fmt(acc.balance)}</div>
                </button>
              </div>
            `)}
          </div>
        `}
      `}
      <a class="card export-row" href="/api/export.xlsx">⬇ Download Excel export</a>
      <div class="signedin muted">
        signed in as <b>${store.username || "…"}</b> ·${" "}
        <button class="linkbtn" onClick=${async () => { try { await api.logout(); } catch { /* ignore */ } location.reload(); }}>
          Log out
        </button>
      </div>
      <button class="fab" onClick=${() => setAdding(true)} aria-label="Add account">+</button>
      ${payCard && html`<${PayCardSheet} card=${payCard} onClose=${() => setPayCard(null)}
        onSaved=${() => { setPayCard(null); setRefresh((r) => r + 1); }} />`}
      ${detail && html`<${AccountDetail} account=${detail} onClose=${() => setDetail(null)} />`}
      ${adding && html`<${NewAccountSheet} onClose=${() => setAdding(false)}
        onSaved=${() => { setAdding(false); setRefresh((r) => r + 1); }} />`}
    </div>
  `;
}
