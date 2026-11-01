const inrPaise = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const inrWhole = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 0,
});

/* Whole rupees stay clean (₹1,250); paise show both digits (₹487.80).
   Negatives get a true minus sign (−₹450), matching the signed list amounts. */
export function fmt(amount) {
  const out = Number.isInteger(Math.round(amount * 100) / 100) && amount % 1 === 0
    ? inrWhole.format(amount)
    : inrPaise.format(amount);
  return out.replace("-", "−");
}

export function fmtWhole(amount) {
  return inrWhole.format(Math.round(amount));
}

/* Compact Indian notation for axis ticks: 1.2k, 3.4L, 1.1Cr */
export function fmtCompact(amount) {
  const abs = Math.abs(amount);
  const sign = amount < 0 ? "-" : "";
  if (abs >= 1e7) return `${sign}₹${(abs / 1e7).toFixed(1).replace(/\.0$/, "")}Cr`;
  if (abs >= 1e5) return `${sign}₹${(abs / 1e5).toFixed(1).replace(/\.0$/, "")}L`;
  if (abs >= 1e3) return `${sign}₹${(abs / 1e3).toFixed(1).replace(/\.0$/, "")}k`;
  return `${sign}₹${Math.round(abs)}`;
}

export function todayISO() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export function currentMonth() {
  return todayISO().slice(0, 7);
}

export function shiftMonth(month, delta) {
  const [y, m] = month.split("-").map(Number);
  const idx = y * 12 + (m - 1) + delta;
  return `${String(Math.floor(idx / 12)).padStart(4, "0")}-${String((idx % 12) + 1).padStart(2, "0")}`;
}

export function monthLabel(month) {
  const [y, m] = month.split("-").map(Number);
  return new Date(y, m - 1, 1).toLocaleDateString("en-IN", { month: "long", year: "numeric" });
}

export function monthBounds(month) {
  const [y, m] = month.split("-").map(Number);
  const last = new Date(y, m, 0).getDate();
  return [`${month}-01`, `${month}-${String(last).padStart(2, "0")}`];
}

export function shortMonth(month) {
  const [y, m] = month.split("-").map(Number);
  return new Date(y, m - 1, 1).toLocaleDateString("en-IN", { month: "short" });
}

/* Money Manager categories are emoji-prefixed ("🪑 Household", "🏠Amit home").
   Split off a leading emoji grapheme (skin tones/ZWJ included) as the icon. */
const segmenter = typeof Intl !== "undefined" && Intl.Segmenter
  ? new Intl.Segmenter(undefined, { granularity: "grapheme" })
  : null;

export function splitCategoryName(name) {
  if (!name) return { icon: null, label: "" };
  const first = segmenter
    ? segmenter.segment(name)[Symbol.iterator]().next().value?.segment
    : Array.from(name)[0];
  if (first && /\p{Extended_Pictographic}/u.test(first)) {
    return { icon: first, label: name.slice(first.length).trim() || name };
  }
  return { icon: null, label: name };
}

/* "2026-09-08" -> "8 Sep" */
export function shortDate(iso) {
  const d = new Date(`${iso}T00:00:00`);
  return d.toLocaleDateString("en-IN", { day: "numeric", month: "short" });
}

/* "2026-10-31" -> "Sat, 31 Oct" (year added when it isn't this year) */
export function dayHeading(iso) {
  const d = new Date(`${iso}T00:00:00`);
  const sameYear = d.getFullYear() === new Date().getFullYear();
  const wd = d.toLocaleDateString("en-IN", { weekday: "short" });
  const dm = d.toLocaleDateString("en-IN", { day: "numeric", month: "short", ...(sameYear ? {} : { year: "numeric" }) });
  return `${wd}, ${dm}`;
}

/* Date-field label: "Today, 1 Nov" · "Yesterday, 31 Oct" · "Fri, 30 Oct" */
export function friendlyDate(iso) {
  if (!iso) return "Pick a date";
  const today = todayISO();
  const y = new Date(`${today}T00:00:00`);
  y.setDate(y.getDate() - 1);
  const yesterday = `${y.getFullYear()}-${String(y.getMonth() + 1).padStart(2, "0")}-${String(y.getDate()).padStart(2, "0")}`;
  if (iso === today) return `Today, ${shortDate(iso)}`;
  if (iso === yesterday) return `Yesterday, ${shortDate(iso)}`;
  return dayHeading(iso);
}

/* Signed money for lists: "+₹1,200" / "−₹450" (true minus sign, not a hyphen). */
export function fmtSigned(amount) {
  if (!amount) return fmt(0);
  return `${amount > 0 ? "+" : "−"}${fmt(Math.abs(amount))}`;
}
