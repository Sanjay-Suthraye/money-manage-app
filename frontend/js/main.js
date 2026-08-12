import { html, render, useEffect, useState } from "./html.js";
import { api, setUnauthorizedHandler } from "./api.js";
import { refreshRefData } from "./store.js";
import { ToastHost } from "./components/ui.js";
import { TransactionsPage } from "./pages/transactions.js";
import { StatsPage } from "./pages/stats.js";
import { AccountsPage } from "./pages/accounts.js";
import { PeoplePage } from "./pages/people.js";
import { ChatPage } from "./pages/chat.js";

const I = {
  list: html`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M8 6h13M8 12h13M8 18h13M3.5 6h.01M3.5 12h.01M3.5 18h.01"/></svg>`,
  chart: html`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M4 20V10M10 20V4M16 20v-6M22 20H2"/></svg>`,
  wallet: html`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z"/><path d="M16 12h.01M3 9h13"/></svg>`,
  people: html`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="9" cy="8" r="3.2"/><path d="M2.8 19c.7-3 3.2-4.6 6.2-4.6s5.5 1.6 6.2 4.6"/><circle cx="17" cy="9" r="2.4"/><path d="M15.5 14.7c2.8.1 4.9 1.5 5.6 4.1"/></svg>`,
  chat: html`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a8 8 0 0 1-8 8H4l2.4-2.9A8 8 0 1 1 21 12Z"/></svg>`,
};

const TABS = [
  { id: "transactions", label: "Transactions", icon: I.list, component: TransactionsPage },
  { id: "stats", label: "Stats", icon: I.chart, component: StatsPage },
  { id: "accounts", label: "Accounts", icon: I.wallet, component: AccountsPage },
  { id: "people", label: "People", icon: I.people, component: PeoplePage },
  { id: "chat", label: "Chat", icon: I.chat, component: ChatPage },
];

function currentTab() {
  const hash = location.hash.replace(/^#\/?/, "");
  return TABS.some((t) => t.id === hash) ? hash : "transactions";
}

function App() {
  const [tab, setTab] = useState(currentTab());

  useEffect(() => {
    refreshRefData().catch(() => {});
    const onHash = () => setTab(currentTab());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const active = TABS.find((t) => t.id === tab);

  return html`
    <${active.component} />
    <nav class="tabbar">
      ${TABS.map(
        (t) => html`
          <button
            class=${t.id === tab ? "active" : ""}
            onClick=${() => { location.hash = `#/${t.id}`; }}
          >
            ${t.icon}
            <span>${t.label}</span>
          </button>
        `
      )}
    </nav>
    <${ToastHost} />
  `;
}

function LoginView({ onSuccess }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.login(username.trim(), password);
      onSuccess();
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  }

  return html`
    <div class="login-page">
      <form class="login-card" onSubmit=${submit}>
        <h1>₹ Money</h1>
        <div class="field">
          <label>Username</label>
          <input
            type="text"
            autocomplete="username"
            autocapitalize="none"
            value=${username}
            onInput=${(e) => setUsername(e.target.value)}
          />
        </div>
        <div class="field">
          <label>Password</label>
          <input
            type="password"
            autocomplete="current-password"
            value=${password}
            onInput=${(e) => setPassword(e.target.value)}
          />
        </div>
        ${error && html`<div class="login-error">${error}</div>`}
        <button class="btn" type="submit" disabled=${busy || !username.trim() || !password}>
          ${busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  `;
}

/* Gate: ask the server whether auth is on and whether the session cookie is
   valid; locally (no credentials configured) this resolves straight to the app.
   A 401 later — session expiry — swaps the app back for the login view; chat
   history survives via the module-level savedMessages in pages/chat.js. */
function Root() {
  const [auth, setAuth] = useState("checking"); // checking | login | ready

  useEffect(() => {
    setUnauthorizedHandler(() => setAuth("login"));
    api
      .authMe()
      .then((s) => setAuth(!s.auth_enabled || s.authenticated ? "ready" : "login"))
      .catch(() => setAuth("ready")); // /me unreachable → let App surface errors
  }, []);

  if (auth === "checking") return null;
  if (auth === "login") return html`<${LoginView} onSuccess=${() => setAuth("ready")} />`;
  return html`<${App} />`;
}

render(html`<${Root} />`, document.getElementById("app"));
