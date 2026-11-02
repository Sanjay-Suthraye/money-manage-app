/* Line icons (24px grid, stroke = currentColor). Paths are from the UI
   redesign; one <Icon> renders them all so stroke widths stay consistent. */

import { html } from "./html.js";

export const P = {
  chevL: "M15 6l-6 6 6 6",
  chevR: "M9 6l6 6-6 6",
  search: "M20 20l-3.5-3.5M18 11a7 7 0 1 1-14 0 7 7 0 0 1 14 0Z",
  filter: "M4 6h16M7 12h10M10 18h4",
  plus: "M12 5v14M5 12h14",
  close: "M6 6l12 12M18 6L6 18",
  check: "M5 12.5l4.5 4.5L19 7.5",
  up: "M12 19V5M6 11l6-6 6 6",
  recur: "M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7",
  gear: "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6ZM12 2v3M12 19v3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M2 12h3M19 12h3M4.9 19.1L7 17M17 7l2.1-2.1",
  sliders: "M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M16 6a2 2 0 1 0 4 0 2 2 0 0 0-4 0ZM8 12a2 2 0 1 0 4 0 2 2 0 0 0-4 0ZM16 18a2 2 0 1 0 4 0 2 2 0 0 0-4 0Z",
  history: "M3 12a9 9 0 1 0 3-6.7L3 8M3 3v5h5M12 7v5l3 2",
  pen: "M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z",
  clip: "M21 11.5l-8.6 8.6a5 5 0 0 1-7.1-7.1l8.6-8.6a3.3 3.3 0 0 1 4.7 4.7l-8.6 8.6a1.7 1.7 0 0 1-2.4-2.4l7.9-7.9",
  stop: "M7 7h10v10H7Z",
  calendar: "M4 7a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V7ZM4 10h16M8 3v4M16 3v4",
  download: "M12 4v12M7 11l5 5 5-5M5 20h14",
  logout: "M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3M10 17l5-5-5-5M15 12H4",
  sparkle: "M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8L12 3Z",
  tag: "M20 13l-7 7-9-9V4h7l9 9ZM8 8h.01",

  // category glyphs
  food: "M7 3v8a2 2 0 0 0 2 2v8M11 3v8M7 7h4M17 3c-1.7 0-3 2-3 5s1.3 4 3 4v9",
  grocery: "M5 8h14l-1.2 11.2a2 2 0 0 1-2 1.8H8.2a2 2 0 0 1-2-1.8L5 8ZM9 8V6a3 3 0 0 1 6 0v2",
  transport: "M5 16V9.5L7 5h10l2 4.5V16M5 16h14M5 16v2M19 16v2M7.5 12.5h.01M16.5 12.5h.01",
  salary: "M12 4v12M7 11l5 5 5-5M5 20h14",
  card: "M3 7a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7ZM3 10h18",
  shop: "M20 13l-7 7-9-9V4h7l9 9ZM8 8h.01",
  bills: "M6 3h12v18l-3-2-3 2-3-2-3 2V3ZM9 8h6M9 12h6",
  health: "M20.8 8.6a5 5 0 0 0-8.8-3.2A5 5 0 0 0 3.2 8.6c0 5.2 8.8 10.4 8.8 10.4s8.8-5.2 8.8-10.4Z",
  fun: "M4 6h16v11H4zM9 21h6M12 17v4M10 9.5v4l3.5-2-3.5-2Z",
  home: "M3 11l9-7 9 7M5 9.5V20h14V9.5M10 20v-5h4v5",
  study: "M3 8l9-4 9 4-9 4-9-4ZM7 10v5c0 1.5 2.2 3 5 3s5-1.5 5-3v-5",
  gift: "M4 11h16v9H4zM3 7h18v4H3zM12 7v13M12 7C10 4 7 4 7 6s3 1 5 1c2 0 5 1 5-1s-3-2-5 1",
  invest: "M3 17l6-6 4 4 8-8M15 7h6v6",
  refund: "M4 12a8 8 0 1 0 2.3-5.7M4 4v5h5",
  transfer: "M7 7h13l-4-4M17 17H4l4 4",
  travel: "M10.5 13.5L3 11l1.5-1.5 8 1 4-4.5a2.1 2.1 0 0 1 3 3l-4.5 4 1 8L14.5 22l-2.5-7.5",
  people: "M9 11.2a3.2 3.2 0 1 0 0-6.4 3.2 3.2 0 0 0 0 6.4ZM2.8 19c.7-3 3.2-4.6 6.2-4.6s5.5 1.6 6.2 4.6M17 11.4a2.4 2.4 0 1 0 0-4.8 2.4 2.4 0 0 0 0 4.8ZM15.5 14.7c2.8.1 4.9 1.5 5.6 4.1",
};

export function Icon({ d, size = 20, sw = 1.8 }) {
  return html`<svg class="ico" width=${size} height=${size} viewBox="0 0 24 24" fill="none"
    stroke="currentColor" stroke-width=${sw} stroke-linecap="round" stroke-linejoin="round"
    aria-hidden="true"><path d=${d} /></svg>`;
}

/* First match wins, so the specific words sit above the broad ones. */
const CATEGORY_RULES = [
  [/grocer|bigbasket|blinkit|zepto|instamart|supermarket|vegetable|fruit|kirana/, "grocery"],
  [/food|dining|restaurant|lunch|dinner|breakfast|swiggy|zomato|\beat|cafe|coffee|snack|\bbars?\b|drink/, "food"],
  [/fuel|petrol|diesel|uber|ola|rapido|\bcab|taxi|metro|\bauto\b|commute|transport|parking|toll|vehicle|\bcars?\b|\bbike/, "transport"],
  [/flight|travel|trip|holiday|vacation|hotel|irctc|train/, "travel"],
  [/salary|wage|payroll|stipend|bonus|income/, "salary"],
  [/refund|cashback|reimburs/, "refund"],
  [/invest|mutual|\bsip\b|stock|dividend|interest|saving|deposit|\bfd\b|\bgold/, "invest"],
  [/\brent|home|house|household|furnit|maintenance|help|repair/, "home"],
  [/bill|utilit|electric|water|gas|recharge|internet|broadband|phone|mobile|emi|insurance|tax|fee|charge/, "bills"],
  [/card/, "card"],
  [/health|medic|doctor|pharma|hospital|clinic|gym|fitness|personal care|salon/, "health"],
  [/movie|entertain|netflix|spotify|prime|hotstar|subscription|ott|game|fun|party/, "fun"],
  [/educat|school|college|course|book|tuition|class/, "study"],
  [/gift|donat|charity|festival/, "gift"],
  [/shop|amazon|flipkart|myntra|cloth|apparel|jewel|electronic|gadget|fashion/, "shop"],
  [/transfer/, "transfer"],
];

export function categoryGlyph(name) {
  const n = (name || "").toLowerCase();
  for (const [re, key] of CATEGORY_RULES) if (re.test(n)) return P[key];
  return null;
}
