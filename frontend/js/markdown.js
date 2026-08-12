/* Minimal, safe markdown for agent chat bubbles.
   Escape-first: the input is fully HTML-escaped before any markup is applied,
   so raw HTML can never pass through. Supports: **bold**, *italic*, `code`,
   fenced code blocks, #/##/### headings, bullet + numbered lists, pipe tables,
   and https links. Unclosed fences are auto-closed (streaming-safe). */

const SENTINEL = "\uE000";

function escapeHtml(s) {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function inline(s) {
  return s
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[\s(])\*([^*\s][^*]*)\*/g, "$1<em>$2</em>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
}

export function renderMarkdown(text) {
  let src = escapeHtml(text).replace(/\uE000/g, "");

  // Pull out fenced code blocks first (auto-close a trailing open fence).
  const blocks = [];
  if ((src.match(/```/g) || []).length % 2 === 1) src += "\n```";
  src = src.replace(/```(\w*)\n?([\s\S]*?)```/g, (_, _lang, code) => {
    blocks.push(`<pre><code>${code.replace(/\n$/, "")}</code></pre>`);
    return `\n${SENTINEL}${blocks.length - 1}${SENTINEL}\n`;
  });

  const lines = src.split("\n");
  const out = [];
  let list = null; // "ul" | "ol" | null
  let para = [];
  let table = null; // collected table rows

  const closeList = () => { if (list) { out.push(`</${list}>`); list = null; } };
  const closePara = () => {
    if (para.length) { out.push(`<p>${para.map(inline).join("<br>")}</p>`); para = []; }
  };
  const closeTable = () => {
    if (!table) return;
    const [head, ...body] = table;
    const tr = (cells, tag) => `<tr>${cells.map((c) => `<${tag}>${inline(c)}</${tag}>`).join("")}</tr>`;
    out.push(`<div class="tblwrap"><table><thead>${tr(head, "th")}</thead><tbody>${
      body.map((r) => tr(r, "td")).join("")}</tbody></table></div>`);
    table = null;
  };
  const closeAll = () => { closePara(); closeList(); closeTable(); };

  for (const raw of lines) {
    const line = raw.trimEnd();
    const placeholder = line.match(/^\uE000(\d+)\uE000$/);
    if (placeholder) { closeAll(); out.push(blocks[Number(placeholder[1])]); continue; }

    if (!line.trim()) { closeAll(); continue; }

    const heading = line.match(/^(#{1,3})\s+(.*)/);
    if (heading) { closeAll(); out.push(`<h${heading[1].length}>${inline(heading[2])}</h${heading[1].length}>`); continue; }

    // Pipe tables: |a|b| rows; the |---| separator row is dropped.
    if (/^\|.*\|$/.test(line.trim())) {
      closePara(); closeList();
      if (/^[\s|:-]+$/.test(line)) continue; // separator row
      const cells = line.trim().slice(1, -1).split("|").map((c) => c.trim());
      (table ??= []).push(cells);
      continue;
    }
    closeTable();

    const bullet = line.match(/^\s*[-*]\s+(.*)/);
    const numbered = line.match(/^\s*\d+[.)]\s+(.*)/);
    if (bullet || numbered) {
      closePara();
      const want = bullet ? "ul" : "ol";
      if (list !== want) { closeList(); out.push(`<${want}>`); list = want; }
      out.push(`<li>${inline((bullet || numbered)[1])}</li>`);
      continue;
    }
    closeList();
    para.push(line);
  }
  closeAll();
  return out.join("");
}
