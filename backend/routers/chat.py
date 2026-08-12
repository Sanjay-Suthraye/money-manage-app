import asyncio
import json
import re
from uuid import uuid4

from datetime import datetime

from claude_agent_sdk import AssistantMessage, ResultMessage, StreamEvent, ToolUseBlock
from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import delete, func as sa_func, select
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from backend.agent.client import agent_session
from backend.agent.tools import UPLOAD_DIR, WRITE_TOOLS
from backend.db import SessionLocal, get_db
from backend.models import ChatMessage, Conversation

DEFAULT_TITLE = "New conversation"

router = APIRouter(prefix="/api/chat", tags=["chat"])

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
ALLOWED_SUFFIXES = {".xlsx", ".xls", ".csv", ".txt", ".png", ".jpg", ".jpeg", ".gif", ".webp"}


class Attachment(BaseModel):
    path: str
    filename: str


class ChatIn(BaseModel):
    message: str
    attachments: list[Attachment] = []


def _summarize(name: str, tool_input: dict) -> str:
    try:
        if name == "add_transactions":
            entries = tool_input.get("entries", [])
            parts = [f"₹{e.get('amount')} {e.get('category') or e.get('type')}" for e in entries[:3]]
            more = f" +{len(entries) - 3} more" if len(entries) > 3 else ""
            return f"Saving {len(entries)} transaction{'s' if len(entries) != 1 else ''}: {', '.join(parts)}{more}"
        if name == "add_loan_entry":
            verbs = {"lend": "Lending", "borrow": "Borrowing", "repay_in": "Recording repayment from",
                     "repay_out": "Recording repayment to"}
            return f"{verbs.get(tool_input.get('kind'), 'Loan')} ₹{tool_input.get('amount')} — {tool_input.get('person')}"
        if name == "add_split_expense":
            return (f"Split bill: my ₹{tool_input.get('my_amount')} + "
                    f"{tool_input.get('person')} owes ₹{tool_input.get('friend_amount')}")
        if name == "query_transactions":
            return "Searching transactions…"
        if name == "get_summary":
            return f"Reading {tool_input.get('mode', '')} summary…"
        if name == "update_transaction":
            return f"Updating transaction #{tool_input.get('id')}"
        if name == "delete_transaction":
            return f"Deleting transaction #{tool_input.get('id')}"
        if name == "inspect_file":
            basename = tool_input.get("path", "").rsplit("/", 1)[-1]
            return f"Reading {basename.split('_', 1)[-1]}…"
        if name == "import_file":
            extras = []
            if tool_input.get("account_types"):
                extras.append(f"{len(tool_input['account_types'])} account types")
            if tool_input.get("person_accounts"):
                extras.append(f"{len(tool_input['person_accounts'])} loan accounts → People")
            return "Importing into the database" + (f" ({', '.join(extras)})…" if extras else "…")
        if name == "update_account":
            return f"Updating account '{tool_input.get('account')}'"
        if name == "undo_import":
            return f"Undoing import batch #{tool_input.get('batch_id')}"
    except Exception:
        pass
    return name


@router.post("/upload")
async def upload(file: UploadFile):
    suffix = ("." + file.filename.rsplit(".", 1)[-1].lower()) if file.filename and "." in file.filename else ""
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(422, f"Unsupported file type '{suffix}'. "
                                 f"Allowed: {', '.join(sorted(ALLOWED_SUFFIXES))}")
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File too large (20 MB max)")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^\w.\-]", "_", file.filename or "file")
    path = UPLOAD_DIR / f"{uuid4().hex[:8]}_{safe_name}"
    path.write_bytes(data)
    return {"path": str(path), "filename": file.filename, "size": len(data)}


def _title_from(text: str) -> str:
    title = " ".join(text.split())
    return (title[:48] + "…") if len(title) > 48 else (title or DEFAULT_TITLE)


def _active_conversation(db: Session) -> Conversation:
    conv = db.scalar(
        select(Conversation).where(Conversation.archived_at.is_(None))
        .order_by(Conversation.id.desc())
    )
    if conv is None:
        conv = Conversation()
        db.add(conv)
        db.commit()
    return conv


def backfill_conversations() -> None:
    """One-time: attach pre-conversation messages to a single active thread.
    Called from the app lifespan; idempotent."""
    db = SessionLocal()
    try:
        orphans = db.scalars(
            select(ChatMessage).where(ChatMessage.conversation_id.is_(None))
            .order_by(ChatMessage.id)
        ).all()
        if not orphans:
            return
        conv = _active_conversation(db)
        if conv.title == DEFAULT_TITLE:
            first_user = next((m for m in orphans if m.role == "user"), None)
            if first_user:
                conv.title = _title_from(first_user.text)
        for m in orphans:
            m.conversation_id = conv.id
        db.commit()
    finally:
        db.close()


def _save_message(role: str, text: str, conversation_id: int,
                  tool_name: str | None = None, tool_writes: bool = False,
                  attachments: list[str] | None = None) -> None:
    db = SessionLocal()
    try:
        db.add(ChatMessage(role=role, text=text, tool_name=tool_name, tool_writes=tool_writes,
                           attachments=json.dumps(attachments) if attachments else None,
                           conversation_id=conversation_id))
        db.commit()
    finally:
        db.close()


def _compose_prompt(body: ChatIn) -> str:
    if not body.attachments:
        return body.message
    lines = "\n".join(f"- {a.path} (original name: {a.filename})" for a in body.attachments)
    return (f"{body.message}\n\n[The user attached these files — examine them with the "
            f"inspect_file tool before responding:\n{lines}]")


async def _abort_orphaned_turn(client) -> None:
    """After a client disconnect mid-turn: interrupt the CLI and drain its
    leftover messages so the next chat starts clean. Reset if that fails."""
    async with agent_session.lock:
        if agent_session.peek() is not client:
            return  # already reset/reconnected

        async def _drain():
            await client.interrupt()
            async for _ in client.receive_response():
                pass

        try:
            await asyncio.wait_for(_drain(), timeout=30)
        except Exception:
            await agent_session.reset()


@router.post("")
async def chat(body: ChatIn):
    async def gen():
        async with agent_session.lock:
            try:
                client = await agent_session.get()
            except Exception as exc:
                yield {"event": "error", "data": json.dumps({"message": str(exc)})}
                return
            db = SessionLocal()
            try:
                conv = _active_conversation(db)
                if conv.title == DEFAULT_TITLE and body.message.strip():
                    conv.title = _title_from(body.message)
                    db.commit()
                conv_id = conv.id
            finally:
                db.close()
            _save_message("user", body.message or "(attached file)", conv_id,
                          attachments=[a.filename for a in body.attachments])
            chunks: list[str] = []
            agent_saved = False
            turn_done = False
            try:
                await client.query(_compose_prompt(body))
                async for msg in client.receive_response():
                    if isinstance(msg, StreamEvent):
                        event = msg.event
                        if (event.get("type") == "content_block_delta"
                                and event.get("delta", {}).get("type") == "text_delta"):
                            chunks.append(event["delta"]["text"])
                            yield {"event": "delta",
                                   "data": json.dumps({"text": event["delta"]["text"]})}
                    elif isinstance(msg, AssistantMessage):
                        for block in msg.content:
                            # Only surface our finance tools; the CLI's internal
                            # tool calls (ToolSearch etc.) are noise in the chat.
                            if isinstance(block, ToolUseBlock) and block.name.startswith("mcp__money__"):
                                name = block.name.removeprefix("mcp__money__")
                                summary = _summarize(name, block.input or {})
                                _save_message("tool", summary, conv_id, tool_name=name,
                                              tool_writes=name in WRITE_TOOLS)
                                yield {"event": "tool", "data": json.dumps({
                                    "name": name,
                                    "summary": summary,
                                    "writes": name in WRITE_TOOLS,
                                })}
                    elif isinstance(msg, ResultMessage):
                        if chunks:
                            _save_message("agent", "".join(chunks), conv_id)
                        agent_saved = True
                        turn_done = True
                        yield {"event": "done", "data": json.dumps({
                            "ok": not msg.is_error,
                            "cost_usd": msg.total_cost_usd,
                        })}
            except GeneratorExit:
                # Client vanished mid-turn (tab closed / hard abort). The CLI keeps
                # generating and its messages would poison the NEXT turn's stream —
                # interrupt and drain in the background, then re-raise.
                if not turn_done:
                    asyncio.get_running_loop().create_task(_abort_orphaned_turn(client))
                raise
            except Exception as exc:
                # The SDK session may be wedged; drop it so the next message reconnects.
                await agent_session.reset()
                yield {"event": "error", "data": json.dumps({"message": str(exc)})}
            finally:
                # Client disconnect raises CancelledError (not Exception) — keep
                # whatever text already streamed.
                if not agent_saved and chunks:
                    _save_message("agent", "".join(chunks), conv_id)

    return EventSourceResponse(gen())


@router.post("/stop")
async def stop():
    """Interrupt the in-flight agent turn. Deliberately does NOT take the
    session lock — the streaming request holds it; the interrupt is delivered
    through the SDK's control channel and the stream then finishes normally."""
    client = agent_session.peek()
    if client is None:
        return {"ok": False, "detail": "no active session"}
    try:
        await client.interrupt()
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "detail": str(exc)}


def _messages_of(db: Session, conversation_id: int) -> list[dict]:
    rows = db.scalars(
        select(ChatMessage).where(ChatMessage.conversation_id == conversation_id)
        .order_by(ChatMessage.id.desc()).limit(200)
    ).all()
    return [
        {"id": m.id, "role": m.role, "text": m.text, "tool_name": m.tool_name,
         "tool_writes": m.tool_writes,
         "attachments": json.loads(m.attachments) if m.attachments else []}
        for m in reversed(rows)
    ]


@router.get("/history")
def history(db: Session = Depends(get_db)):
    return _messages_of(db, _active_conversation(db).id)


@router.get("/conversations")
def conversations(db: Session = Depends(get_db)):
    counts = dict(db.execute(
        select(ChatMessage.conversation_id, sa_func.count(ChatMessage.id))
        .group_by(ChatMessage.conversation_id)
    ).all())
    last_at = dict(db.execute(
        select(ChatMessage.conversation_id, sa_func.max(ChatMessage.created_at))
        .group_by(ChatMessage.conversation_id)
    ).all())
    convs = db.scalars(select(Conversation).order_by(Conversation.id.desc())).all()
    out = [
        {"id": c.id, "title": c.title, "active": c.archived_at is None,
         "created_at": str(c.created_at), "archived_at": str(c.archived_at) if c.archived_at else None,
         "message_count": counts.get(c.id, 0), "last_at": str(last_at.get(c.id) or c.created_at)}
        for c in convs
    ]
    out.sort(key=lambda c: (not c["active"], -c["id"]))  # active first, then newest
    return out


@router.get("/conversations/{conv_id}/messages")
def conversation_messages(conv_id: int, db: Session = Depends(get_db)):
    if not db.get(Conversation, conv_id):
        raise HTTPException(404, f"Conversation {conv_id} not found")
    return _messages_of(db, conv_id)


@router.delete("/conversations/{conv_id}", status_code=204)
def delete_conversation(conv_id: int, db: Session = Depends(get_db)):
    conv = db.get(Conversation, conv_id)
    if not conv:
        raise HTTPException(404, f"Conversation {conv_id} not found")
    if conv.archived_at is None:
        raise HTTPException(409, "This is the current conversation — use New chat to archive it")
    db.execute(delete(ChatMessage).where(ChatMessage.conversation_id == conv_id))
    db.delete(conv)
    db.commit()


@router.post("/reset")
async def reset():
    # Take the lock so a reset can't race an in-flight stream (the frontend
    # aborts first). Archives the current thread instead of deleting it.
    async with agent_session.lock:
        await agent_session.reset()
        db = SessionLocal()
        try:
            conv = _active_conversation(db)
            has_messages = db.scalar(
                select(sa_func.count(ChatMessage.id))
                .where(ChatMessage.conversation_id == conv.id)
            )
            if has_messages:
                conv.archived_at = datetime.now()
                db.commit()
                return {"ok": True, "archived": True}
        finally:
            db.close()
    return {"ok": True, "archived": False}
