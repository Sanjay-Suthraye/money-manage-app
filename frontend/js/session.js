/* Sign-out and account switching.

   The device remembers which usernames have signed in on it — usernames only,
   never passwords — so the login screen can offer them and Settings can offer
   "Switch to …". Storage can be blocked or cleared; every reader falls back to
   an empty list and the plain login form still works. */

import { api } from "./api.js";

const ACCOUNTS_KEY = "mm.accounts";
const SWITCH_KEY = "mm.switchTo";
const MAX_ACCOUNTS = 5;

export function knownAccounts() {
  try {
    const list = JSON.parse(localStorage.getItem(ACCOUNTS_KEY) || "[]");
    return Array.isArray(list) ? list.filter((u) => typeof u === "string" && u) : [];
  } catch {
    return [];
  }
}

function saveAccounts(list) {
  try {
    localStorage.setItem(ACCOUNTS_KEY, JSON.stringify(list.slice(0, MAX_ACCOUNTS)));
  } catch { /* storage blocked */ }
}

/* Most recent first. */
export function rememberAccount(username) {
  if (username) saveAccounts([username, ...knownAccounts().filter((u) => u !== username)]);
}

export function forgetAccount(username) {
  saveAccounts(knownAccounts().filter((u) => u !== username));
}

/* The username a "Switch to …" asked the login screen to prefill; read once. */
export function takeSwitchTarget() {
  try {
    const name = sessionStorage.getItem(SWITCH_KEY);
    sessionStorage.removeItem(SWITCH_KEY);
    return name;
  } catch {
    return null;
  }
}

/* Log out and reload into the login screen, prefilled with `switchTo` if given.
   The reload wipes module-level caches (chat transcript, ref data) so nothing
   of this user's data reaches the next one. */
export async function signOut(switchTo = null) {
  try {
    await api.logout();
  } catch { /* log out locally regardless */ }
  if (switchTo) {
    try {
      sessionStorage.setItem(SWITCH_KEY, switchTo);
    } catch { /* storage blocked: the login form just starts empty */ }
  }
  location.replace("/");
}
