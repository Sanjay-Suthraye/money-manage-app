/* Settings: AI entries, categories, recurring rules, data export, sign out.

   Absorbs the export link and the "signed in as" footer that used to sit at the
   bottom of the Accounts page, so there is exactly one settings place.

   On the credit UI: the hero is ONE number, never a points-to-entries
   conversion, and the bucket breakdown stays collapsed unless there is
   something a user actually needs to know (earned credits have an expiry date;
   free and purchased do not). The meter is framed as consumption rather than
   depletion — at 4 of 100 used it should read "barely touched", not "96 left
   and falling". A counter that feels like it is draining makes people ration
   the feature and log less, which is the opposite of the point. */

import { api } from "../api.js";
import { shortDate } from "../format.js";
import { html, useState } from "../html.js";
import { Sheet, toast, useApi } from "../components/ui.js";
import { Icon, P } from "../icons.js";
import { ManageCategoriesSheet } from "./categories.js";
import { RecurringSheet } from "./recurring.js";
import { setCredits, store } from "../store.js";

function CreditsSheet({ data, onClose, onChanged }) {
  const balance = data.balance;
  const used = data.usage.charged;
  const pct = data.free_grant ? Math.min(used / data.free_grant, 1) : 0;
  const [busy, setBusy] = useState(false);
  const showBuckets = balance.earned + balance.purchased > 0;

  async function topUp() {
    setBusy(true);
    try {
      const result = await api.creditsGoodwill();
      setCredits(result.balance);
      toast(`+${result.granted} AI entries added`);
      onChanged();
    } catch (err) {
      toast(err.info?.message || err.message, { duration: 4000 });
    } finally {
      setBusy(false);
    }
  }

  return html`
    <${Sheet} title="AI entries" onClose=${onClose}>

      <div class="credit-hero">
        <div class="credit-num tnum">${balance.total}</div>
        <div class="muted" style="font-size:13px">left · used by smart add and chat</div>
      </div>

      <div class="bar" style="margin:14px 0 6px">
        <div class="fill" style=${`width:${pct * 100}%`}></div>
      </div>
      <div class="spread muted" style="font-size:12.5px">
        <span>${used} used this month</span>
        <span>${data.free_grant} free every month</span>
      </div>

      <div class="muted" style="font-size:12.5px; margin-top:12px; line-height:1.5">
        Free entries refresh on ${shortDate(data.resets_on)} — they don't carry over.
        Typing entries in yourself is always free and always works, even at zero.
      </div>

      ${showBuckets &&
      html`<div class="field" style="margin-top:18px">
        <div class="field-label">Where they came from</div>
        <div class="credit-bucket"><span>Free this month</span><b class="tnum">${balance.free}</b></div>
        ${balance.earned > 0 &&
        html`<div class="credit-bucket">
          <span>
            Earned${data.earned_expires_on
              ? html` <span class="muted">· expires ${shortDate(data.earned_expires_on)}</span>`
              : ""}
          </span>
          <b class="tnum">${balance.earned}</b>
        </div>`}
        ${balance.purchased > 0 &&
        html`<div class="credit-bucket">
          <span>Purchased <span class="muted">· never expire</span></span>
          <b class="tnum">${balance.purchased}</b>
        </div>`}
      </div>`}

      ${data.earn.goodwill_available
        ? html`<button class="btn ghost" style="margin-top:16px" disabled=${busy} onClick=${topUp}>
            ${data.earn.available ? `Watch an ad for ${data.earn.goodwill_credits} more` : `Get ${data.earn.goodwill_credits} more entries`}
          </button>`
        : html`<div class="muted" style="font-size:12.5px; margin-top:16px">
            No more top-ups available today — free entries refresh ${shortDate(data.resets_on)}.
          </div>`}

      ${data.usage.calls > 0 &&
      html`<div class="muted" style="font-size:12px; margin-top:14px">
        ${data.usage.calls} AI call${data.usage.calls === 1 ? "" : "s"} this month${
          data.usage.refunded > 0 ? `, ${data.usage.refunded} refunded` : ""
        }.
      </div>`}
    <//>
  `;
}

export function SettingsSheet({ onClose }) {
  const [refresh, setRefresh] = useState(0);
  const [creditsOpen, setCreditsOpen] = useState(false);
  const [recurringOpen, setRecurringOpen] = useState(false);
  const [categoriesOpen, setCategoriesOpen] = useState(false);
  const { data: credits } = useApi(() => api.credits(), [refresh]);

  return html`
    <${Sheet} title="Settings" onClose=${onClose}>
      <div class="field">
        <div class="field-label">AI</div>
        <div class="list-group">
          <button class="list-row" disabled=${!credits} onClick=${() => credits && setCreditsOpen(true)}>
            <span class="tile" style="--c:var(--series-1)"><${Icon} d=${P.sparkle} size=${18} /></span>
            <div class="grow">
              <div class="ttl">AI entries</div>
              <div class="sub">
                ${credits
                  ? `${credits.balance.total} left · refreshes ${shortDate(credits.resets_on)}`
                  : "…"}
              </div>
            </div>
            <${Icon} d=${P.chevR} size=${16} sw=${2} />
          </button>
        </div>
      </div>

      <div class="field">
        <div class="field-label">Money</div>
        <div class="list-group">
          <button class="list-row" onClick=${() => setCategoriesOpen(true)}>
            <span class="tile" style="--c:var(--series-2)"><${Icon} d=${P.tag} size=${18} /></span>
            <div class="grow">
              <div class="ttl">Categories</div>
              <div class="sub">Add, rename, recolor, merge</div>
            </div>
            <${Icon} d=${P.chevR} size=${16} sw=${2} />
          </button>
          <button class="list-row" onClick=${() => setRecurringOpen(true)}>
            <span class="tile" style="--c:var(--series-4)"><${Icon} d=${P.recur} size=${18} /></span>
            <div class="grow">
              <div class="ttl">Recurring rules</div>
              <div class="sub">Rent, SIPs, subscriptions</div>
            </div>
            <${Icon} d=${P.chevR} size=${16} sw=${2} />
          </button>
          <a class="list-row" href="/api/export.xlsx">
            <span class="tile" style="--c:var(--series-6)"><${Icon} d=${P.download} size=${18} /></span>
            <div class="grow">
              <div class="ttl">Download Excel export</div>
              <div class="sub">Every account and transaction</div>
            </div>
            <${Icon} d=${P.chevR} size=${16} sw=${2} />
          </a>
        </div>
      </div>

      <div class="signedin muted">
        signed in as <b>${store.username || "…"}</b> ·${" "}
        <button
          class="linkbtn"
          onClick=${async () => {
            try {
              await api.logout();
            } catch {
              /* log out locally regardless */
            }
            location.reload();
          }}
        >
          Log out
        </button>
      </div>

      ${categoriesOpen && html`<${ManageCategoriesSheet} onClose=${() => setCategoriesOpen(false)} />`}
      ${recurringOpen && html`<${RecurringSheet} onClose=${() => setRecurringOpen(false)} />`}
      ${creditsOpen &&
      credits &&
      html`<${CreditsSheet}
        data=${credits}
        onClose=${() => setCreditsOpen(false)}
        onChanged=${() => setRefresh((r) => r + 1)}
      />`}
    <//>
  `;
}
