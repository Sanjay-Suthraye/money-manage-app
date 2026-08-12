import { html, useEffect, useRef, useState } from "../html.js";
import { api, streamChat } from "../api.js";
import { invalidate } from "../store.js";
import { toast } from "../components/ui.js";

const SUGGESTIONS = [
  "How much did I spend on food this month?",
  "Add lunch 250 from cash",
  "Who owes me money?",
  "What's my credit card outstanding?",
];

let savedMessages = []; // survives tab switches within the session
let historyLoaded = false; // server history fetched once per page load

export function ChatPage() {
  const [messages, setMessages] = useState(savedMessages);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [pendingFile, setPendingFile] = useState(null);
  const [uploading, setUploading] = useState(false);
  const scrollRef = useRef(null);
  const stickToBottom = useRef(true);
  const abortRef = useRef(null);
  const fileInputRef = useRef(null);

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
    }

    setInput("");
    setBusy(true);
    stickToBottom.current = true;
    setMessages((m) => [
      ...m,
      { role: "user", text: message || "(attached file)", attachments: attachments.map((a) => a.filename) },
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
          if (payload?.ok === false) {
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
          setMessages((m) => {
            const copy = [...m];
            const last = copy[copy.length - 1];
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

  async function reset() {
    abortRef.current?.abort();
    try {
      await api.chatReset();
    } catch { /* backend may be down; clear locally anyway */ }
    savedMessages = [];
    setMessages([]);
    setBusy(false);
    toast("New conversation started");
  }

  return html`
    <div class="chat-page">
      <div class="topbar" style="padding:10px 16px; position:static">
        <h1>Chat</h1>
        <button class="chip" onClick=${reset}>New chat</button>
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
                Attach 📎 your Money Manager export to import it, or a receipt photo to record it.
              </div>
              <div class="suggestions">
                ${SUGGESTIONS.map((s) => html`<button class="chip" onClick=${() => send(s)}>${s}</button>`)}
              </div>
            </div>
          `}
          ${messages.map(
            (m, i) => html`
              <div key=${i} class="bubble ${m.role}">
                ${m.attachments?.length > 0 &&
                html`<div class="attach-note">📎 ${m.attachments.join(", ")}</div>`}
                ${m.text || (busy && i === messages.length - 1 ? "…" : "")}
              </div>
            `
          )}
        </div>
      </div>
      <div class="chat-inputbar">
        ${pendingFile &&
        html`
          <div class="inner" style="padding-bottom:6px">
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
            accept=".xlsx,.xls,.csv,image/*"
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
          <button class="send" disabled=${busy} onClick=${() => send()} aria-label="Send">➤</button>
        </div>
      </div>
    </div>
  `;
}
