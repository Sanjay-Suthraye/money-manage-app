import { html, useEffect, useRef, useState } from "../html.js";
import { api } from "../api.js";
import { invalidate, refreshRefData } from "../store.js";
import { CategoryTile, Chips, Seg, Sheet, slotColor, toast, useStore } from "../components/ui.js";
import { Icon, P } from "../icons.js";

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
    <${Sheet} title="Edit category" onClose=${onClose}>
      <div class="field">
        <label>Name</label>
        <input type="text" value=${name} onInput=${(e) => setName(e.target.value)} />
      </div>
      ${!isChild &&
      html`
        <div class="field">
          <div class="field-label">Color</div>
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
      <div class="field" style="margin-top:22px">
        <div class="field-label">Danger zone</div>
        <div class="chips">
          <button class="chip" onClick=${remove} disabled=${saving}>Delete</button>
          <button class="chip ${merging ? "sel" : ""}" onClick=${() => setMerging(!merging)}>Merge into…</button>
        </div>
        ${merging &&
        html`
          <div style="margin-top:10px">
            <${Chips} items=${targets} value=${mergeTarget} onChange=${setMergeTarget} allowNone />
            ${mergeTarget &&
            html`<button class="btn" style="margin-top:10px" disabled=${saving} onClick=${merge}>
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
        <div class="muted" style="font-size:12.5px; margin-bottom:8px">Optional parent (makes it a subcategory)</div>
        <${Chips} items=${parents} value=${parentId} onChange=${setParentId} allowNone />
      </div>
      <button class="btn" style="margin-top:10px" disabled=${!name.trim()} onClick=${add}>Add</button>
    </div>
  `;
}

/* Inline "new category" for pickers (the add-transaction sheet): creates it,
   reloads the shared lists so its chip exists, then hands it back to be
   selected. With `parent` it offers to nest the new one there. */
export function NewCategoryInline({ kind, parent, onCreated, onCancel }) {
  const [name, setName] = useState("");
  const [nested, setNested] = useState(false);
  const [saving, setSaving] = useState(false);
  const input = useRef(null);
  useEffect(() => input.current?.focus(), []);

  async function add() {
    if (!name.trim() || saving) return;
    setSaving(true);
    let cat;
    try {
      cat = await api.createCategory({ name: name.trim(), kind, parent_id: nested ? parent.id : null });
    } catch (err) {
      toast(err.message);
      setSaving(false);
      return;
    }
    await refreshRefData().catch(() => {});
    toast(`Added ${cat.name}`);
    onCreated(cat);
  }

  return html`
    <div class="newcat">
      <div class="newcat-row">
        <input ref=${input} type="text" class="input" placeholder=${`New ${kind} category`}
          aria-label="New category name" value=${name}
          onInput=${(e) => setName(e.target.value)}
          onKeyDown=${(e) => {
            if (e.key === "Enter") add();
            else if (e.key === "Escape") onCancel();
          }} />
        <button class="btn sm" disabled=${!name.trim() || saving} onClick=${add}>Add</button>
        <button class="iconbtn well" onClick=${onCancel} aria-label="Cancel adding category">
          <${Icon} d=${P.close} size=${16} sw=${2.2} />
        </button>
      </div>
      ${parent &&
      html`<button class="chip sm ${nested ? "sel" : ""}" aria-pressed=${nested} onClick=${() => setNested(!nested)}>
        Inside ${parent.name}
      </button>`}
    </div>
  `;
}

export function ManageCategoriesSheet({ onClose }) {
  const { categories } = useStore();
  const [kind, setKind] = useState("expense");
  const [editing, setEditing] = useState(null); // {category, isChild}
  const list = categories.filter((c) => c.kind === kind);

  const row = (cat, isChild, slot) => html`
    <button class="catrow ${isChild ? "child" : ""}" key=${cat.id}
      onClick=${() => setEditing({ category: cat, isChild })}>
      <${CategoryTile} name=${cat.name} slot=${slot} small=${isChild} />
      <span class="grow ellipsis">${cat.name}</span>
      <${Icon} d=${P.chevR} size=${16} sw=${2} />
    </button>
  `;

  return html`
    <${Sheet} title="Categories" onClose=${onClose}>
      <${Seg}
        options=${[
          { value: "expense", label: "Expenses" },
          { value: "income", label: "Income" },
        ]}
        value=${kind} onChange=${setKind}
      />
      ${list.length > 0
        ? html`<div class="list-group">
            ${list.map((p) => html`${row(p, false, p.color_slot)}${p.children.map((ch) => row(ch, true, p.color_slot))}`)}
          </div>`
        : html`<div class="muted" style="padding:12px 0">None yet.</div>`}
      <${NewCategoryRow} kind=${kind} />
      ${editing &&
      html`<${CategorySheet} category=${editing.category} isChild=${editing.isChild} kind=${kind}
        onClose=${() => setEditing(null)} />`}
    <//>
  `;
}
