import { html, useState } from "../html.js";
import { api } from "../api.js";
import { splitCategoryName } from "../format.js";
import { invalidate } from "../store.js";
import { Chips, Seg, Sheet, slotColor, toast, useStore } from "../components/ui.js";

/* Edit one category: rename, recolor (parents only — children inherit),
   delete, or merge into another category of the same kind. */
function CategorySheet({ category, isChild, kind, onClose }) {
  const { categories } = useStore();
  const [name, setName] = useState(category.name);
  const [colorSlot, setColorSlot] = useState(category.color_slot);
  const [merging, setMerging] = useState(false);
  const [mergeTarget, setMergeTarget] = useState(null);
  const [saving, setSaving] = useState(false);

  // Merge targets: same-kind parents and children, minus self and own children.
  const ownIds = new Set([category.id, ...(category.children?.map((c) => c.id) || [])]);
  const targets = categories
    .filter((p) => p.kind === kind)
    .flatMap((p) => [p, ...p.children])
    .filter((c) => !ownIds.has(c.id));

  async function run(fn, okMsg) {
    setSaving(true);
    try {
      const res = await fn();
      invalidate();
      toast(typeof okMsg === "function" ? okMsg(res) : okMsg);
      onClose();
    } catch (err) {
      toast(err.message);
      setSaving(false);
    }
  }

  const save = () =>
    run(() => {
      const patch = {};
      if (name.trim() !== category.name) patch.name = name.trim();
      if (!isChild && colorSlot !== category.color_slot) patch.color_slot = colorSlot;
      return Object.keys(patch).length ? api.updateCategory(category.id, patch) : Promise.resolve();
    }, "Category updated");

  const remove = () => run(() => api.deleteCategory(category.id), "Category deleted");
  const merge = () =>
    run(() => api.deleteCategory(category.id, mergeTarget), (r) => `Moved ${r.moved} transactions`);

  return html`
    <${Sheet} onClose=${onClose}>
      <h2>Edit category</h2>
      <div class="field">
        <label>Name</label>
        <input type="text" value=${name} onInput=${(e) => setName(e.target.value)} />
      </div>
      ${!isChild &&
      html`
        <div class="field">
          <label>Color</label>
          <div class="swatches">
            ${[0, 1, 2, 3, 4, 5, 6, 7].map(
              (i) => html`
                <button class="swatch ${colorSlot === i ? "sel" : ""}"
                  style="background:${slotColor(i)}" onClick=${() => setColorSlot(i)}
                  aria-label="Color ${i + 1}"></button>
              `
            )}
          </div>
        </div>
      `}
      <button class="btn" disabled=${!name.trim() || saving} onClick=${save}>Save changes</button>
      <div class="field" style="margin-top:18px">
        <label>Danger zone</label>
        <div class="chips">
          <button class="chip" onClick=${remove} disabled=${saving}>Delete</button>
          <button class="chip ${merging ? "sel" : ""}" onClick=${() => setMerging(!merging)}>Merge into…</button>
        </div>
        ${merging &&
        html`
          <div style="margin-top:10px">
            <${Chips} items=${targets} value=${mergeTarget} onChange=${setMergeTarget} allowNone />
            ${mergeTarget &&
            html`<button class="btn danger" style="margin-top:10px" disabled=${saving} onClick=${merge}>
              Move transactions & delete
            </button>`}
          </div>
        `}
      </div>
    <//>
  `;
}

function NewCategoryRow({ kind, onDone }) {
  const { categories } = useStore();
  const [name, setName] = useState("");
  const [parentId, setParentId] = useState(null);
  const parents = categories.filter((c) => c.kind === kind);

  async function add() {
    try {
      await api.createCategory({ name: name.trim(), kind, parent_id: parentId });
      invalidate();
      toast("Category added");
      setName("");
      setParentId(null);
      onDone?.();
    } catch (err) {
      toast(err.message);
    }
  }

  return html`
    <div class="field" style="margin-top:14px">
      <label>New category</label>
      <input type="text" value=${name} placeholder="Name"
        onInput=${(e) => setName(e.target.value)} />
      <div style="margin-top:8px">
        <label style="font-size:12px; color:var(--muted)">Optional parent (makes it a subcategory)</label>
        <${Chips} items=${parents} value=${parentId} onChange=${setParentId} allowNone />
      </div>
      <button class="btn" style="margin-top:10px" disabled=${!name.trim()} onClick=${add}>Add</button>
    </div>
  `;
}

export function ManageCategoriesSheet({ onClose }) {
  const { categories } = useStore();
  const [kind, setKind] = useState("expense");
  const [editing, setEditing] = useState(null); // {category, isChild}
  const list = categories.filter((c) => c.kind === kind);

  const row = (cat, isChild) => {
    const { icon, label } = splitCategoryName(cat.name);
    return html`
      <button class="catrow ${isChild ? "child" : ""}" key=${cat.id}
        onClick=${() => setEditing({ category: cat, isChild })}>
        <span class="swatch" style="background:${slotColor(cat.color_slot)}"></span>
        <span class="grow" style="text-align:left">${icon ? `${icon} ` : ""}${label || cat.name}</span>
        <span class="muted">›</span>
      </button>
    `;
  };

  return html`
    <${Sheet} onClose=${onClose}>
      <h2>Categories</h2>
      <${Seg}
        options=${[
          { value: "expense", label: "Expenses", cls: "exp" },
          { value: "income", label: "Income", cls: "inc" },
        ]}
        value=${kind} onChange=${setKind}
      />
      <div style="margin-top:10px">
        ${list.map((p) => html`${row(p, false)}${p.children.map((ch) => row(ch, true))}`)}
        ${list.length === 0 && html`<div class="muted" style="padding:12px 0">None yet.</div>`}
      </div>
      <${NewCategoryRow} kind=${kind} />
      ${editing &&
      html`<${CategorySheet} category=${editing.category} isChild=${editing.isChild} kind=${kind}
        onClose=${() => setEditing(null)} />`}
    <//>
  `;
}
