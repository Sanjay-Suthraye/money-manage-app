/* Natural-language quick add: a composer docked above the tab bar that turns
   what you type into a tappable confirmation chip.

   It sits beside the + button rather than inside TxSheet on purpose — the whole
   point is that most entries never open a form. The + still opens the full
   sheet, so nothing regresses for anyone who prefers it.

   Nothing is saved until the tick is pressed. Fields the server flagged as
   low-confidence render underlined, and anything genuinely missing blocks Save —
   that is what makes a wrong guess cheap instead of dangerous. Every field the
   user edits is reported back on confirm, which is what teaches the cache. */

import { api } from "../api.js";
import { fmt, todayISO } from "../format.js";
import { Icon, P } from "../icons.js";
import { html, useState } from "../html.js";
import { invalidate, setCredits } from "../store.js";
import { AmountInput, Chips, DateField, Seg, toast, useStore } from "./ui.js";

function Part({ label, guess, missing, open, onClick }) {
  const cls = ["chip-seg", guess && "guess", missing && "missing", open && "open"]
    .filter(Boolean)
    .join(" ");
  return html`<button class=${cls} onClick=${onClick}>${label}</button>`;
}

function ConfirmChip({ parsed, saving, onSave, onDismiss }) {
  const { accounts, categories } = useStore();
  const [draft, setDraft] = useState(parsed.draft);
  const [corrected, setCorrected] = useState([]);
  const [open, setOpen] = useState(null);

  const low = new Set(parsed.low_confidence || []);
  const need = new Set(parsed.needs || []);
  const ambiguous = new Set(parsed.ambiguous_fields || []);

  /* Which fields the user touched IS the training signal — it goes to the
     server on confirm and becomes a tier-2 correction. */
  function set(field, patch) {
    setDraft((d) => ({ ...d, ...patch }));
    setCorrected((c) => (c.includes(field) ? c : [...c, field]));
    setOpen(null);
  }

  const cats = categories.filter((c) => c.kind === draft.type);
  const all = categories.flatMap((c) => [c, ...(c.children || [])]);
  const cat = all.find((c) => c.id === draft.category_id);
  const acct = accounts.find((a) => a.id === draft.account_id);
  const toAcct = accounts.find((a) => a.id === draft.to_account_id);
  const parent = cats.find(
    (c) => c.id === draft.category_id || (c.children || []).some((k) => k.id === draft.category_id)
  );

  const isTransfer = draft.type === "transfer";
  const blocked =
    saving ||
    need.size > 0 ||
    !(Number(draft.amount) > 0) ||
    !draft.account_id ||
    (isTransfer && !draft.to_account_id);

  const isGuess = (name) => low.has(name) || ambiguous.has(name);

  return html`
    <div class="confirm">
      ${parsed.source !== "rules" &&
      html`<div class="confirm-tier">
        ${parsed.source === "rules+memory" ? "learned" : parsed.source === "llm" ? "AI" : "check this"}
      </div>`}
      <div class="confirm-row">
        <div class="confirm-parts">
        <${Part}
          label=${draft.amount ? fmt(Number(draft.amount)) : "Amount?"}
          guess=${isGuess("amount")}
          missing=${need.has("amount")}
          open=${open === "amount"}
          onClick=${() => setOpen(open === "amount" ? null : "amount")}
        />
        ${isTransfer
          ? html`<${Part}
              label=${toAcct ? `to ${toAcct.name}` : "To?"}
              guess=${isGuess("to_account")}
              missing=${need.has("to_account")}
              open=${open === "to_account"}
              onClick=${() => setOpen(open === "to_account" ? null : "to_account")}
            />`
          : html`<${Part}
              label=${cat ? cat.name : "Category?"}
              guess=${isGuess("category")}
              missing=${need.has("category")}
              open=${open === "category"}
              onClick=${() => setOpen(open === "category" ? null : "category")}
            />`}
        <${Part}
          label=${acct ? acct.name : "Account?"}
          guess=${isGuess("account")}
          missing=${need.has("account")}
          open=${open === "account"}
          onClick=${() => setOpen(open === "account" ? null : "account")}
        />
        <${Part}
          label=${parsed.fields.date.label || draft.date}
          guess=${isGuess("date")}
          missing=${need.has("date")}
          open=${open === "date"}
          onClick=${() => setOpen(open === "date" ? null : "date")}
        />
        </div>
        <div class="confirm-actions">
          <button class="confirm-x" onClick=${onDismiss} aria-label="Discard">
            <${Icon} d=${P.close} size=${16} sw=${2.2} />
          </button>
          <button class="confirm-ok" disabled=${blocked} onClick=${() => onSave(draft, corrected)} aria-label="Save">
            <${Icon} d=${P.check} size=${17} sw=${2.4} />
          </button>
        </div>
      </div>

      ${open === "amount" &&
      html`<div class="confirm-edit">
        <${AmountInput}
          value=${String(draft.amount ?? "")}
          currency="INR"
          autofocus
          onChange=${(v) => setDraft((d) => ({ ...d, amount: v }))}
        />
        <button class="btn" onClick=${() => set("amount", { amount: Number(draft.amount) || 0 })}>Done</button>
      </div>`}

      ${open === "category" &&
      html`<div class="confirm-edit">
        <${Seg}
          options=${[
            { value: "expense", label: "Expense" },
            { value: "income", label: "Income" },
          ]}
          value=${draft.type}
          onChange=${(v) => set("type", { type: v, category_id: null })}
        />
        <div style="margin-top:8px">
          <${Chips}
            items=${cats}
            value=${parent?.id}
            allowNone
            onChange=${(id) => set("category", { category_id: id })}
          />
        </div>
        ${parent?.children?.length > 0 &&
        html`<div style="margin-top:8px">
          <${Chips}
            items=${parent.children}
            value=${draft.category_id !== parent.id ? draft.category_id : null}
            allowNone
            onChange=${(id) => set("category", { category_id: id ?? parent.id })}
          />
        </div>`}
      </div>`}

      ${open === "account" &&
      html`<div class="confirm-edit">
        <${Chips}
          items=${accounts.filter((a) => a.id !== draft.to_account_id)}
          value=${draft.account_id}
          onChange=${(id) => set("account", { account_id: id })}
        />
      </div>`}

      ${open === "to_account" &&
      html`<div class="confirm-edit">
        <${Chips}
          items=${accounts.filter((a) => a.id !== draft.account_id)}
          value=${draft.to_account_id}
          onChange=${(id) => set("to_account", { to_account_id: id })}
        />
      </div>`}

      ${open === "date" &&
      html`<div class="confirm-edit">
        <div class="row">
          <button
            class=${`chip ${draft.date === todayISO() ? "sel" : ""}`}
            onClick=${() => set("date", { date: todayISO() })}
          >
            Today
          </button>
          <div class="grow"><${DateField} value=${draft.date} onChange=${(v) => set("date", { date: v })} /></div>
        </div>
      </div>`}
    </div>
  `;
}

export function QuickAdd({ onSaved, onManual }) {
  const [text, setText] = useState("");
  const [parsed, setParsed] = useState(null);
  const [busy, setBusy] = useState(false); // false | "parsing" | "saving"
  const [slow, setSlow] = useState(false);

  async function submit(e) {
    e?.preventDefault();
    const value = text.trim();
    if (!value || busy) return;
    setBusy("parsing");
    /* Only say "thinking" once it is actually slow — a tier-1 hit returns in
       single-digit milliseconds and a spinner would just make it feel slower. */
    const timer = setTimeout(() => setSlow(true), 400);
    try {
      const result = await api.quickAddParse(value);
      setParsed(result);
      if (result.credits) setCredits(result.credits);
      if (result.message) toast(result.message, { duration: 3500 });
    } catch (err) {
      toast(err.message);
    } finally {
      clearTimeout(timer);
      setSlow(false);
      setBusy(false);
    }
  }

  async function save(draft, corrected) {
    setBusy("saving");
    try {
      await api.quickAddConfirm({
        request_id: parsed.request_id,
        draft,
        merchant_key: parsed.merchant_key,
        phrase_key: parsed.phrase_key,
        corrected_fields: corrected,
        create_person: parsed.fields.person.source === "new" ? parsed.fields.person.label : null,
        source: parsed.source,
      });
      invalidate();
      toast(`Saved · ${fmt(Number(draft.amount))}`);
      setParsed(null);
      setText("");
      onSaved?.();
    } catch (err) {
      /* Keep the chip open: a 422 or a 409 on a globally-unique person name
         must not throw away what the user typed. */
      toast(err.message, { duration: 4000 });
    } finally {
      setBusy(false);
    }
  }

  return html`
    <div class="dock">
      <div class="dock-inner">
        ${parsed &&
        html`<${ConfirmChip}
          key=${parsed.request_id}
          parsed=${parsed}
          saving=${busy === "saving"}
          onSave=${save}
          onDismiss=${() => setParsed(null)}
        />`}
        <form class="nlbar" onSubmit=${submit}>
          <button type="button" class="dock-add" onClick=${onManual} aria-label="Add manually">
            <${Icon} d=${P.plus} size=${22} sw=${2.2} />
          </button>
          <label class="composer-field" style="align-items:center">
            <span class="sr-only">Quick add</span>
            <input
              type="text"
              placeholder="450 swiggy yesterday"
              value=${text}
              enterkeyhint="done"
              onInput=${(e) => setText(e.target.value)}
            />
            ${busy === "parsing"
              ? html`<span class="nlbar-busy">${slow ? "thinking…" : "···"}</span>`
              : html`<button class="sendbtn" type="submit" disabled=${!text.trim()} aria-label="Parse">
                  <${Icon} d=${P.up} size=${16} sw=${2.2} />
                </button>`}
          </label>
        </form>
      </div>
    </div>
  `;
}
