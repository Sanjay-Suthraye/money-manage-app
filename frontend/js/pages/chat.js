import { html, useEffect, useMemo, useRef, useState } from "../html.js";
import { api, streamChat } from "../api.js";
import { shortDate } from "../format.js";
import { invalidate, setCredits } from "../store.js";
import { Sheet, toast, useApi, useStore } from "../components/ui.js";
import { renderMarkdown } from "../markdown.js";
import { Icon, P } from "../icons.js";

/* Hide the counter entirely above this. A visibly draining number makes people
   ration the habit-forming feature and log less, so the warning should arrive
   late and mean something. The server sends the threshold in its balance
   snapshot; the constant is only a fallback for an older cached response. */
const CHIP_BELOW_FALLBACK = 10;

/* Out of AI entries. Deliberately reassuring rather than alarming: nothing is
   locked, the manual form still works, and the entries come back on a known
   date. Rendered as a card instead of the red .bubble.error style. */
function NoCreditsCard({ info }) {
  return html`
    <div class="nocredit-card">
      <div style="font-weight:600; margin-bottom:6px">Out of AI entries for now</div>
      <div class="muted" style="font-size:13.5px; line-height:1.5">
        Adding transactions yourself still works exactly as before — nothing is locked.
        ${info?.resets_on ? html` Free entries come back on ${shortDate(info.resets_on)}.` : ""}
      </div>
      <div class="btn-row" style="margin-top:12px">
        <button class="btn ghost sm" onClick=${() => { location.hash = "#/transactions"; }}>
          Add manually
        </button>
      </div>
    </div>
  `;
}

/* Agent replies render as (safe, escaped-first) markdown; re-parsed per delta.
   They sit on the page as plain text — only the user's side is a bubble. */
function AgentBubble({ text, streaming, stopped }) {
  const htmlStr = useMemo(() => renderMarkdown(text), [text]);
  return html`
    <div class="bubble agent">
      ${text
        ? html`<div class="md" dangerouslySetInnerHTML=${{ __html: htmlStr }}></div>`
        : streaming ? html`<span class="typing" aria-label="Thinking"><i></i><i></i><i></i></span>` : ""}
      ${stopped && html`<div class="stopped-note">stopped</div>`}
    </div>
  `;
}

/* What the agent did this turn, one receipt-style card per run of tool steps. */
function ToolCard({ steps }) {
  return html`
    <div class="tool-card">
      ${steps.map((t, i) => html`
        <div class=${`tool-row ${t.writes ? "writes" : ""}`} key=${i}>
          <span class="dot"></span>
          <span class="grow">${t.text}</span>
        </div>
      `)}
    </div>
  `;
}

/* Fold consecutive tool messages into one entry so they render as one card. */
function groupTools(messages) {
  const out = [];
  for (const [i, m] of messages.entries()) {
    const last = out[out.length - 1];
    if (m.role === "tool" && last?.role === "tools") last.steps.push(m);
    else if (m.role === "tool") out.push({ role: "tools", steps: [m], i });
    else out.push({ ...m, i });
  }
  return out;
}

/* Short chips above the composer; `attach` opens the file picker instead. */
const SUGGESTIONS = [
  { label: "Food spend this month?", prompt: "How much did I spend on food this month?" },
  { label: "Import a statement", attach: true },
  { label: "Card due?", prompt: "What's my credit card outstanding, and when is it due?" },
  { label: "Who owes me?", prompt: "Who owes me money?" },
];

let savedMessages = []; // survives tab switches within the session
let historyLoaded = false; // server history fetched once per page load

/* Read-only view of an archived (or the current) conversation. */
function TranscriptSheet({ conversation, onClose, onDeleted }) {
  const { data: msgs } = useApi(() => api.conversationMessages(conversation.id), [conversation.id]);

  async function remove() {
    try {
      await api.deleteConversation(conversation.id);
      toast("Conversation deleted");
      onDeleted();
    } catch (err) {
      toast(err.message);
    }
  }

  return html`
    <${Sheet} onClose=${onClose}>
      <div class="sheet-head" style="margin-bottom:4px">
        <h2 class="ellipsis">${conversation.title}</h2>
        ${!conversation.active &&
        html`<button class="chip sm" onClick=${remove}>Delete</button>`}
      </div>
      <div class="muted" style="font-size:12.5px; margin-bottom:14px">
        ${conversation.active ? "current conversation" : `read-only transcript · ${shortDate(conversation.last_at.slice(0, 10))}`}
      </div>
      <div class="chat-inner">
        ${groupTools((msgs || []).map((m) => ({ ...m, writes: m.tool_writes }))).map((m) =>
          m.role === "tools"
            ? html`<${ToolCard} key=${m.i} steps=${m.steps} />`
            : m.role === "agent"
            ? html`<${AgentBubble} key=${m.i} text=${m.text} />`
            : html`
                <div key=${m.i} class="bubble ${m.role}">
                  ${m.attachments?.length > 0 &&
                  html`<div class="attach-note">📎 ${m.attachments.join(", ")}</div>`}
                  ${m.text}
                </div>
              `
        )}
        ${msgs?.length === 0 && html`<div class="muted" style="padding:12px 0">Empty conversation.</div>`}
      </div>
    <//>
  `;
}

/* List of conversations: the current thread plus archived ones. */
function ConversationsSheet({ onClose }) {
  const [refresh, setRefresh] = useState(0);
  const [viewing, setViewing] = useState(null);
  const { data: convs } = useApi(() => api.conversations(), [refresh]);

  return html`
    <${Sheet} title="Chat history" onClose=${onClose}>
      ${(convs || []).map(
        (c) => html`
          <button class="convrow" key=${c.id} onClick=${() => setViewing(c)}>
            <div class="grow" style="text-align:left; min-width:0">
              <div class="ellipsis" style="font-weight:500">
                ${c.title}${c.active && html`<span class="split-badge">current</span>`}
              </div>
              <div class="sub">${c.message_count} message${c.message_count === 1 ? "" : "s"} · ${shortDate(c.last_at.slice(0, 10))}</div>
            </div>
            <span class="muted"><${Icon} d=${P.chevR} size=${16} sw=${2} /></span>
          </button>
        `
      )}
      ${convs?.length === 0 && html`<div class="muted" style="padding:12px 0">Nothing here yet.</div>`}
      ${viewing &&
      html`<${TranscriptSheet} conversation=${viewing}
        onClose=${() => setViewing(null)}
        onDeleted=${() => { setViewing(null); setRefresh((r) => r + 1); }} />`}
    <//>
  `;
}

/* Small preview of a staged image attachment; URL revoked on unmount. */
function PendingThumb({ file }) {
  const url = useMemo(() => URL.createObjectURL(file), [file]);
  useEffect(() => () => URL.revokeObjectURL(url), [url]);
  return html`<img class="attach-thumb" src=${url} alt=${file.name} />`;
}

export function ChatPage() {
  /* The balance lives in the shared store (loaded by /auth/me, refreshed from
     each turn's done frame), so the chip needs no fetch of its own. */
  const { credits } = useStore();
  const [messages, setMessages] = useState(savedMessages);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [pendingFile, setPendingFile] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const scrollRef = useRef(null);
  const stickToBottom = useRef(true);
  const abortRef = useRef(null);
  const fileInputRef = useRef(null);
  const stoppingRef = useRef(false);
  const stopTimerRef = useRef(null);

  useEffect(() => {
    savedMessages = messages;
    if (stickToBottom.current && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages]);

  useEffect(() => {
    if (historyLoaded || savedMessages.length) return;
    api.chatHistory().then((rows) => {
      historyLoaded = true;
      const msgs = rows.map((r) => ({ role: r.role, text: r.text, attachments: r.attachments, writes: r.tool_writes }));
      setMessages((m) => (m.length ? m : msgs)); // user may have already sent something
    }).catch(() => {});
  }, []);

  function onScroll() {
    const el = scrollRef.current;
    if (!el) return;
    stickToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 60;
  }

  async function send(text) {
    const message = (text ?? input).trim();
    const file = text ? null : pendingFile;
    if ((!message && !file) || busy) return;

    let attachments = [];
    let thumb = null;
    if (file) {
      setUploading(true);
      try {
        const uploaded = await api.uploadChatFile(file);
        attachments = [{ path: uploaded.path, filename: uploaded.filename }];
      } catch (err) {
        toast(`Upload failed: ${err.message}`);
        setUploading(false);
        return;
      }
      setUploading(false);
      setPendingFile(null);
      if (file.type?.startsWith("image/")) thumb = URL.createObjectURL(file);
    }

    setInput("");
    setBusy(true);
    stoppingRef.current = false;
    stickToBottom.current = true;
    setMessages((m) => [
      ...m,
      { role: "user", text: message || "(attached file)", attachments: attachments.map((a) => a.filename), thumb },
      { role: "agent", text: "" },
    ]);

    let wroteData = false;
    const abort = new AbortController();
    abortRef.current = abort;

    streamChat(
      message || "I've attached a file — take a look.",
      attachments,
      {
        onDelta: (chunk) =>
          setMessages((m) => {
            const copy = [...m];
            const last = copy[copy.length - 1];
            copy[copy.length - 1] = { ...last, text: last.text + chunk };
            return copy;
          }),
        onTool: (tool) => {
          if (tool.writes) wroteData = true;
          setMessages((m) => {
            const copy = [...m];
            // keep the streaming agent bubble last; insert tool chip before it
            const streaming = copy.pop();
            copy.push({ role: "tool", text: tool.summary, writes: !!tool.writes }, streaming);
            return copy;
          });
        },
        onDone: (payload) => {
          setBusy(false);
          abortRef.current = null;
          clearTimeout(stopTimerRef.current);
          if (stoppingRef.current) {
            stoppingRef.current = false;
            setMessages((m) => {
              const copy = [...m];
              const last = copy[copy.length - 1];
              if (last?.role === "agent") {
                copy[copy.length - 1] = { ...last, text: last.text || "—", stopped: true };
              }
              return copy;
            });
          } else if (payload?.ok === false) {
            setMessages((m) => {
              const copy = [...m];
              const last = copy[copy.length - 1];
              if (last?.role === "agent" && !last.text) {
                copy[copy.length - 1] = {
                  role: "error",
                  text: "The agent couldn't respond. Try again or start a new chat.",
                };
              }
              return copy;
            });
          }
          if (payload?.credits) setCredits(payload.credits);
          if (wroteData) invalidate();
        },
        onError: (msg, info) => {
          setBusy(false);
          abortRef.current = null;
          clearTimeout(stopTimerRef.current);
          if (info?.error === "out_of_credits") {
            stoppingRef.current = false;
            if (info.balance) setCredits({ ...info.balance, resets_on: info.resets_on });
            setMessages((m) => {
              const copy = [...m];
              const last = copy[copy.length - 1];
              const bubble = { role: "nocredits", info };
              if (last?.role === "agent" && !last.text) copy[copy.length - 1] = bubble;
              else copy.push(bubble);
              return copy;
            });
            return;
          }
          const stopped = stoppingRef.current;
          stoppingRef.current = false;
          setMessages((m) => {
            const copy = [...m];
            const last = copy[copy.length - 1];
            if (stopped) {
              if (last?.role === "agent") {
                copy[copy.length - 1] = { ...last, text: last.text || "—", stopped: true };
              }
              return copy;
            }
            const errBubble = { role: "error", text: msg };
            if (last?.role === "agent" && !last.text) copy[copy.length - 1] = errBubble;
            else copy.push(errBubble); // keep the partial reply, error after it
            return copy;
          });
        },
      },
      abort.signal
    );
  }

  async function stop() {
    if (stoppingRef.current) return;
    stoppingRef.current = true;
    try {
      const r = await api.chatStop();
      if (!r.ok) throw new Error(r.detail || "stop failed");
      // The interrupted turn still ends with a normal `done` frame; if it
      // never arrives, hard-abort as a fallback.
      stopTimerRef.current = setTimeout(() => abortRef.current?.abort(), 4000);
    } catch {
      abortRef.current?.abort();
      setBusy(false);
      stoppingRef.current = false;
      setMessages((m) => {
        const copy = [...m];
        const last = copy[copy.length - 1];
        if (last?.role === "agent") copy[copy.length - 1] = { ...last, text: last.text || "—", stopped: true };
        return copy;
      });
    }
  }

  async function reset() {
    abortRef.current?.abort();
    let archived = false;
    try {
      archived = (await api.chatReset())?.archived ?? false;
    } catch { /* backend may be down; clear locally anyway */ }
    for (const m of savedMessages) if (m.thumb) URL.revokeObjectURL(m.thumb);
    savedMessages = [];
    setMessages([]);
    setBusy(false);
    toast(archived ? "Conversation archived — new chat started" : "New conversation started");
  }

  /* The counter only appears once the balance is low (see CHIP_BELOW_FALLBACK);
     until then the subtitle says what the tab is for. */
  const showCount = credits && credits.total < (credits.chip_below ?? CHIP_BELOW_FALLBACK);

  function pickSuggestion(sg) {
    if (sg.attach) fileInputRef.current?.click();
    else send(sg.prompt);
  }

  return html`
    <div class="chat-page">
      <div class="chat-head">
        <div style="min-width:0">
          <h1>Copilot</h1>
          ${showCount
            ? html`<div class=${`sub ${credits.total < 5 ? "low" : ""}`}>
                ${credits.total} AI ${credits.total === 1 ? "entry" : "entries"} left · resets ${shortDate(credits.resets_on)}
              </div>`
            : html`<div class="sub">Ask about your money, or log a spend</div>`}
        </div>
        <div class="topbar-actions">
          <button class="iconbtn" onClick=${() => setHistoryOpen(true)} aria-label="History">
            <${Icon} d=${P.history} size=${19} />
          </button>
          <button class="iconbtn" onClick=${reset} aria-label="New chat">
            <${Icon} d=${P.pen} size=${19} />
          </button>
        </div>
      </div>
      <div class="chat-scroll" ref=${scrollRef} onScroll=${onScroll}>
        <div class="chat-inner">
          ${messages.length === 0 &&
          html`
            <div class="empty">
              <div class="big">💬</div>
              <div class="ttl">Ask about your money, or dictate entries</div>
              <div class="sub">
                “I spent 500 on lunch and 2000 on groceries today” — the agent records it and asks if anything's unclear.
                Attach your Money Manager export to import it, a receipt photo to record it, or a PDF statement or bill.
              </div>
            </div>
          `}
          ${groupTools(messages).map((m) =>
            m.role === "tools"
              ? html`<${ToolCard} key=${m.i} steps=${m.steps} />`
              : m.role === "agent"
              ? html`<${AgentBubble} key=${m.i} text=${m.text}
                  streaming=${busy && m.i === messages.length - 1} stopped=${m.stopped} />`
              : m.role === "nocredits"
              ? html`<${NoCreditsCard} key=${m.i} info=${m.info} />`
              : html`
                  <div key=${m.i} class="bubble ${m.role}">
                    ${m.thumb && html`<img class="attach-thumb" src=${m.thumb} alt=${m.attachments?.[0] || "attachment"} />`}
                    ${m.attachments?.length > 0 &&
                    html`<div class="attach-note">📎 ${m.attachments.join(", ")}</div>`}
                    ${m.text}
                  </div>
                `
          )}
        </div>
      </div>
      <div class="chat-inputbar">
        <div class="inner">
          ${pendingFile
            ? html`
                <div class="pending">
                  ${pendingFile.type?.startsWith("image/") && html`<${PendingThumb} file=${pendingFile} />`}
                  <span class="chip sel">
                    📎 <span class="ellipsis" style="max-width:200px">${pendingFile.name}</span>
                    <button style="color:inherit; display:grid" onClick=${() => setPendingFile(null)} aria-label="Remove attachment">
                      <${Icon} d=${P.close} size=${14} sw=${2.4} />
                    </button>
                  </span>
                  ${uploading && html`<span class="muted" style="font-size:12.5px">uploading…</span>`}
                </div>
              `
            : !busy &&
              html`<div class="suggest">
                ${SUGGESTIONS.map((sg) => html`<button class="pill" onClick=${() => pickSuggestion(sg)}>${sg.label}</button>`)}
              </div>`}
          <div class="composer">
            <input
              type="file"
              ref=${fileInputRef}
              accept=".xlsx,.xls,.csv,.pdf,application/pdf,image/*"
              style="display:none"
              onChange=${(e) => {
                if (e.target.files[0]) setPendingFile(e.target.files[0]);
                e.target.value = "";
              }}
            />
            <button class="iconbtn" disabled=${busy || uploading}
              onClick=${() => fileInputRef.current?.click()} aria-label="Attach file">
              <${Icon} d=${P.clip} size=${20} />
            </button>
            <label class="composer-field">
              <span class="sr-only">Message</span>
              <textarea
                rows="1"
                placeholder="Ask or log a spend"
                value=${input}
                onInput=${(e) => {
                  setInput(e.target.value);
                  e.target.style.height = "auto";
                  e.target.style.height = `${Math.min(e.target.scrollHeight, 120)}px`;
                }}
                onKeyDown=${(e) => {
                  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
                    e.preventDefault();
                    send();
                  }
                }}
              ></textarea>
              ${busy
                ? html`<button class="sendbtn stop" onClick=${stop} aria-label="Stop"><${Icon} d=${P.stop} size=${14} sw=${2.4} /></button>`
                : html`<button class="sendbtn" onClick=${() => send()} disabled=${!input.trim() && !pendingFile} aria-label="Send">
                    <${Icon} d=${P.up} size=${16} sw=${2.2} />
                  </button>`}
            </label>
          </div>
        </div>
      </div>
      ${historyOpen && html`<${ConversationsSheet} onClose=${() => setHistoryOpen(false)} />`}
    </div>
  `;
}
