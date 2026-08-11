/* Tiny shared state: the month cursor (Transactions <-> Stats) plus boot-loaded
   accounts/categories/persons caches, invalidated after any mutation. */

import { api } from "./api.js";
import { currentMonth } from "./format.js";

const listeners = new Set();

export const store = {
  month: currentMonth(),
  accounts: [],
  categories: [],
  persons: [],
  loaded: false,
};

export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function emit() {
  for (const fn of listeners) fn();
}

export function setMonth(month) {
  store.month = month;
  emit();
}

export async function refreshRefData() {
  const [accounts, categories, persons] = await Promise.all([
    api.accounts(),
    api.categories(),
    api.persons(),
  ]);
  store.accounts = accounts;
  store.categories = categories;
  store.persons = persons.persons;
  store.loaded = true;
  emit();
}

/* Call after any write so every page refetches fresh data on next render. */
export function invalidate() {
  refreshRefData().catch(() => {});
}
