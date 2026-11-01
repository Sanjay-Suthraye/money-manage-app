import asyncio
import json
import logging
import re
from uuid import uuid4

from datetime import datetime

from claude_agent_sdk import AssistantMessage, ResultMessage, StreamEvent, ToolUseBlock
from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import delete, func as sa_func, select
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from backend.agent.client import BusyError, registry
from backend.agent.tools import UPLOAD_DIR, WRITE_TOOLS
from backend.db import SessionLocal, get_db
from backend.deps import current_user
from backend.models import ChatMessage, Conversation, User
from backend.services import credits as credits_svc

DEFAULT_TITLE = "New conversation"

_log = logging.getLogger("credits")

router = APIRouter(prefix="/api/chat", tags=["chat"])

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
ALLOWED_SUFFIXES = {".xlsx", ".xls", ".csv", ".txt", ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp"}


class Attachment(BaseModel):
    path: str
    filename: str


class ChatIn(BaseModel):
    message: str
    # Capped: prompt rule 11 tells the model to inspect_file EVERY attached path,
    # each image is ~1,600-3,300 tokens, and they are re-sent on every round trip
    # of the turn. Uncapped, one request could cost ~$1 before the daily valve
    # noticed. Nobody attaches five files to a chat message legitimately.
    attachments: list[Attachment] = Field(default_factory=list, max_length=5)


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
async def upload(file: UploadFile, user: User = Depends(current_user)):
    if not user.chat_enabled:
        raise HTTPException(403, "Chat is disabled for this account")
    # Check before accepting 20 MB the user then can't do anything with. The
    # credit itself belongs to the turn that uses the file, not to the upload.
    _preflight(user)
    suffix = ("." + file.filename.rsplit(".", 1)[-1].lower()) if file.filename and "." in file.filename else ""
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(422, f"Unsupported file type '{suffix}'. "
                                 f"Allowed: {', '.join(sorted(ALLOWED_SUFFIXES))}")
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File too large (20 MB max)")
    user_dir = UPLOAD_DIR / str(user.id)
    user_dir.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^\w.\-]", "_", file.filename or "file")
    path = user_dir / f"{uuid4().hex[:8]}_{safe_name}"
    path.write_bytes(data)
    return {"path": str(path), "filename": file.filename, "size": len(data)}


def _title_from(text: str) -> str:
    title = " ".join(text.split())
    return (title[:48] + "…") if len(title) > 48 else (title or DEFAULT_TITLE)


def _active_conversation(db: Session, user_id: int) -> Conversation:
    conv = db.scalar(
        select(Conversation).where(Conversation.archived_at.is_(None),
                                   Conversation.user_id == user_id)
        .order_by(Conversation.id.desc())
    )
    if conv is None:
        conv = Conversation(user_id=user_id)
        db.add(conv)
        db.commit()
    return conv


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


# ---- credit metering -------------------------------------------------------
# Helpers open their OWN SessionLocal (the _save_message pattern above) because
# the request's db may already be closed inside a generator being torn down.

def _preflight(user: User) -> None:
    """Refuse before streaming starts, so a real HTTP status is still possible."""
    db = SessionLocal()
    try:
        credits_svc.ensure_monthly_grant(db, user.id)
        snap = credits_svc.snapshot(db, user.id)
        if snap["total"] < 1:
            raise HTTPException(402, detail=credits_svc.out_of_credits_detail(db, user.id))
        # Abuse valves are separate from credits: 429, not 402, because having
        # credits left is not the problem and buying more would not help.
        try:
            credits_svc.check_daily_caps(db, user.id, feature="chat")
        except credits_svc.RateLimited as exc:
            raise HTTPException(429, detail=exc.detail) from exc
    finally:
        db.close()


def _settle(user_id: int, hold_id: int | None, msg, conv_id: int,
            reason: str | None) -> dict:
    """Log the turn's real usage, then keep, top up, or refund the hold.

    A chat turn costs whatever it cost: the hold is 1 credit and the settle
    charges the difference, priced from `msg` — which must be the session's
    turn_result(), never the raw ResultMessage (that carries the CLI process's
    running total). Never raises — a failure here must not mask whatever else
    went wrong.
    """
    db = SessionLocal()
    try:
        # Priced from our own table. msg.total_cost_usd comes from the CLI's
        # stale Sonnet rate and would over-charge by ~2x.
        cost_usd = credits_svc.result_cost_usd(msg)
        actual = 0 if reason else credits_svc.chat_credits(cost_usd)
        usage_id = credits_svc.record_usage(
            db, user_id, feature="chat", endpoint="POST /api/chat",
            result=msg, conversation_id=conv_id, credits_charged=actual,
            cost_usd=cost_usd)
        credits_svc.link_usage(db, hold_id, usage_id)
        if reason:
            credits_svc.refund(db, user_id, hold_id, reason)
        elif actual > 1 and hold_id:
            # Top up to the real cost. allow_partial drains to zero rather than
            # going negative; any shortfall is recorded on the ledger row.
            credits_svc.debit(db, user_id, feature="chat", amount=actual - 1,
                              ref=f"settle-{hold_id}", usage_id=usage_id,
                              allow_partial=True, note=f"turn cost {actual}")
        return credits_svc.snapshot(db, user_id)
    except Exception:
        _log.exception("settle failed user=%s hold=%s", user_id, hold_id)
        return {}
    finally:
        db.close()


def _release(user_id: int, hold_id: int | None, reason: str) -> None:
    if hold_id is None:
        return
    db = SessionLocal()
    try:
        credits_svc.refund(db, user_id, hold_id, reason)
    except Exception:
        _log.exception("release failed user=%s hold=%s", user_id, hold_id)
    finally:
        db.close()


@router.post("")
async def chat(body: ChatIn, user: User = Depends(current_user)):
    if not user.chat_enabled:
        raise HTTPException(403, "Chat is disabled for this account")
    _preflight(user)          # 402 here, while a real status code is still possible
    session = registry.get(user.id, user.username)
    turn_ref = f"chat-{uuid4().hex[:12]}"

    async def gen():
        hold_id = None
        async with session.lock:
            try:
                client = await session.get()
            except BusyError as exc:
                yield {"event": "error", "data": json.dumps({"message": str(exc)})}
                return
            except Exception as exc:
                yield {"event": "error", "data": json.dumps({"message": str(exc)})}
                return
            db = SessionLocal()
            try:
                conv = _active_conversation(db, user.id)
                if conv.title == DEFAULT_TITLE and body.message.strip():
                    conv.title = _title_from(body.message)
                    db.commit()
                conv_id = conv.id
                # Debit AFTER session.get() succeeded: BusyError and connect
                # failures happen above, so a busy server never costs a credit.
                # This also re-checks the balance, closing the two-tab race
                # against the preflight.
                try:
                    hold_id = credits_svc.debit(db, user.id, feature="chat", ref=turn_ref)
                except credits_svc.OutOfCredits as exc:
                    yield {"event": "error", "data": json.dumps(exc.detail)}
                    return
            finally:
                db.close()
            _save_message("user", body.message or "(attached file)", conv_id,
                          attachments=[a.filename for a in body.attachments])
            chunks: list[str] = []
            wrote_data = False
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
                                wrote_data = wrote_data or name in WRITE_TOOLS
                                _save_message("tool", summary, conv_id, tool_name=name,
                                              tool_writes=name in WRITE_TOOLS)
                                yield {"event": "tool", "data": json.dumps({
                                    "name": name,
                                    "summary": summary,
                                    "writes": name in WRITE_TOOLS,
                                })}
                    elif isinstance(msg, ResultMessage):
                        # This turn's usage alone; the raw message is cumulative.
                        turn = session.turn_result(msg)
                        if chunks:
                            _save_message("agent", "".join(chunks), conv_id)
                        agent_saved = True
                        turn_done = True
                        # terminal_reason is the CLI's own account of why the loop
                        # ended, so "the user pressed Stop" is server-side truth
                        # rather than something the browser claims.
                        stopped = msg.terminal_reason in ("aborted_streaming", "aborted_tools")
                        reason = ("agent_error" if msg.is_error
                                  else "stopped" if stopped
                                  else "no_output" if not (chunks or wrote_data)
                                  else None)
                        # Settle BEFORE the done frame so its payload carries the
                        # post-settle balance.
                        snap = _settle(user.id, hold_id, turn, conv_id, reason)
                        hold_id = None
                        yield {"event": "done", "data": json.dumps({
                            "ok": not msg.is_error,
                            # Our price, matching what was actually charged.
                            # msg.total_cost_usd would report ~2x this.
                            "cost_usd": credits_svc.result_cost_usd(turn),
                            "stopped": stopped,
                            "credits": snap,
                        })}
            except (GeneratorExit, asyncio.CancelledError):
                # Client vanished mid-turn (tab closed / hard abort). Behind
                # uvicorn + sse-starlette that almost always arrives as
                # CancelledError rather than GeneratorExit. The CLI keeps
                # generating and its messages would poison the NEXT turn's stream —
                # interrupt and drain in the background, then re-raise.
                if not turn_done:
                    asyncio.get_running_loop().create_task(session.abort_orphaned_turn(client))
                # Real cost was incurred upstream, but the user saw nothing.
                # Charging for an unseen reply is user-hostile; the leak stays
                # visible in ai_usage (refunded=1) so it can be measured.
                _release(user.id, hold_id, "client_disconnect")
                hold_id = None
                raise
            except Exception as exc:
                # The SDK session may be wedged; drop it so the next message reconnects.
                await session.reset()
                _release(user.id, hold_id, "agent_error")
                hold_id = None
                yield {"event": "error", "data": json.dumps({"message": str(exc)})}
            finally:
                # Safety net: no credit is ever left permanently held, even on a
                # path nobody anticipated.
                _release(user.id, hold_id, "unreconciled")
                # Client disconnect raises CancelledError (not Exception) — keep
                # whatever text already streamed.
                if not agent_saved and chunks:
                    _save_message("agent", "".join(chunks), conv_id)

    return EventSourceResponse(gen())


@router.post("/stop")
async def stop(user: User = Depends(current_user)):
    """Interrupt the caller's in-flight agent turn. Deliberately does NOT take
    the session lock — the streaming request holds it; the interrupt is delivered
    through the SDK's control channel and the stream then finishes normally."""
    session = registry.peek(user.id)
    client = session.peek() if session else None
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
def history(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return _messages_of(db, _active_conversation(db, user.id).id)


@router.get("/conversations")
def conversations(db: Session = Depends(get_db), user: User = Depends(current_user)):
    counts = dict(db.execute(
        select(ChatMessage.conversation_id, sa_func.count(ChatMessage.id))
        .group_by(ChatMessage.conversation_id)
    ).all())
    last_at = dict(db.execute(
        select(ChatMessage.conversation_id, sa_func.max(ChatMessage.created_at))
        .group_by(ChatMessage.conversation_id)
    ).all())
    convs = db.scalars(select(Conversation).where(Conversation.user_id == user.id)
                       .order_by(Conversation.id.desc())).all()
    out = [
        {"id": c.id, "title": c.title, "active": c.archived_at is None,
         "created_at": str(c.created_at), "archived_at": str(c.archived_at) if c.archived_at else None,
         "message_count": counts.get(c.id, 0), "last_at": str(last_at.get(c.id) or c.created_at)}
        for c in convs
    ]
    out.sort(key=lambda c: (not c["active"], -c["id"]))  # active first, then newest
    return out


@router.get("/conversations/{conv_id}/messages")
def conversation_messages(conv_id: int, db: Session = Depends(get_db),
                          user: User = Depends(current_user)):
    conv = db.get(Conversation, conv_id)
    if not conv or conv.user_id != user.id:
        raise HTTPException(404, f"Conversation {conv_id} not found")
    return _messages_of(db, conv_id)


@router.delete("/conversations/{conv_id}", status_code=204)
def delete_conversation(conv_id: int, db: Session = Depends(get_db),
                        user: User = Depends(current_user)):
    conv = db.get(Conversation, conv_id)
    if not conv or conv.user_id != user.id:
        raise HTTPException(404, f"Conversation {conv_id} not found")
    if conv.archived_at is None:
        raise HTTPException(409, "This is the current conversation — use New chat to archive it")
    db.execute(delete(ChatMessage).where(ChatMessage.conversation_id == conv_id))
    db.delete(conv)
    db.commit()


@router.post("/reset")
async def reset(user: User = Depends(current_user)):
    # Take the lock so a reset can't race an in-flight stream (the frontend
    # aborts first). Archives the current thread instead of deleting it.
    session = registry.get(user.id, user.username)
    async with session.lock:
        await session.reset()
        db = SessionLocal()
        try:
            conv = _active_conversation(db, user.id)
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
