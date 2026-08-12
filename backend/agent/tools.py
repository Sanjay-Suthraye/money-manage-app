"""In-process MCP tools for the chat agent. Thin wrappers over the same service
layer the REST API uses — one write path, one set of validations. Tools accept
account/category/person NAMES (natural for the model) and resolve them here.
Amounts are rupees; services convert to paise."""

import base64
import json
import mimetypes
from pathlib import Path
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool
from sqlalchemy import select

from backend.db import SessionLocal
from backend.importer import moneymanager as mm
from backend.models import Category
from backend.schemas import LoanIn, SplitIn, TransactionIn, TransactionPatch
from backend.services import accounts as accounts_svc
from backend.services import loans as loans_svc
from backend.services import summary as summary_svc
from backend.services import transactions as tx_svc
from backend.settings import PROJECT_ROOT

UPLOAD_DIR = PROJECT_ROOT / "data" / "uploads"

WRITE_TOOLS = {"add_transactions", "add_loan_entry", "add_split_expense",
               "update_transaction", "delete_transaction",
               "import_file", "undo_import", "update_account"}

# (name, description, schema, impl) — impls take (user_id, args) so every
# user's MCP server closes over its own identity. See build_money_server().
TOOL_SPECS: list[tuple] = []


def _spec(name: str, description: str, schema: dict):
    def register(fn):
        TOOL_SPECS.append((name, description, schema, fn))
        return fn
    return register


def _ok(payload: Any) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(payload, default=str)}]}


def _err(message: str) -> dict:
    return {"content": [{"type": "text", "text": f"ERROR: {message}"}], "is_error": True}


def _resolve_category(db, user_id: int, name: str | None, kind: str) -> int | None:
    if not name:
        return None
    cat = db.scalar(select(Category).where(
        Category.name.ilike(name.strip()), Category.kind == kind, Category.user_id == user_id))
    if cat:
        return cat.id
    # Create as a new parent category so the entry is never silently dropped.
    count = len(db.scalars(select(Category.id).where(
        Category.parent_id.is_(None), Category.user_id == user_id)).all())
    cat = Category(user_id=user_id, name=name.strip().title(), kind=kind, color_slot=count % 8)
    db.add(cat)
    db.commit()
    return cat.id


def _resolve_account(db, user_id: int, name: str):
    acc = accounts_svc.resolve_account(db, user_id, name)
    if not acc:
        available = ", ".join(a.name for a in db.scalars(select(accounts_svc.Account).where(
            accounts_svc.Account.user_id == user_id)))
        raise ValueError(f"No account named '{name}'. Available: {available}")
    return acc


@_spec(
    "add_transactions",
    "Record one or more income/expense/transfer entries. Each entry: "
    "{type: 'income'|'expense'|'transfer', amount: rupees, account: name, "
    "category?: name (income/expense), to_account?: name (transfer), "
    "date?: YYYY-MM-DD (default today), note?, currency?: code (default INR), "
    "inr_amount?: rupees (required for non-INR)}",
    {
        "type": "object",
        "properties": {
            "entries": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "enum": ["income", "expense", "transfer"]},
                        "amount": {"type": "number"},
                        "account": {"type": "string"},
                        "category": {"type": "string"},
                        "to_account": {"type": "string"},
                        "date": {"type": "string"},
                        "note": {"type": "string"},
                        "currency": {"type": "string"},
                        "inr_amount": {"type": "number"},
                    },
                    "required": ["type", "amount", "account"],
                },
            }
        },
        "required": ["entries"],
    },
)
async def add_transactions(user_id: int, args: dict[str, Any]) -> dict:
    from backend.utils import today_ist

    db = SessionLocal()
    try:
        items = []
        for entry in args["entries"]:
            acc = _resolve_account(db, user_id, entry["account"])
            to_id = (_resolve_account(db, user_id, entry["to_account"]).id
                     if entry.get("to_account") else None)
            category_id = (
                _resolve_category(db, user_id, entry.get("category"), entry["type"])
                if entry["type"] in ("income", "expense") else None
            )
            items.append(TransactionIn(
                type=entry["type"],
                date=entry.get("date") or today_ist(),
                account_id=acc.id,
                to_account_id=to_id,
                category_id=category_id,
                amount=entry["amount"],
                currency=(entry.get("currency") or "INR").upper(),
                inr_amount=entry.get("inr_amount"),
                note=entry.get("note"),
            ))
        created = tx_svc.create_many(db, user_id, items)
        return _ok({"created": created})
    except Exception as exc:
        return _err(str(exc))
    finally:
        db.close()


@_spec(
    "add_loan_entry",
    "Record a loan move with a person. kind: 'lend' (I gave them money), "
    "'borrow' (they gave me), 'repay_in' (they paid me back), 'repay_out' "
    "(I paid them back). Unknown person names are created automatically.",
    {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": ["lend", "borrow", "repay_in", "repay_out"]},
            "person": {"type": "string"},
            "account": {"type": "string"},
            "amount": {"type": "number"},
            "date": {"type": "string"},
            "note": {"type": "string"},
        },
        "required": ["kind", "person", "account", "amount"],
    },
)
async def add_loan_entry(user_id: int, args: dict[str, Any]) -> dict:
    from backend.utils import today_ist

    db = SessionLocal()
    try:
        person = loans_svc.get_or_create_person(db, user_id, args["person"])
        acc = _resolve_account(db, user_id, args["account"])
        created = loans_svc.add_loan_entry(db, user_id, LoanIn(
            kind=args["kind"], person_id=person.id, account_id=acc.id,
            amount=args["amount"], date=args.get("date") or today_ist(),
            note=args.get("note"),
        ))
        return _ok({"created": created, "person_net_note": "positive = they owe me"})
    except Exception as exc:
        return _err(str(exc))
    finally:
        db.close()


@_spec(
    "add_split_expense",
    "Record a shared bill I paid: my share becomes an expense, the friend's "
    "share becomes a lend against them. Amounts in rupees.",
    {
        "type": "object",
        "properties": {
            "account": {"type": "string"},
            "person": {"type": "string"},
            "my_amount": {"type": "number"},
            "friend_amount": {"type": "number"},
            "category": {"type": "string"},
            "date": {"type": "string"},
            "note": {"type": "string"},
        },
        "required": ["account", "person", "my_amount", "friend_amount"],
    },
)
async def add_split_expense(user_id: int, args: dict[str, Any]) -> dict:
    from backend.utils import today_ist

    db = SessionLocal()
    try:
        acc = _resolve_account(db, args["account"])
        person = loans_svc.get_or_create_person(db, user_id, args["person"])
        category_id = _resolve_category(db, args.get("category"), "expense")
        created = tx_svc.create_split(db, SplitIn(
            date=args.get("date") or today_ist(),
            account_id=acc.id, category_id=category_id, person_id=person.id,
            my_amount=args["my_amount"], friend_amount=args["friend_amount"],
            note=args.get("note"),
        ))
        return _ok({"created": created})
    except Exception as exc:
        return _err(str(exc))
    finally:
        db.close()


@_spec(
    "query_transactions",
    "Search transactions. Filters: from/to dates (YYYY-MM-DD), account (name), "
    "category (name), person (name), type, text (note search), limit. Returns "
    "newest first with ids (needed for update/delete).",
    {
        "type": "object",
        "properties": {
            "from": {"type": "string"},
            "to": {"type": "string"},
            "account": {"type": "string"},
            "category": {"type": "string"},
            "person": {"type": "string"},
            "type": {"type": "string"},
            "text": {"type": "string"},
            "limit": {"type": "integer"},
        },
    },
)
async def query_transactions(user_id: int, args: dict[str, Any]) -> dict:
    db = SessionLocal()
    try:
        account_id = _resolve_account(db, user_id, args["account"]).id if args.get("account") else None
        category_id = None
        if args.get("category"):
            cat = db.scalar(select(Category).where(
                Category.name.ilike(args["category"].strip()), Category.user_id == user_id))
            if cat:
                category_id = cat.id
        person = loans_svc.resolve_person(db, user_id, args["person"]) if args.get("person") else None
        rows = tx_svc.list_transactions(
            db,
            user_id,
            date_from=args.get("from"), date_to=args.get("to"),
            account_id=account_id, category_id=category_id,
            person_id=person.id if person else None,
            tx_type=args.get("type"), q=args.get("text"),
            limit=min(args.get("limit") or 50, 200),
        )
        return _ok({"count": len(rows), "transactions": rows})
    except Exception as exc:
        return _err(str(exc))
    finally:
        db.close()


@_spec(
    "get_summary",
    "Aggregated numbers. mode='monthly' (income/expense per month, param months), "
    "mode='categories' (per-category totals, params from/to/kind), "
    "mode='accounts' (balances + credit-card outstanding + statement-cycle info), "
    "mode='persons' (who owes whom), "
    "mode='budgets' (monthly category limits vs spend, param month YYYY-MM), "
    "mode='networth' (end-of-month total balance series, param months), "
    "mode='recurring' (recurring rules and currently-due suggestions).",
    {
        "type": "object",
        "properties": {
            "mode": {"type": "string",
                     "enum": ["monthly", "categories", "accounts", "persons",
                              "budgets", "networth", "recurring"]},
            "months": {"type": "integer"},
            "month": {"type": "string"},
            "from": {"type": "string"},
            "to": {"type": "string"},
            "kind": {"type": "string", "enum": ["income", "expense"]},
        },
        "required": ["mode"],
    },
)
async def get_summary(user_id: int, args: dict[str, Any]) -> dict:
    from backend.services import budgets as budgets_svc
    from backend.services import recurring as recurring_svc
    from backend.utils import today_ist

    db = SessionLocal()
    try:
        mode = args["mode"]
        if mode == "monthly":
            return _ok(summary_svc.monthly(db, user_id, args.get("months") or 6))
        if mode == "categories":
            today = today_ist()
            date_from = args.get("from") or f"{today[:7]}-01"
            date_to = args.get("to") or today
            return _ok(summary_svc.categories(db, user_id, date_from, date_to, args.get("kind") or "expense"))
        if mode == "accounts":
            return _ok(accounts_svc.list_with_balances(db, user_id))
        if mode == "persons":
            return _ok(loans_svc.person_balances(db, user_id))
        if mode == "budgets":
            return _ok(budgets_svc.list_budgets(db, user_id, args.get("month") or today_ist()[:7]))
        if mode == "networth":
            return _ok(summary_svc.networth(db, user_id, args.get("months") or 12))
        if mode == "recurring":
            return _ok({"rules": recurring_svc.list_rules(db, user_id), "due": recurring_svc.due_items(db, user_id)})
        return _err(f"unknown mode {mode}")
    except Exception as exc:
        return _err(str(exc))
    finally:
        db.close()


@_spec(
    "update_transaction",
    "Update fields of an existing transaction by id (get the id from "
    "query_transactions first). Only pass the fields to change.",
    {
        "type": "object",
        "properties": {
            "id": {"type": "integer"},
            "amount": {"type": "number"},
            "date": {"type": "string"},
            "account": {"type": "string"},
            "category": {"type": "string"},
            "note": {"type": "string"},
            "type": {"type": "string", "enum": ["income", "expense", "transfer"]},
        },
        "required": ["id"],
    },
)
async def update_transaction(user_id: int, args: dict[str, Any]) -> dict:
    db = SessionLocal()
    try:
        tx = tx_svc.get_transaction(db, user_id, args["id"])
        patch = TransactionPatch(
            amount=args.get("amount"),
            date=args.get("date"),
            type=args.get("type"),
            note=args.get("note"),
        )
        if args.get("account"):
            patch.account_id = _resolve_account(db, user_id, args["account"]).id
        if args.get("category"):
            patch.category_id = _resolve_category(db, user_id, args["category"],
                                                  args.get("type") or tx.type)
        return _ok({"updated": tx_svc.update_transaction(db, user_id, args["id"], patch)})
    except Exception as exc:
        return _err(str(exc))
    finally:
        db.close()


@_spec(
    "delete_transaction",
    "Delete a transaction by id (get the id from query_transactions first, and "
    "confirm with the user when several could match).",
    {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]},
)
async def delete_transaction(user_id: int, args: dict[str, Any]) -> dict:
    db = SessionLocal()
    try:
        tx_svc.delete_transaction(db, user_id, args["id"])
        return _ok({"deleted": args["id"]})
    except Exception as exc:
        return _err(str(exc))
    finally:
        db.close()


# Model-friendly image bounds: ~1568px is Claude vision's effective maximum,
# and results must stay well under transport/API message limits.
IMAGE_SHRINK_THRESHOLD_BYTES = 500 * 1024
IMAGE_MAX_DIMENSION = 1568


def _shrink_image(raw: bytes) -> bytes | None:
    """Re-encode a large image as a bounded JPEG; None = keep the original."""
    try:
        from io import BytesIO

        from PIL import Image

        with Image.open(BytesIO(raw)) as img:
            oversized = max(img.size) > IMAGE_MAX_DIMENSION
            if len(raw) <= IMAGE_SHRINK_THRESHOLD_BYTES and not oversized:
                return None
            img = img.convert("RGB")
            if oversized:
                img.thumbnail((IMAGE_MAX_DIMENSION, IMAGE_MAX_DIMENSION))
            out = BytesIO()
            img.save(out, format="JPEG", quality=80)
            return out.getvalue()
    except Exception:
        return None  # unreadable/exotic image: fall back to the original bytes


def _safe_upload_path(path_str: str, user_id: int) -> Path:
    path = Path(path_str).resolve()
    jail = (UPLOAD_DIR / str(user_id)).resolve()
    if jail not in path.parents:
        raise ValueError("Only files the user attached in this chat (under data/uploads) can be opened")
    if not path.is_file():
        raise ValueError(f"File not found: {path_str}")
    return path


@_spec(
    "inspect_file",
    "Examine a file the user attached in chat. Spreadsheets (.xlsx/.csv) return "
    "headers, a proposed import mapping, sample parsed rows, and stats. Images "
    "(receipts, screenshots) are returned for you to look at directly.",
    {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
)
async def inspect_file(user_id: int, args: dict[str, Any]) -> dict:
    try:
        path = _safe_upload_path(args["path"], user_id)
        suffix = path.suffix.lower()
        if suffix in (".xlsx", ".xls"):
            headers, body = mm.read_sheet(path.read_bytes())
            mapping, unmatched = mm.propose_mapping(headers)
            analysis = mm.analyze(headers, body, mapping, unmatched)
            analysis["kind"] = "spreadsheet"
            analysis["next_step"] = (
                "Summarize for the user: rows, date range, accounts, categories, "
                "transfer stats. Then, unless already known, confirm two things "
                "before import_file: (1) the type of each NEW account — especially "
                "which are credit cards (see suggested_type, but the user decides); "
                "(2) whether each candidate_person_account should become a Person "
                "in the loan ledger (get the person's name and who owes whom). "
                "Then call import_file with account_types and person_accounts."
            )
            return _ok(analysis)
        if suffix in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
            raw = path.read_bytes()
            mime = mimetypes.guess_type(path.name)[0] or "image/png"
            shrunk = _shrink_image(raw)
            if shrunk is not None:
                raw, mime = shrunk, "image/jpeg"
            data = base64.standard_b64encode(raw).decode()
            return {"content": [{"type": "image", "data": data, "mimeType": mime}]}
        if suffix in (".csv", ".txt"):
            return _ok({"kind": "text", "preview": path.read_text(errors="replace")[:8000]})
        return _err(f"Unsupported file type {suffix}")
    except Exception as exc:
        return _err(str(exc))


@_spec(
    "import_file",
    "Import an attached Money Manager Excel export into the database. Run "
    "inspect_file first and confirm with the user. Optional params: mapping "
    "(role -> header; auto-proposed when omitted); account_types (account name "
    "-> cash|bank|credit_card|wallet, applied to NEW accounts); person_accounts "
    "(pseudo-account name -> {person: real name, owes: 'me'|'them'}) — transfers "
    "touching those accounts become loan-ledger entries instead of creating the "
    "account. Returns a report with batch_id; reversible with undo_import.",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "mapping": {"type": "object"},
            "account_types": {
                "type": "object",
                "additionalProperties": {
                    "type": "string",
                    "enum": ["cash", "bank", "credit_card", "wallet"],
                },
            },
            "person_accounts": {
                "type": "object",
                "additionalProperties": {
                    "type": "object",
                    "properties": {
                        "person": {"type": "string"},
                        "owes": {"type": "string", "enum": ["me", "them"]},
                    },
                    "required": ["person"],
                },
            },
        },
        "required": ["path"],
    },
)
async def import_file(user_id: int, args: dict[str, Any]) -> dict:
    db = SessionLocal()
    try:
        path = _safe_upload_path(args["path"], user_id)
        if path.suffix.lower() not in (".xlsx", ".xls"):
            return _err("Only Excel exports (.xlsx) can be imported")
        headers, body = mm.read_sheet(path.read_bytes())
        mapping = args.get("mapping") or mm.propose_mapping(headers)[0]
        report = mm.commit_import(
            db, user_id, path.name, headers, body, mapping,
            account_types=args.get("account_types"),
            person_accounts=args.get("person_accounts"),
        )
        return _ok(report)
    except Exception as exc:
        return _err(str(exc))
    finally:
        db.close()


@_spec(
    "update_account",
    "Update an account: change its type, rename it, set card statement/due days "
    "or credit limit, or fix its balance. Use actual_balance for the balance "
    "true-up after an import that covers only part of history: pass the REAL "
    "current balance and the opening balance is recomputed server-side so the "
    "app matches reality. SIGN CONVENTION for credit cards: the balance is "
    "negative when money is owed — 'I owe 12,000 on ATLAS' means "
    "actual_balance = -12000.",
    {
        "type": "object",
        "properties": {
            "account": {"type": "string", "description": "current account name"},
            "type": {"type": "string", "enum": ["cash", "bank", "credit_card", "wallet"]},
            "new_name": {"type": "string"},
            "actual_balance": {"type": "number",
                               "description": "true current balance in rupees (negative = owed)"},
            "opening_balance": {"type": "number"},
            "statement_day": {"type": "integer"},
            "due_day": {"type": "integer"},
            "credit_limit": {"type": "number"},
        },
        "required": ["account"],
    },
)
async def update_account(user_id: int, args: dict[str, Any]) -> dict:
    from decimal import Decimal

    from backend.schemas import AccountPatch
    from backend.utils import rupees, to_minor

    db = SessionLocal()
    try:
        acc = _resolve_account(db, user_id, args["account"])
        fields: dict[str, Any] = {}
        if args.get("type"):
            fields["type"] = args["type"]
        if args.get("new_name"):
            fields["name"] = args["new_name"]
        for key in ("statement_day", "due_day"):
            if args.get(key) is not None:
                fields[key] = args[key]
        if args.get("credit_limit") is not None:
            fields["credit_limit"] = Decimal(str(args["credit_limit"]))
        if args.get("actual_balance") is not None:
            # True-up: shift the opening balance so computed == actual. Server-side
            # arithmetic in paise — the model never does the math.
            computed = accounts_svc.compute_balances(db, user_id).get(acc.id, 0)
            actual_minor = to_minor(Decimal(str(args["actual_balance"])))
            fields["opening_balance"] = Decimal(
                str(rupees(acc.opening_balance_minor + (actual_minor - computed)))
            )
        elif args.get("opening_balance") is not None:
            fields["opening_balance"] = Decimal(str(args["opening_balance"]))
        if not fields:
            return _err("Nothing to update — pass at least one field")
        updated = accounts_svc.update_account(db, user_id, acc.id, AccountPatch(**fields))
        balance = accounts_svc.compute_balances(db, user_id).get(updated.id, 0)
        return _ok({"account": accounts_svc.account_to_dict(updated, balance)})
    except Exception as exc:
        return _err(str(exc))
    finally:
        db.close()


@_spec(
    "undo_import",
    "Delete every transaction created by a previous import batch (from the "
    "batch_id in import_file's report).",
    {"type": "object", "properties": {"batch_id": {"type": "integer"}}, "required": ["batch_id"]},
)
async def undo_import(user_id: int, args: dict[str, Any]) -> dict:
    from sqlalchemy import delete as sa_delete

    from backend.models import ImportBatch, Transaction

    db = SessionLocal()
    try:
        batch = db.get(ImportBatch, args["batch_id"])
        if not batch or batch.user_id != user_id:
            return _err(f"Import batch {args['batch_id']} not found")
        deleted = db.execute(
            sa_delete(Transaction).where(Transaction.import_batch_id == batch.id,
                                         Transaction.user_id == user_id)
        ).rowcount
        db.delete(batch)
        db.commit()
        return _ok({"deleted_transactions": deleted})
    except Exception as exc:
        return _err(str(exc))
    finally:
        db.close()


def build_money_server(user_id: int):
    """One MCP server per agent session, closed over the owning user — the
    only way tool calls (which carry no request context) stay isolated."""
    def bind(name, description, schema, impl):
        async def handler(args: dict[str, Any]) -> dict:
            return await impl(user_id, args)
        handler.__name__ = name
        return tool(name, description, schema)(handler)

    return create_sdk_mcp_server(
        name="money", version="1.0.0",
        tools=[bind(*spec) for spec in TOOL_SPECS],
    )


ALLOWED_TOOL_NAMES = [f"mcp__money__{spec[0]}" for spec in TOOL_SPECS]
