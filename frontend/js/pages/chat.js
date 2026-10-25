import { html, useEffect, useMemo, useRef, useState } from "../html.js";
import { api, streamChat } from "../api.js";
import { shortDate } from "../format.js";
import { invalidate } from "../store.js";
import { Sheet, toast, useApi } from "../components/ui.js";
import { renderMarkdown } from "../markdown.js";

/* Agent replies render as (safe, escaped-first) markdown; re-parsed per delta. */
function AgentBubble({ text, streaming, stopped }) {
  const htmlStr = useMemo(() => renderMarkdown(text), [text]);
  return html`
    <div class="bubble agent">
      ${text
        ? html`<div class="md" dangerouslySetInnerHTML=${{ __html: htmlStr }}></div>`
        : streaming ? "…" : ""}
      ${stopped && html`<div class="stopped-note">stopped</div>`}
    </div>
  `;
}

const SUGGESTIONS = [
  "How much did I spend on food this month?",
  "Add lunch 250 from cash",
  "Who owes me money?",
  "What's my credit card outstanding?",
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
      <div class="spread">
        <h2 style="margin:0" class="ellipsis">${conversation.title}</h2>
        ${!conversation.active &&
        html`<button class="chip" onClick=${remove}>Delete</button>`}
      </div>
      <div class="muted" style="font-size:12.5px; margin:4px 0 10px">
        ${conversation.active ? "current conversation" : `read-only transcript · ${shortDate(conversation.last_at.slice(0, 10))}`}
      </div>
      <div class="chat-inner" style="padding:0">
        ${(msgs || []).map((m, i) =>
          m.role === "agent"
            ? html`<${AgentBubble} key=${i} text=${m.text} />`
            : html`
                <div key=${i} class="bubble ${m.role}">
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
    <${Sheet} onClose=${onClose}>
      <h2>Chat history</h2>
      ${(convs || []).map(
        (c) => html`
          <button class="convrow" key=${c.id} onClick=${() => setViewing(c)}>
            <div class="grow" style="text-align:left; min-width:0">
              <div class="ellipsis" style="font-weight:550">
                ${c.title}${c.active && html`<span class="split-badge">current</span>`}
              </div>
              <div class="sub">${c.message_count} message${c.message_count === 1 ? "" : "s"} · ${shortDate(c.last_at.slice(0, 10))}</div>
            </div>
            <span class="muted">›</span>
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
      const msgs = rows.map((r) => ({ role: r.role, text: r.text, attachments: r.attachments }));
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
            copy.push({ role: "tool", text: tool.summary }, streaming);
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
          if (wroteData) invalidate();
        },
        onError: (msg) => {
          setBusy(false);
          abortRef.current = null;
          clearTimeout(stopTimerRef.current);
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

  return html`
    <div class="chat-page">
      <div class="topbar" style="padding:10px 16px; position:static">
        <h1>Chat</h1>
        <div style="display:flex; gap:8px">
          <button class="chip" onClick=${() => setHistoryOpen(true)}>History</button>
          <button class="chip" onClick=${reset}>New chat</button>
        </div>
      </div>
      <div class="chat-scroll" ref=${scrollRef} onScroll=${onScroll}>
        <div class="chat-inner">
          ${messages.length === 0 &&
          html`
            <div class="empty">
              <div class="big">💬</div>
              <div style="font-weight:600; color:var(--ink-2)">Ask about your money, or dictate entries</div>
              <div style="font-size:13.5px; margin-top:4px">
                “I spent 500 on lunch and 2000 on groceries today” — the agent records it and asks if anything's unclear.
                Attach 📎 your Money Manager export to import it, a receipt photo to record it, or a PDF statement or bill.
              </div>
              <div class="suggestions">
                ${SUGGESTIONS.map((s) => html`<button class="chip" onClick=${() => send(s)}>${s}</button>`)}
              </div>
            </div>
          `}
          ${messages.map((m, i) =>
            m.role === "agent"
              ? html`<${AgentBubble} key=${i} text=${m.text}
                  streaming=${busy && i === messages.length - 1} stopped=${m.stopped} />`
              : html`
                  <div key=${i} class="bubble ${m.role}">
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
        ${pendingFile &&
        html`
          <div class="inner" style="padding-bottom:6px">
            ${pendingFile.type?.startsWith("image/") &&
            html`<${PendingThumb} file=${pendingFile} />`}
            <span class="chip sel" style="display:inline-flex; align-items:center; gap:6px">
              📎 ${pendingFile.name}
              <button style="color:inherit; font-weight:700" onClick=${() => setPendingFile(null)}>×</button>
            </span>
            ${uploading && html`<span class="muted" style="font-size:12.5px">uploading…</span>`}
          </div>
        `}
        <div class="inner">
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
          <button class="send" style="background:var(--surface-2); color:var(--ink-2)"
            disabled=${busy || uploading}
            onClick=${() => fileInputRef.current?.click()} aria-label="Attach file">📎</button>
          <textarea
            rows="1"
            placeholder="Message"
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
            ? html`<button class="send stop" onClick=${stop} aria-label="Stop">■</button>`
            : html`<button class="send" onClick=${() => send()} aria-label="Send">➤</button>`}
        </div>
      </div>
      ${historyOpen && html`<${ConversationsSheet} onClose=${() => setHistoryOpen(false)} />`}
    </div>
  `;
}
