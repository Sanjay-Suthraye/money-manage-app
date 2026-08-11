import { html, useState } from "../html.js";
import { api } from "../api.js";
import { fmt, todayISO } from "../format.js";
import { invalidate } from "../store.js";
import { AmountInput, Chips, EmptyState, Sheet, toast, useApi, useStore } from "../components/ui.js";

const KIND_LABELS = {
  lend: "Lent",
  borrow: "Borrowed",
  repay_in: "They repaid",
  repay_out: "You repaid",
};

/* Shared sheet for all four loan moves. `Settle` pre-fills the full balance. */
function LoanSheet({ person, kind, onClose, onSaved }) {
  const { accounts } = useStore();
  const prefill = kind === "repay_in" || kind === "repay_out" ? Math.abs(person.net) : "";
  const [amount, setAmount] = useState(prefill ? String(prefill) : "");
  const [accountId, setAccountId] = useState(
    accounts.find((a) => a.type === "bank")?.id ?? accounts[0]?.id
  );
  const [date, setDate] = useState(todayISO());
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);

  const titles = {
    lend: `Lend to ${person.name}`,
    borrow: `Borrow from ${person.name}`,
    repay_in: `${person.name} pays you back`,
    repay_out: `Pay ${person.name} back`,
  };
  const accountLabels = {
    lend: "From account",
    borrow: "Into account",
    repay_in: "Into account",
    repay_out: "From account",
  };

  async function save() {
    setSaving(true);
    try {
      await api.addLoan({
        kind,
        person_id: person.id,
        account_id: accountId,
        amount: Number(amount),
        date,
        note: note || null,
      });
      invalidate();
      toast("Saved");
      onSaved();
    } catch (err) {
      toast(err.message);
      setSaving(false);
    }
  }

  return html`
    <${Sheet} onClose=${onClose}>
      <h2>${titles[kind]}</h2>
      <${AmountInput} value=${amount} onChange=${setAmount} currency="INR" autofocus=${!prefill} />
      <div class="field" style="margin-top:10px">
        <label>${accountLabels[kind]}</label>
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
      <button class="btn" disabled=${!(Number(amount) > 0) || saving} onClick=${save}>
        ${titles[kind]}${Number(amount) > 0 ? ` · ${fmt(Number(amount))}` : ""}
      </button>
    <//>
  `;
}

function LedgerSheet({ person, onClose }) {
  const { data } = useApi(() => api.personLedger(person.id), [person.id]);
  return html`
    <${Sheet} onClose=${onClose}>
      <div class="spread">
        <h2 style="margin:0">${person.name}</h2>
        <div class="tnum ${data?.net > 0 ? "pos" : data?.net < 0 ? "neg" : ""}" style="font-weight:700; font-size:17px">
          ${data ? fmt(Math.abs(data.net)) : ""}
          ${data?.net > 0 ? " owed to you" : data?.net < 0 ? " you owe" : ""}
        </div>
      </div>
      <div style="margin-top:12px">
        ${(data?.entries || []).map(
          (e) => html`
            <div class="ledger-row" key=${e.id}>
              <div>
                <div style="font-weight:550">${KIND_LABELS[e.type]}${e.note ? ` · ${e.note}` : ""}</div>
                <div class="muted" style="font-size:12.5px">${e.date}</div>
              </div>
              <div class="right">
                <div class="tnum" style="font-weight:600">${fmt(e.amount)}</div>
                <div class="muted tnum" style="font-size:12.5px">net ${fmt(e.running_net)}</div>
              </div>
            </div>
          `
        )}
        ${data?.entries?.length === 0 && html`<div class="muted" style="padding:14px 0">No entries yet.</div>`}
      </div>
    <//>
  `;
}

function NewPersonSheet({ onClose, onSaved }) {
  const [name, setName] = useState("");
  const [saving, setSaving] = useState(false);
  async function save() {
    setSaving(true);
    try {
      await api.createPerson({ name });
      invalidate();
      onSaved();
    } catch (err) {
      toast(err.message);
      setSaving(false);
    }
  }
  return html`
    <${Sheet} onClose=${onClose}>
      <h2>New person</h2>
      <div class="field">
        <label>Name</label>
        <input type="text" value=${name} onInput=${(e) => setName(e.target.value)} placeholder="e.g. Raj" autofocus />
      </div>
      <button class="btn" disabled=${!name.trim() || saving} onClick=${save}>Add person</button>
    <//>
  `;
}

export function PeoplePage() {
  const store = useStore();
  const [sheet, setSheet] = useState(null); // {person, kind} | {ledger} | {add}
  const [refresh, setRefresh] = useState(0);
  const { data } = useApi(() => api.persons(), [refresh, store.persons]);

  const persons = data?.persons || [];

  return html`
    <div class="page">
      <div class="topbar"><h1>People</h1></div>
      <div class="sumstrip">
        <div><div class="lab">Owed to you</div><div class="val pos tnum">${fmt(data?.total_owed_to_me || 0)}</div></div>
        <div><div class="lab">You owe</div><div class="val neg tnum">${fmt(data?.total_i_owe || 0)}</div></div>
        <div><div class="lab">Net</div><div class="val tnum">${fmt((data?.total_owed_to_me || 0) - (data?.total_i_owe || 0))}</div></div>
      </div>
      ${persons.length === 0 &&
      html`<${EmptyState} icon="🤝" title="No people yet"
        sub="Add a friend to track money you lend or borrow." />`}
      ${persons.map(
        (p) => html`
          <div class="card person-card" key=${p.id}>
            <button class="spread" style="width:100%; text-align:left" onClick=${() => setSheet({ ledger: p })}>
              <div>
                <div class="name">${p.name}</div>
                <div class="muted" style="font-size:12.5px">
                  ${p.last_activity ? `Last activity ${p.last_activity}` : "No activity"}
                </div>
              </div>
              <div class="net tnum ${p.net > 0 ? "pos" : p.net < 0 ? "neg" : "muted"}">
                ${p.net === 0 ? "Settled" : fmt(Math.abs(p.net))}
                ${p.net > 0 ? html`<div class="muted" style="font-size:11px; font-weight:500; text-align:right">owes you</div>`
                  : p.net < 0 ? html`<div class="muted" style="font-size:11px; font-weight:500; text-align:right">you owe</div>` : ""}
              </div>
            </button>
            <div class="person-actions">
              <button onClick=${() => setSheet({ person: p, kind: "lend" })}>Lend</button>
              <button onClick=${() => setSheet({ person: p, kind: "borrow" })}>Borrow</button>
              ${p.net > 0 && html`<button onClick=${() => setSheet({ person: p, kind: "repay_in" })}>Settle</button>`}
              ${p.net < 0 && html`<button onClick=${() => setSheet({ person: p, kind: "repay_out" })}>Settle</button>`}
            </div>
          </div>
        `
      )}
      <button class="fab" onClick=${() => setSheet({ add: true })} aria-label="Add person">+</button>
      ${sheet?.person &&
      html`<${LoanSheet} person=${sheet.person} kind=${sheet.kind} onClose=${() => setSheet(null)}
        onSaved=${() => { setSheet(null); setRefresh((r) => r + 1); }} />`}
      ${sheet?.ledger && html`<${LedgerSheet} person=${sheet.ledger} onClose=${() => setSheet(null)} />`}
      ${sheet?.add &&
      html`<${NewPersonSheet} onClose=${() => setSheet(null)}
        onSaved=${() => { setSheet(null); setRefresh((r) => r + 1); }} />`}
    </div>
  `;
}
