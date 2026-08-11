/* Chart.js theming from the CSS tokens: charts follow light/dark automatically.
   Series colors come from the fixed categorical slots (color follows the
   category, never its rank). */

import { fmt, fmtCompact } from "./format.js";

export function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export function seriesColor(slot) {
  return cssVar(`--series-${(slot % 8) + 1}`);
}

export function applyChartDefaults() {
  const { Chart } = window;
  if (!Chart) return;
  Chart.defaults.font.family = 'system-ui, -apple-system, "Segoe UI", sans-serif';
  Chart.defaults.font.size = 11.5;
  Chart.defaults.color = cssVar("--muted");
  Chart.defaults.borderColor = cssVar("--grid");
  Chart.defaults.plugins.legend.display = false;
  Chart.defaults.plugins.tooltip.backgroundColor = cssVar("--ink");
  Chart.defaults.plugins.tooltip.titleColor = cssVar("--page");
  Chart.defaults.plugins.tooltip.bodyColor = cssVar("--page");
  Chart.defaults.plugins.tooltip.cornerRadius = 8;
  Chart.defaults.plugins.tooltip.padding = 10;
  Chart.defaults.animation.duration = 250;
}

export const inrTick = (value) => fmtCompact(value);
export const inrTooltip = (ctx) =>
  `${ctx.dataset.label ? ctx.dataset.label + ": " : ""}${fmt(ctx.parsed.y ?? ctx.parsed)}`;

/* Watch theme changes and re-render registered charts. */
export function onThemeChange(callback) {
  const mq = window.matchMedia("(prefers-color-scheme: dark)");
  mq.addEventListener("change", callback);
  return () => mq.removeEventListener("change", callback);
}
