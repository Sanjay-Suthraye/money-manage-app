import { html, useEffect, useMemo, useRef, useState } from "../html.js";
import { api } from "../api.js";
import { fmt, fmtCompact, fmtWhole, monthBounds, monthLabel, shiftMonth, shortMonth, splitCategoryName } from "../format.js";
import { setMonth } from "../store.js";
import { AmountInput, Chips, EmptyState, MonthNav, Seg, Sheet, slotColor, toast, useApi, useStore } from "../components/ui.js";
import { applyChartDefaults, cssVar, inrTick, onThemeChange } from "../chartTheme.js";
import { Icon, P } from "../icons.js";
import { TxEditor, TxRow } from "./transactions.js";
import { ManageCategoriesSheet } from "./categories.js";

const MAX_SLICES = 7; // beyond this, fold into "Other" (8th slot)

function useChart(buildConfig, deps) {
  const canvasRef = useRef(null);
  const chartRef = useRef(null);
  const [theme, setTheme] = useState(0);

  useEffect(() => onThemeChange(() => setTheme((t) => t + 1)), []);

  useEffect(() => {
    if (!canvasRef.current || !window.Chart) return;
    applyChartDefaults();
    const config = buildConfig();
    if (!config) return;
    chartRef.current = new window.Chart(canvasRef.current, config);
    return () => chartRef.current?.destroy();
  }, [...deps, theme]);

  return canvasRef;
}

/* ---- Category donut: max 7 slices + Other, center total, legend beside it ---- */
function CategoryDonut({ data, kind, onPick }) {
  const slices = useMemo(() => {
    if (!data?.items?.length) return [];
    const top = data.items.slice(0, MAX_SLICES);
    const rest = data.items.slice(MAX_SLICES);
    // Slot colors are stable per category, but with more categories than slots
    // two displayed slices can collide. First occurrence keeps its slot; a
    // duplicate takes the next unused slot so no two slices share a hue.
    const used = new Set();
    const result = top.map((item) => {
      let slot = item.color_slot % 8;
      while (used.has(slot)) slot = (slot + 1) % 8;
      used.add(slot);
      return { ...item, color_slot: slot };
    });
    if (rest.length) {
      let slot = 7;
      while (used.has(slot)) slot = (slot + 7) % 8;
      result.push({
        category_id: null,
        name: "Other",
        color_slot: slot,
        total: rest.reduce((sum, item) => sum + item.total, 0),
        share: rest.reduce((sum, item) => sum + item.share, 0),
        children: [],
        _rest: rest,
      });
    }
    return result;
  }, [data]);

  if (!slices.length) return null;

  // SVG ring: one stroked circle per slice, a 2.5-unit surface gap between.
  const R = 60;
  const C = 2 * Math.PI * R;
  let acc = 0;
  const arcs = slices.map((s) => {
    const len = s.share * C;
    const arc = {
      ...s,
      dash: `${slices.length > 1 ? Math.max(len - 2.5, 0) : C} ${C}`,
      offset: -acc,
    };
    acc += len;
    return arc;
  });

  return html`
    <div class="card donut-card">
      <div class="donut">
        <svg width="140" height="140" viewBox="0 0 140 140" role="img"
          aria-label=${`${kind === "income" ? "Income" : "Spending"} by category: ${slices.map((s) => `${s.name} ${Math.round(s.share * 100)}%`).join(", ")}`}>
          ${arcs.map(
            (a) => html`
              <circle key=${a.name} cx="70" cy="70" r=${R} fill="none" stroke-width="14"
                style=${`stroke:${slotColor(a.color_slot)}; cursor:pointer`}
                stroke-dasharray=${a.dash} stroke-dashoffset=${a.offset} transform="rotate(-90 70 70)"
                onClick=${() => onPick(a)}>
                <title>${a.name}: ${fmt(a.total)}</title>
              </circle>
            `
          )}
        </svg>
        <div class="center">
          <div class="lab">Total</div>
          <div class="val tnum">${fmtWhole(data.total)}</div>
        </div>
      </div>
      <div class="legend">
        ${slices.map(
          (s) => html`
            <button class="legend-row" key=${s.name} onClick=${() => onPick(s)} title=${fmt(s.total)}>
              <span class="swatch" style=${`background:${slotColor(s.color_slot)}`}></span>
              <span class="nm ellipsis">${s.name}</span>
              <span class="pct tnum">${Math.round(s.share * 100)}%</span>
            </button>
          `
        )}
      </div>
    </div>
  `;
}

/* ---- Last 6 months: one bar per month for the selected kind; the month
   being viewed is the accent, the rest are context gray. ---- */
function MonthBars({ months, kind, month }) {
  if (!months?.length) return null;
  const vals = months.map((m) => ({ month: m.month, v: kind === "income" ? m.income : m.expense }));
  const max = Math.max(...vals.map((x) => x.v), 1);
  const avg = vals.reduce((sum, x) => sum + x.v, 0) / vals.length;
  const cur = vals.some((x) => x.month === month) ? month : vals[vals.length - 1].month;
  return html`
    <div class="card">
      <div class="spread">
        <div class="card-title">Last 6 months</div>
        <div class="muted tnum" style="font-size:12.5px">Avg ${fmtWhole(avg)}</div>
      </div>
      <div class="mbars" role="img"
        aria-label=${`${kind === "income" ? "Income" : "Spending"} per month: ${vals.map((x) => `${shortMonth(x.month)} ${fmtWhole(x.v)}`).join(", ")}`}>
        ${vals.map(
          (x) => html`
            <div class="mbar ${x.month === cur ? "cur" : ""}" key=${x.month} title=${`${monthLabel(x.month)}: ${fmt(x.v)}`}>
              <span class="v tnum">${x.v ? fmtCompact(x.v).replace("₹", "") : "–"}</span>
              <div class="b" style=${`height:${Math.round((x.v / max) * 80)}px`}></div>
              <span class="m">${shortMonth(x.month)}</span>
            </div>
          `
        )}
      </div>
    </div>
  `;
}

/* ---- Cumulative spend: this month (red) vs last month (context gray) ---- */
function TrendLine({ month, thisDays, lastDays }) {
  const canvasRef = useChart(() => {
    if (!thisDays) return null;
    const daysIn = (m) => new Date(Number(m.slice(0, 4)), Number(m.slice(5, 7)), 0).getDate();
    const n = Math.max(daysIn(month), lastDays ? daysIn(shiftMonth(month, -1)) : 0);
    const cumulate = (rows, len) => {
      const per = new Array(len).fill(0);
      for (const r of rows || []) per[Number(r.date.slice(8, 10)) - 1] = r.expense;
      const out = [];
      let sum = 0;
      for (let i = 0; i < len; i++) { sum += per[i]; out.push(sum); }
      return out;
    };
    const cur = cumulate(thisDays, daysIn(month));
    const prev = lastDays ? cumulate(lastDays, daysIn(shiftMonth(month, -1))) : [];
    return {
      type: "line",
      data: {
        labels: Array.from({ length: n }, (_, i) => i + 1),
        datasets: [
          {
            label: "Last month",
            data: prev,
            borderColor: cssVar("--baseline"),
            borderWidth: 2,
            pointRadius: 0,
            pointHoverRadius: 4,
            tension: 0.2,
          },
          {
            label: "This month",
            data: cur,
            borderColor: cssVar("--accent"),
            borderWidth: 2,
            pointRadius: 0,
            pointHoverRadius: 4,
            tension: 0.2,
          },
        ],
      },
      options: {
        interaction: { mode: "index", intersect: false },
        scales: {
          x: { grid: { display: false }, ticks: { maxTicksLimit: 8 } },
          y: { ticks: { callback: inrTick }, border: { display: false } },
        },
        plugins: {
          tooltip: {
            callbacks: {
              title: (items) => `Day ${items[0].label}`,
              label: (ctx) => ` ${ctx.dataset.label}: ${fmt(ctx.parsed.y)}`,
            },
          },
        },
      },
    };
  }, [month, thisDays, lastDays]);

  return html`
    <div class="card">
      <div class="spread" style="margin-bottom:8px">
        <div class="card-title">Cumulative spend</div>
        <div class="chart-legend">
          <span><i style="background:var(--accent)"></i>This month</span>
          <span><i style="background:var(--baseline)"></i>Last month</span>
        </div>
      </div>
      <canvas ref=${canvasRef} height="170"></canvas>
    </div>
  `;
}

/* ---- Net worth: end-of-month balance across all active accounts ---- */
function NetWorthLine() {
  const { data } = useApi(() => api.summaryNetworth(12), []);
  const canvasRef = useChart(() => {
    if (!data?.items?.length) return null;
    return {
      type: "line",
      data: {
        labels: data.items.map((m) => shortMonth(m.month)),
        datasets: [
          {
            label: "Net worth",
            data: data.items.map((m) => m.value),
            borderColor: cssVar("--accent"),
            borderWidth: 2,
            pointRadius: 0,
            pointHoverRadius: 4,
            tension: 0.2,
          },
        ],
      },
      options: {
        scales: {
          x: { grid: { display: false }, ticks: { maxTicksLimit: 6 } },
          y: { ticks: { callback: inrTick }, border: { display: false } },
        },
        plugins: {
          tooltip: { callbacks: { label: (ctx) => ` ${fmt(ctx.parsed.y)}` } },
        },
      },
    };
  }, [data]);

  if (data && !data.items?.length) return null;
  return html`
    <div class="card">
      <div class="spread" style="margin-bottom:8px">
        <div class="card-title">Net worth</div>
        <div class="muted" style="font-size:12px">money lent to people not included</div>
      </div>
      <canvas ref=${canvasRef} height="170"></canvas>
    </div>
  `;
}

/* ---- Budgets: monthly limit vs spend per parent expense category ---- */
function BudgetSheet({ month, budgets, onClose, onChanged }) {
  const { categories } = useStore();
  const parents = categories.filter((c) => c.kind === "expense");
  const [categoryId, setCategoryId] = useState(null);
  const [amount, setAmount] = useState("");
  const [saving, setSaving] = useState(false);

  function pick(id) {
    setCategoryId(id);
    const existing = budgets?.items?.find((i) => i.category_id === id);
    setAmount(existing ? String(existing.limit) : "");
  }

  async function save() {
    setSaving(true);
    try {
      await api.setBudget(categoryId, Number(amount) || 0);
      toast(Number(amount) > 0 ? "Budget saved" : "Budget removed");
      onChanged();
      setCategoryId(null);
      setAmount("");
    } catch (err) {
      toast(err.message);
    } finally {
      setSaving(false);
    }
  }

  return html`
    <${Sheet} title=${`Budgets · ${monthLabel(month)}`} onClose=${onClose}>
      <div class="field">
        <div class="field-label">Category</div>
        <${Chips} items=${parents} value=${categoryId} onChange=${pick} allowNone />
      </div>
      ${categoryId &&
      html`
        <div class="field">
          <label>Monthly limit (0 removes)</label>
          <${AmountInput} value=${amount} onChange=${setAmount} currency="INR" autofocus />
        </div>
        <button class="btn" disabled=${saving || amount === ""} onClick=${save}>Save budget</button>
      `}
    <//>
  `;
}

function BudgetsCard({ month }) {
  const [refresh, setRefresh] = useState(0);
  const [editing, setEditing] = useState(false);
  const { data } = useApi(() => api.budgets(month), [month, refresh]);

  const items = data?.items || [];
  return html`
    <div class="card">
      <div class="spread" style="margin-bottom:12px">
        <div class="card-title">Budgets</div>
        <button class="linkbtn" onClick=${() => setEditing(true)}>${items.length ? "Edit" : "Set a budget"}</button>
      </div>
      ${items.length === 0 &&
      html`<div class="muted" style="font-size:13px">
        No budgets yet — set a monthly limit per category.
      </div>`}
      ${items.map((b) => {
        const { icon, label } = splitCategoryName(b.name);
        const over = b.pct > 1;
        return html`
          <div class="budget-row" key=${b.category_id}>
            <div class="top">
              <span class="nm ellipsis">${icon ? `${icon} ` : ""}${label || b.name}</span>
              <span class="lbl tnum ${over ? "over" : ""}">
                ${over ? `${fmt(Math.round((b.spent - b.limit) * 100) / 100)} over` : `${fmt(b.spent)} / ${fmt(b.limit)}`}
              </span>
            </div>
            <div class="bar">
              <div class="fill ${over ? "over" : b.pct > 0.9 ? "warn" : ""}" style=${`width:${Math.min(b.pct, 1) * 100}%`}></div>
            </div>
          </div>
        `;
      })}
      ${editing &&
      html`<${BudgetSheet} month=${month} budgets=${data}
        onClose=${() => setEditing(false)} onChanged=${() => setRefresh((r) => r + 1)} />`}
    </div>
  `;
}

/* ---- Category drill-in: filtered transaction list ---- */
function DrillSheet({ month, kind, slice, onClose }) {
  const [from, to] = monthBounds(month);
  const [editTx, setEditTx] = useState(null);
  const [refresh, setRefresh] = useState(0);
  const { data: txs } = useApi(async () => {
    if (slice.category_id) {
      return api.transactions({ from, to, type: kind, category_id: slice.category_id, limit: 500 });
    }
    // "Other" = everything in the folded categories
    const all = await api.transactions({ from, to, type: kind, limit: 1000 });
    const ids = new Set(slice._rest.map((r) => r.category_id));
    return all.filter((tx) => ids.has(tx.category?.parent_id ?? tx.category?.id ?? null));
  }, [slice, month, refresh]);

  return html`
    <${Sheet} onClose=${onClose}>
      <div class="sheet-head">
        <h2 class="ellipsis">${slice.name}</h2>
        <div class="tnum" style="font-weight:700; font-size:17px">${fmt(slice.total)}</div>
      </div>
      <div>
        ${(txs || []).map((tx) => html`<${TxRow} key=${tx.id} tx=${tx} onClick=${() => setEditTx(tx)} />`)}
      </div>
      ${editTx &&
      html`<${TxEditor} tx=${editTx} onClose=${() => setEditTx(null)}
        onSaved=${() => { setEditTx(null); setRefresh((r) => r + 1); }} />`}
    <//>
  `;
}

export function StatsPage() {
  const { month } = useStore();
  const [kind, setKind] = useState("expense");
  const [drill, setDrill] = useState(null);
  const [manageCats, setManageCats] = useState(false);
  const [from, to] = monthBounds(month);
  const lastMonth = shiftMonth(month, -1);

  const { data: catData } = useApi(() => api.summaryCategories(from, to, kind), [month, kind]);
  const { data: months } = useApi(() => api.summaryMonthly(6), [month]);
  const { data: thisDays } = useApi(() => api.summaryDays(month), [month]);
  const { data: lastDays } = useApi(() => api.summaryDays(lastMonth), [month]);

  const empty = catData && catData.items.length === 0;

  return html`
    <div class="page">
      <div class="topbar">
        <${MonthNav} month=${month} onChange=${setMonth} />
        <button class="iconbtn" onClick=${() => setManageCats(true)} aria-label="Manage categories">
          <${Icon} d=${P.sliders} size=${19} />
        </button>
      </div>
      <${Seg}
        options=${[
          { value: "expense", label: "Expenses" },
          { value: "income", label: "Income" },
        ]}
        value=${kind}
        onChange=${setKind}
      />
      ${empty
        ? html`<${EmptyState} icon="📊" title="Nothing to chart yet"
            sub="Add some transactions and the breakdown appears here." />`
        : html`<${CategoryDonut} data=${catData} kind=${kind} onPick=${setDrill} />`}
      ${kind === "expense" && html`<${BudgetsCard} month=${month} />`}
      <${MonthBars} months=${months} kind=${kind} month=${month} />
      <${NetWorthLine} />
      <${TrendLine} month=${month} thisDays=${thisDays} lastDays=${lastDays} />
      ${drill &&
      html`<${DrillSheet} month=${month} kind=${kind} slice=${drill} onClose=${() => setDrill(null)} />`}
      ${manageCats && html`<${ManageCategoriesSheet} onClose=${() => setManageCats(false)} />`}
    </div>
  `;
}
