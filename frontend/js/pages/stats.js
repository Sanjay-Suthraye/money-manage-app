import { html, useEffect, useMemo, useRef, useState } from "../html.js";
import { api } from "../api.js";
import { fmt, fmtWhole, monthBounds, shiftMonth, shortMonth, splitCategoryName } from "../format.js";
import { setMonth } from "../store.js";
import { AmountInput, Chips, EmptyState, MonthNav, Seg, Sheet, toast, useApi, useStore } from "../components/ui.js";
import { applyChartDefaults, cssVar, inrTick, onThemeChange, seriesColor } from "../chartTheme.js";
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

/* ---- Category donut: max 7 slices + Other, center total, ranked legend ---- */
function CategoryDonut({ data, onPick }) {
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

  const canvasRef = useChart(() => {
    if (!slices.length) return null;
    return {
      type: "doughnut",
      data: {
        labels: slices.map((s) => s.name),
        datasets: [
          {
            data: slices.map((s) => s.total),
            backgroundColor: slices.map((s) => seriesColor(s.color_slot)),
            borderColor: cssVar("--surface"),
            borderWidth: 2, // 2px surface spacer between fills
          },
        ],
      },
      options: {
        cutout: "68%",
        plugins: {
          tooltip: { callbacks: { label: (ctx) => ` ${fmt(ctx.parsed)}` } },
        },
      },
    };
  }, [slices]);

  if (!slices.length) return null;

  return html`
    <div class="card">
      <div class="chart-box" style="max-width:280px; margin:0 auto; position:relative">
        <canvas ref=${canvasRef} height="240"></canvas>
        <div style="position:absolute; inset:0; display:grid; place-items:center; pointer-events:none">
          <div style="text-align:center">
            <div class="muted" style="font-size:11.5px; font-weight:600">TOTAL</div>
            <div class="tnum" style="font-size:20px; font-weight:750">${fmtWhole(data.total)}</div>
          </div>
        </div>
      </div>
      <div style="margin-top:10px">
        ${slices.map(
          (s) => html`
            <button class="legend-row" key=${s.name} onClick=${() => onPick(s)}>
              <div class="swatch" style="background:${seriesColor(s.color_slot)}"></div>
              <div class="nm">${s.name}</div>
              <div class="pct tnum">${Math.round(s.share * 100)}%</div>
              <div class="val tnum">${fmt(s.total)}</div>
            </button>
          `
        )}
      </div>
    </div>
  `;
}

/* ---- 6-month income vs expense bars (blue/red semantic pair) ---- */
function MonthlyBars({ months }) {
  const canvasRef = useChart(() => {
    if (!months?.length) return null;
    return {
      type: "bar",
      data: {
        labels: months.map((m) => shortMonth(m.month)),
        datasets: [
          {
            label: "Income",
            data: months.map((m) => m.income),
            backgroundColor: cssVar("--income"),
            borderRadius: 4,
            borderSkipped: false,
            maxBarThickness: 22,
          },
          {
            label: "Expense",
            data: months.map((m) => m.expense),
            backgroundColor: cssVar("--expense"),
            borderRadius: 4,
            borderSkipped: false,
            maxBarThickness: 22,
          },
        ],
      },
      options: {
        scales: {
          x: { grid: { display: false } },
          y: { ticks: { callback: inrTick }, border: { display: false } },
        },
        plugins: {
          tooltip: { callbacks: { label: (ctx) => ` ${ctx.dataset.label}: ${fmt(ctx.parsed.y)}` } },
        },
      },
    };
  }, [months]);

  return html`
    <div class="card">
      <div class="spread" style="margin-bottom:8px">
        <div style="font-weight:650; font-size:14.5px">Income vs expense</div>
        <div class="row" style="gap:12px; font-size:12px">
          <span class="row" style="gap:5px"><span class="swatch" style="width:10px;height:10px;border-radius:3px;background:var(--income);display:inline-block"></span>Income</span>
          <span class="row" style="gap:5px"><span class="swatch" style="width:10px;height:10px;border-radius:3px;background:var(--expense);display:inline-block"></span>Expense</span>
        </div>
      </div>
      <canvas ref=${canvasRef} height="170"></canvas>
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
            borderColor: cssVar("--expense"),
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
        <div style="font-weight:650; font-size:14.5px">Cumulative spend</div>
        <div class="row" style="gap:12px; font-size:12px">
          <span class="row" style="gap:5px"><span style="width:14px;height:2.5px;background:var(--expense);display:inline-block;border-radius:2px"></span>This month</span>
          <span class="row" style="gap:5px"><span style="width:14px;height:2.5px;background:var(--baseline);display:inline-block;border-radius:2px"></span>Last month</span>
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
        <div style="font-weight:650; font-size:14.5px">Net worth</div>
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
    <${Sheet} onClose=${onClose}>
      <h2>Budgets · ${month}</h2>
      <div class="field">
        <label>Category</label>
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

function budgetFill(pct) {
  if (pct >= 1) return "var(--expense)";
  if (pct >= 0.8) return "var(--series-4)";
  return "var(--good-text)";
}

function BudgetsCard({ month }) {
  const [refresh, setRefresh] = useState(0);
  const [editing, setEditing] = useState(false);
  const { data } = useApi(() => api.budgets(month), [month, refresh]);

  const items = data?.items || [];
  return html`
    <div class="card">
      <div class="spread" style="margin-bottom:4px">
        <div style="font-weight:650; font-size:14.5px">Budgets</div>
        <button class="chip" onClick=${() => setEditing(true)}>${items.length ? "Edit" : "Set a budget"}</button>
      </div>
      ${items.length === 0 &&
      html`<div class="muted" style="font-size:13px; padding:6px 0 2px">
        No budgets yet — set a monthly limit per category.
      </div>`}
      ${items.map((b) => {
        const { icon, label } = splitCategoryName(b.name);
        return html`
          <div class="budget-row" key=${b.category_id}>
            <div class="spread">
              <div style="font-size:14px; font-weight:550">${icon ? `${icon} ` : ""}${label || b.name}</div>
              <div class="tnum" style="font-size:13px">
                ${fmt(b.spent)} <span class="muted">of ${fmt(b.limit)}</span>
                ${b.pct >= 1 && html`<span class="neg"> · +${fmt(Math.round((b.spent - b.limit) * 100) / 100)} over</span>`}
              </div>
            </div>
            <div class="budget-bar">
              <div class="fill" style="width:${Math.min(b.pct, 1) * 100}%; background:${budgetFill(b.pct)}"></div>
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
      <div class="spread">
        <h2 style="margin:0">${slice.name}</h2>
        <div class="tnum" style="font-weight:700">${fmt(slice.total)}</div>
      </div>
      <div style="margin-top:8px">
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
        <button class="gearbtn" onClick=${() => setManageCats(true)} aria-label="Manage categories">
          <svg viewBox="0 0 24 24" width="19" height="19" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33h.01a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51h.01a1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82v.01a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
        </button>
      </div>
      <${Seg}
        options=${[
          { value: "expense", label: "Expenses", cls: "exp" },
          { value: "income", label: "Income", cls: "inc" },
        ]}
        value=${kind}
        onChange=${setKind}
      />
      ${empty
        ? html`<${EmptyState} icon="📊" title="Nothing to chart yet"
            sub="Add some transactions and the breakdown appears here." />`
        : html`<${CategoryDonut} data=${catData} onPick=${setDrill} />`}
      ${kind === "expense" && html`<${BudgetsCard} month=${month} />`}
      <${MonthlyBars} months=${months} />
      <${NetWorthLine} />
      <${TrendLine} month=${month} thisDays=${thisDays} lastDays=${lastDays} />
      ${drill &&
      html`<${DrillSheet} month=${month} kind=${kind} slice=${drill} onClose=${() => setDrill(null)} />`}
      ${manageCats && html`<${ManageCategoriesSheet} onClose=${() => setManageCats(false)} />`}
    </div>
  `;
}
