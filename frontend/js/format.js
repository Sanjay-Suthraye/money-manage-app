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

/* Whole rupees stay clean (₹1,250); paise show both digits (₹487.80). */
export function fmt(amount) {
  return Number.isInteger(Math.round(amount * 100) / 100) && amount % 1 === 0
    ? inrWhole.format(amount)
    : inrPaise.format(amount);
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

export function dayLabel(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  const date = new Date(y, m - 1, d);
  const wd = date.toLocaleDateString("en-IN", { weekday: "short" });
  return { day: String(d).padStart(2, "0"), weekday: wd };
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
