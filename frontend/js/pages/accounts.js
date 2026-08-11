import { html, useState } from "../html.js";
import { api } from "../api.js";
import { fmt, todayISO } from "../format.js";
import { invalidate } from "../store.js";
import { AmountInput, Chips, Sheet, toast, useApi, useStore } from "../components/ui.js";
import { TxRow, TxSheet } from "./transactions.js";

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

function AccountDetail({ account, onClose }) {
  const [editTx, setEditTx] = useState(null);
  const [refresh, setRefresh] = useState(0);
  const { data: txs } = useApi(
    () => api.transactions({ account_id: account.id, limit: 100 }),
    [account.id, refresh]
  );
  return html`
    <${Sheet} onClose=${onClose}>
      <div class="spread" style="margin-bottom:4px">
        <h2 style="margin:0">${account.name}</h2>
        <div class="amt tnum" style="font-weight:700; font-size:18px">${fmt(account.balance)}</div>
      </div>
      <div class="muted" style="font-size:13px; margin-bottom:10px">Recent transactions</div>
      ${(txs || []).map((tx) => html`<${TxRow} key=${tx.id} tx=${tx} onClick=${() => setEditTx(tx)} />`)}
      ${txs?.length === 0 && html`<div class="muted" style="padding:16px 0">No transactions yet.</div>`}
      ${editTx &&
      html`<${TxSheet} tx=${editTx} onClose=${() => setEditTx(null)}
        onSaved=${() => { setEditTx(null); setRefresh((r) => r + 1); }} />`}
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
  const [refresh, setRefresh] = useState(0);
  const { data: accounts } = useApi(() => api.accounts(), [refresh, store.accounts]);

  const list = accounts || [];
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
      <button class="fab" onClick=${() => setAdding(true)} aria-label="Add account">+</button>
      ${payCard && html`<${PayCardSheet} card=${payCard} onClose=${() => setPayCard(null)}
        onSaved=${() => { setPayCard(null); setRefresh((r) => r + 1); }} />`}
      ${detail && html`<${AccountDetail} account=${detail} onClose=${() => setDetail(null)} />`}
      ${adding && html`<${NewAccountSheet} onClose=${() => setAdding(false)}
        onSaved=${() => { setAdding(false); setRefresh((r) => r + 1); }} />`}
    </div>
  `;
}
