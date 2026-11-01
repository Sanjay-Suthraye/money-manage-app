import { html, useState } from "../html.js";
import { api } from "../api.js";
import { fmt, shortDate, todayISO } from "../format.js";
import { Icon, P } from "../icons.js";
import { invalidate } from "../store.js";
import { AmountInput, Chips, DateField, EmptyState, Sheet, slotColor, toast, useApi, useStore } from "../components/ui.js";

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
    <${Sheet} title=${titles[kind]} onClose=${onClose}>
      <${AmountInput} hero value=${amount} onChange=${setAmount} currency="INR" autofocus=${!prefill} />
      <div class="field">
        <div class="field-label">${accountLabels[kind]}</div>
        <${Chips} items=${accounts} value=${accountId} onChange=${setAccountId} />
      </div>
      <div class="field">
        <div class="field-label">Date</div>
        <${DateField} value=${date} onChange=${setDate} />
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
      <div class="sheet-head">
        <h2 class="ellipsis">${person.name}</h2>
        <div class="right">
          <div class="tnum ${data?.net > 0 ? "pos" : ""}" style="font-weight:700; font-size:17px">
            ${data ? (data.net === 0 ? "Settled" : fmt(Math.abs(data.net))) : ""}
          </div>
          <div class="muted" style="font-size:12px">${data?.net > 0 ? "owes you" : data?.net < 0 ? "you owe" : ""}</div>
        </div>
      </div>
      <div>
        ${(data?.entries || []).map(
          (e) => html`
            <div class="ledger-row" key=${e.id}>
              <div>
                <div style="font-weight:500">${KIND_LABELS[e.type]}${e.note ? ` · ${e.note}` : ""}</div>
                <div class="muted" style="font-size:12.5px">${shortDate(e.date)}</div>
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
    <${Sheet} title="New person" onClose=${onClose}>
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
      <div class="topbar">
        <h1>People</h1>
        <button class="iconbtn" onClick=${() => setSheet({ add: true })} aria-label="Add person">
          <${Icon} d=${P.plus} size=${19} sw=${2} />
        </button>
      </div>
      <div class="stat-tiles">
        <div class="stat-tile">
          <div class="lab">Owed to you</div>
          <div class="val pos tnum">${fmt(data?.total_owed_to_me || 0)}</div>
        </div>
        <div class="stat-tile">
          <div class="lab">You owe</div>
          <div class="val tnum">${fmt(data?.total_i_owe || 0)}</div>
        </div>
      </div>
      ${persons.length === 0 &&
      html`<${EmptyState} icon="🤝" title="No people yet"
        sub="Add a friend to track money you lend or borrow." />`}
      ${persons.map(
        (p) => html`
          <div class="card person-card" key=${p.id}>
            <button class="person-head" onClick=${() => setSheet({ ledger: p })}>
              <span class=${`tile round ${p.net === 0 ? "neutral" : ""}`} style=${`--c:${slotColor(p.id)}`} aria-hidden="true">
                ${p.name[0].toUpperCase()}
              </span>
              <span class="grow">
                <span class="name ellipsis" style="display:block">${p.name}</span>
                <span class="sub" style="display:block">
                  ${p.last_activity ? `Last entry · ${shortDate(p.last_activity)}` : "No entries yet"}
                </span>
              </span>
              <span class="right">
                <span class="net tnum ${p.net > 0 ? "pos" : p.net === 0 ? "muted" : ""}" style="display:block">
                  ${p.net === 0 ? "Settled" : fmt(Math.abs(p.net))}
                </span>
                <span class="dir" style="display:block">${p.net > 0 ? "owes you" : p.net < 0 ? "you owe" : " "}</span>
              </span>
            </button>
            <div class="person-actions">
              <button onClick=${() => setSheet({ person: p, kind: "lend" })}>Lend</button>
              <button onClick=${() => setSheet({ person: p, kind: "borrow" })}>Borrow</button>
              <button class="settle" disabled=${p.net === 0}
                onClick=${() => setSheet({ person: p, kind: p.net > 0 ? "repay_in" : "repay_out" })}>Settle</button>
            </div>
          </div>
        `
      )}
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
