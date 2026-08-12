"""Importer for RealByte Money Manager Excel exports, calibrated against the
user's real export (2026-08-11.xlsx).

Real-format facts this parser relies on:
- Header row 1; a junk duplicate 'Accounts' column holding a numeric copy of the
  amount; the converted-amount column is headed with the base currency code
  (e.g. 'INR'); type values are 'Exp.' / 'Income' / 'Transfer-In' / 'Transfer-Out'.
- Transfers come as row PAIRS sharing an identical timestamp and amount, and the
  Category cell of a transfer row holds the counterpart ACCOUNT name
  (Out: account=source, category=destination; In: account=destination,
  category=source). So transfers are paired by (timestamp, amount) with a
  counterpart cross-check, and even an orphan half can rebuild the full transfer.
- The user tracked loans as pseudo-accounts ("Amit Owe me"); transfers touching
  a declared pseudo-account are converted to lend/borrow/repay ledger entries
  against a Person instead of creating that account.
"""

import io
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from openpyxl import load_workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models import ACCOUNT_TYPES, Account, Category, ImportBatch, Person, Transaction
from backend.utils import to_minor

SYNONYMS = {
    "date": "date", "period": "date",
    "account": "account", "accounts": "account", "asset": "account", "assets": "account",
    "category": "category",
    "subcategory": "subcategory", "sub category": "subcategory", "sub-category": "subcategory",
    "note": "note", "contents": "note", "content": "note",
    "amount": "amount",
    "income/expense": "row_type", "type": "row_type", "income expense": "row_type",
    "description": "description", "memo": "description",
    "currency": "currency", "currency code": "currency",
}

# A header that is itself a currency code marks the converted-amount column
# (RealByte heads it with the user's base currency). Role name stays "inr".
CURRENCY_CODES = {"INR", "USD", "EUR", "GBP", "AED", "SGD", "AUD", "CAD", "JPY",
                  "CHF", "CNY", "HKD", "NZD", "SAR", "QAR", "KWD", "BDT", "LKR", "NPR"}

TYPE_VALUES = {
    "income": "income",
    "expense": "expense", "exp.": "expense", "exp": "expense",
    "transfer-out": "transfer_out", "transfer out": "transfer_out",
    "transfer-in": "transfer_in", "transfer in": "transfer_in",
    "transfer": "transfer_out",  # older exports: one label; pairing sorts it out
}

DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%Y/%m/%d",
                "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M:%S", "%d %b %Y")


def _norm(header) -> str:
    return str(header or "").strip().lower()


def read_sheet(data: bytes) -> tuple[list[str], list[list]]:
    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    ws = wb.worksheets[0]
    headers: list[str] = []
    body: list[list] = []
    for row in ws.iter_rows(values_only=True):
        if not headers:
            if sum(1 for cell in row if cell not in (None, "")) >= 3:
                headers = [str(cell or "").strip() for cell in row]
            continue
        if any(cell not in (None, "") for cell in row):
            body.append(list(row))
    wb.close()
    return headers, body


def propose_mapping(headers: list[str]) -> tuple[dict[str, str], list[str]]:
    """Map role -> actual header text. First matching column wins; duplicate
    headers (RealByte emits a junk second 'Accounts' column) land in unmatched."""
    mapping: dict[str, str] = {}
    unmatched: list[str] = []
    for header in headers:
        if not header:
            continue
        role = SYNONYMS.get(_norm(header))
        if role is None and header.strip().upper() in CURRENCY_CODES:
            role = "inr"
        if role and role not in mapping:
            mapping[role] = header
        else:
            unmatched.append(header)
    return mapping, unmatched


def base_currency(mapping: dict[str, str]) -> str:
    header = (mapping.get("inr") or "").strip().upper()
    return header if header in CURRENCY_CODES else "INR"


def _parse_datetime(value) -> tuple[str | None, str | None]:
    """Returns (date_iso, ts_iso|None). Excel datetime cells carry meaningful
    times; string dates usually don't."""
    if isinstance(value, datetime):
        return value.date().isoformat(), value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.isoformat(), None
    text = str(value or "").strip()
    for fmt in DATE_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt)
            ts = parsed.strftime("%Y-%m-%d %H:%M:%S") if "%H" in fmt else None
            return parsed.date().isoformat(), ts
        except ValueError:
            continue
    return None, None


def _parse_amount(value) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        amount = Decimal(str(value).replace(",", "").replace("₹", "").strip())
    except InvalidOperation:
        return None
    return abs(amount)


@dataclass
class ParsedRow:
    index: int
    date: str
    ts: str | None            # full "YYYY-MM-DD HH:MM:SS" when the cell had a time
    row_type: str             # income | expense | transfer_out | transfer_in
    account: str
    category: str | None      # None on transfer rows (their Category cell is the counterpart)
    subcategory: str | None
    note: str | None
    amount: Decimal
    currency: str
    inr: Decimal
    counterpart: str | None = None  # transfer rows: the other account's name

    @property
    def sort_key(self) -> str:
        return self.ts or f"{self.date} 00:00:00"


@dataclass
class ParseResult:
    rows: list[ParsedRow] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)


def parse_rows(headers: list[str], body: list[list], mapping: dict[str, str]) -> ParseResult:
    col = {role: headers.index(header) for role, header in mapping.items() if header in headers}
    result = ParseResult()
    base_code = base_currency(mapping)
    if base_code != "INR":
        result.issues.append(
            f"export base currency is {base_code} but the app stores INR — "
            "converted amounts will be treated as INR; review before importing"
        )

    def cell(row, role):
        idx = col.get(role)
        return row[idx] if idx is not None and idx < len(row) else None

    for i, row in enumerate(body, start=2):
        date_iso, ts = _parse_datetime(cell(row, "date"))
        if not date_iso:
            result.issues.append(f"row {i}: unparseable date {cell(row, 'date')!r}")
            continue
        raw_type = _norm(cell(row, "row_type"))
        row_type = TYPE_VALUES.get(raw_type)
        if not row_type:
            result.issues.append(f"row {i}: unknown type {cell(row, 'row_type')!r}")
            continue
        account = str(cell(row, "account") or "").strip()
        if not account:
            result.issues.append(f"row {i}: missing account")
            continue

        currency = str(cell(row, "currency") or base_code).strip().upper() or base_code
        base_value = _parse_amount(cell(row, "inr"))
        orig_value = _parse_amount(cell(row, "amount"))
        if currency == base_code:
            amount = base_value if base_value is not None else orig_value
            inr = amount
        else:
            amount = orig_value if orig_value is not None else base_value
            inr = base_value if base_value is not None else orig_value
        if not amount or not inr:
            result.issues.append(f"row {i}: missing amount")
            continue

        note_parts = [str(cell(row, "note") or "").strip(),
                      str(cell(row, "description") or "").strip()]
        note = " — ".join(p for p in note_parts if p) or None

        is_transfer = row_type.startswith("transfer")
        raw_category = str(cell(row, "category") or "").strip() or None
        result.rows.append(ParsedRow(
            index=i, date=date_iso, ts=ts, row_type=row_type, account=account,
            category=None if is_transfer else raw_category,
            subcategory=None if is_transfer else (str(cell(row, "subcategory") or "").strip() or None),
            note=note, amount=amount, currency=currency, inr=inr,
            counterpart=raw_category if is_transfer else None,
        ))
    return result


@dataclass
class TransferSpec:
    """A resolved transfer: source -> destination, built from a pair or from a
    single row whose counterpart account is known."""
    src: str
    dst: str
    row: ParsedRow            # the row supplying date/ts/amount/note
    from_pair: bool


def resolve_transfers(rows: list[ParsedRow]) -> tuple[list[TransferSpec], list[ParsedRow]]:
    """Pair Transfer-Out with Transfer-In rows; rebuild full transfers from
    orphans that carry a counterpart. Returns (specs, hopeless_orphans)."""
    outs = [r for r in rows if r.row_type == "transfer_out"]
    ins = [r for r in rows if r.row_type == "transfer_in"]
    specs: list[TransferSpec] = []
    unpaired_ins = list(ins)

    def cross_check(out: ParsedRow, inn: ParsedRow) -> bool:
        if inn.account == out.account:
            return False
        if out.counterpart and inn.account != out.counterpart:
            return False
        if inn.counterpart and inn.counterpart != out.account:
            return False
        return True

    def try_pair(key_fn) -> list[ParsedRow]:
        remaining_outs = []
        index: dict[tuple, list[ParsedRow]] = {}
        for inn in unpaired_ins:
            index.setdefault(key_fn(inn), []).append(inn)
        for out in pending_outs:
            candidates = index.get(key_fn(out), [])
            match = next((c for c in candidates if cross_check(out, c)), None)
            if match:
                candidates.remove(match)
                unpaired_ins.remove(match)
                specs.append(TransferSpec(src=out.account, dst=match.account,
                                          row=out, from_pair=True))
            else:
                remaining_outs.append(out)
        return remaining_outs

    # Pass 1: exact timestamp; Pass 2: date-level for rows without times.
    pending_outs = outs
    pending_outs = try_pair(lambda r: (r.ts, r.inr) if r.ts else ("no-ts", id(r)))
    pending_outs = try_pair(lambda r: (r.date, r.inr))

    hopeless: list[ParsedRow] = []
    for out in pending_outs:
        if out.counterpart:
            specs.append(TransferSpec(src=out.account, dst=out.counterpart,
                                      row=out, from_pair=False))
        else:
            hopeless.append(out)
    for inn in unpaired_ins:
        if inn.counterpart:
            specs.append(TransferSpec(src=inn.counterpart, dst=inn.account,
                                      row=inn, from_pair=False))
        else:
            hopeless.append(inn)
    return specs, hopeless


# ---------------------------------------------------------------------------
# Analysis (shared by REST preview and the agent's inspect_file tool)
# ---------------------------------------------------------------------------

BILL_CATEGORY_HINT = ("credit card", "card bill", "creditcard")
CREDIT_NOTE_HINT = ("credit", "card", "bill")
OWE_NAME_HINT = ("owe", "owes", "due", "lent", "udhaar")


def analyze(headers: list[str], body: list[list], mapping: dict[str, str],
            unmatched: list[str]) -> dict:
    parsed = parse_rows(headers, body, mapping)
    specs, hopeless = resolve_transfers(
        [r for r in parsed.rows if r.row_type.startswith("transfer")])

    type_counts: dict[str, int] = {}
    for r in parsed.rows:
        type_counts[r.row_type] = type_counts.get(r.row_type, 0) + 1

    account_rows: dict[str, int] = {}
    account_nontransfer: dict[str, int] = {}
    for r in parsed.rows:
        account_rows[r.account] = account_rows.get(r.account, 0) + 1
        if not r.row_type.startswith("transfer"):
            account_nontransfer[r.account] = account_nontransfer.get(r.account, 0) + 1

    incoming_notes: dict[str, list[str]] = {}
    pairs_in: dict[str, int] = {}
    pairs_out: dict[str, int] = {}
    for spec in specs:
        incoming_notes.setdefault(spec.dst, []).append((spec.row.note or "").lower())
        pairs_in[spec.dst] = pairs_in.get(spec.dst, 0) + 1
        pairs_out[spec.src] = pairs_out.get(spec.src, 0) + 1

    def suggest_type(name: str) -> str:
        low = name.lower()
        if "card" in low or "credit" in low:
            return "credit_card"
        if any(any(h in note for h in CREDIT_NOTE_HINT) for note in incoming_notes.get(name, [])):
            return "credit_card"
        if low == "cash":
            return "cash"
        if "wallet" in low or "paytm" in low or "upi" in low or "lite" in low:
            return "wallet"
        return "bank"

    accounts = [
        {
            "name": name,
            "rows": count,
            "only_transfers": account_nontransfer.get(name, 0) == 0,
            "suggested_type": suggest_type(name),
        }
        for name, count in sorted(account_rows.items(), key=lambda kv: -kv[1])
    ]

    candidate_person_accounts = [
        {
            "account": a["name"],
            "rows": a["rows"],
            "transfers_received": pairs_in.get(a["name"], 0),
            "transfers_sent": pairs_out.get(a["name"], 0),
            "name_hints_loan": any(h in a["name"].lower() for h in OWE_NAME_HINT),
        }
        for a in accounts if a["only_transfers"]
    ]

    categories: dict[tuple, int] = {}
    for r in parsed.rows:
        if r.category and r.row_type in ("income", "expense"):
            categories[(r.category, r.row_type)] = categories.get((r.category, r.row_type), 0) + 1

    possible_bill_expenses = [
        {"date": r.date, "account": r.account, "amount": float(r.inr),
         "category": r.category, "note": r.note}
        for r in parsed.rows
        if r.row_type == "expense" and r.category
        and any(h in r.category.lower() for h in BILL_CATEGORY_HINT)
    ]

    dates = sorted(r.date for r in parsed.rows)
    return {
        "headers": headers,
        "mapping": mapping,
        "unmatched_headers": unmatched,
        "base_currency": base_currency(mapping),
        "row_count": len(body),
        "parsed_count": len(parsed.rows),
        "type_counts": type_counts,
        "date_range": [dates[0], dates[-1]] if dates else None,
        "has_timestamps": any(r.ts for r in parsed.rows),
        "accounts": accounts,
        "candidate_person_accounts": candidate_person_accounts,
        "categories": [
            {"name": name, "kind": kind, "rows": count}
            for (name, kind), count in sorted(categories.items(), key=lambda kv: -kv[1])
        ],
        "transfers": {
            "pairs": sum(1 for s in specs if s.from_pair),
            "rebuilt_from_single_row": sum(1 for s in specs if not s.from_pair),
            "unmatched": len(hopeless),
        },
        "possible_bill_expenses": possible_bill_expenses,
        "issues": parsed.issues[:50],
        "sample_rows": [
            {"date": r.date, "ts": r.ts, "type": r.row_type, "account": r.account,
             "category": r.category, "counterpart": r.counterpart,
             "amount": float(r.amount), "currency": r.currency,
             "inr": float(r.inr), "note": r.note}
            for r in parsed.rows[:8]
        ],
    }


# ---------------------------------------------------------------------------
# Commit
# ---------------------------------------------------------------------------

def _get_or_create_account(db: Session, user_id: int, cache: dict, name: str,
                           type_overrides: dict[str, str], created: dict) -> Account:
    key = name.lower()
    if key in cache:
        return cache[key]
    acc = db.scalar(select(Account).where(Account.name.ilike(name), Account.user_id == user_id))
    if not acc:
        wanted = type_overrides.get(key)
        if wanted not in ACCOUNT_TYPES:
            low = key
            wanted = ("credit_card" if "card" in low or "credit" in low else
                      "cash" if low == "cash" else
                      "wallet" if "wallet" in low or "paytm" in low or "upi" in low else "bank")
        acc = Account(user_id=user_id, name=name, type=wanted)
        db.add(acc)
        db.flush()
        created[name] = wanted
    cache[key] = acc
    return acc


def _get_or_create_person(db: Session, user_id: int, cache: dict, name: str, created: list) -> Person:
    key = name.strip().lower()
    if key in cache:
        return cache[key]
    person = db.scalar(select(Person).where(
        Person.name.ilike(name.strip()), Person.user_id == user_id))
    if not person:
        person = Person(user_id=user_id, name=name.strip())
        db.add(person)
        db.flush()
        created.append(person.name)
    cache[key] = person
    return person


def _get_or_create_category(db: Session, user_id: int, cache: dict, name: str, kind: str,
                            parent_id: int | None, created: list) -> Category:
    key = (name.lower(), kind, parent_id)
    if key in cache:
        return cache[key]
    cat = db.scalar(
        select(Category).where(Category.name.ilike(name), Category.kind == kind,
                               Category.user_id == user_id,
                               Category.parent_id.is_(None) if parent_id is None
                               else Category.parent_id == parent_id)
    )
    if not cat:
        if parent_id is None:
            count = len(db.scalars(select(Category.id).where(
                Category.parent_id.is_(None), Category.user_id == user_id)).all())
            cat = Category(user_id=user_id, name=name, kind=kind, color_slot=count % 8)
        else:
            parent = db.get(Category, parent_id)
            cat = Category(user_id=user_id, name=name, kind=kind, parent_id=parent_id,
                           color_slot=parent.color_slot if parent else 0)
        db.add(cat)
        db.flush()
        created.append(name)
    cache[key] = cat
    return cat


def commit_import(db: Session, user_id: int, filename: str, headers: list[str], body: list[list],
                  mapping: dict[str, str],
                  account_types: dict[str, str] | None = None,
                  person_accounts: dict[str, dict] | None = None) -> dict:
    parsed = parse_rows(headers, body, mapping)
    issues = list(parsed.issues)

    type_overrides = {k.strip().lower(): v for k, v in (account_types or {}).items()}
    pseudo = {}
    for acc_name, spec in (person_accounts or {}).items():
        pseudo[acc_name.strip().lower()] = {
            "person": (spec.get("person") or acc_name).strip(),
            "owes": spec.get("owes") or "me",
        }

    specs, hopeless = resolve_transfers(
        [r for r in parsed.rows if r.row_type.startswith("transfer")])
    plain = [r for r in parsed.rows if not r.row_type.startswith("transfer")]

    dates = sorted(r.date for r in parsed.rows)
    fingerprint = (f"{len(body)}:{dates[0] if dates else ''}:{dates[-1] if dates else ''}:"
                   f"{sum(to_minor(r.inr) for r in parsed.rows)}")
    duplicate_of = db.scalar(select(ImportBatch).where(
        ImportBatch.fingerprint == fingerprint, ImportBatch.user_id == user_id))

    batch = ImportBatch(user_id=user_id, filename=filename, row_count=0, fingerprint=fingerprint)
    db.add(batch)
    db.flush()

    acc_cache: dict = {}
    person_cache: dict = {}
    cat_cache: dict = {}
    created_accounts: dict[str, str] = {}
    created_persons: list[str] = []
    created_categories: list[str] = []
    loan_counts = {"lend": 0, "repay_in": 0, "borrow": 0, "repay_out": 0}
    skipped_pseudo = 0
    transfer_count = 0

    def account_of(name: str) -> Account:
        return _get_or_create_account(db, user_id, acc_cache, name, type_overrides, created_accounts)

    pending: list[tuple[str, Transaction]] = []

    def build_plain(row: ParsedRow, tx_type: str, note_suffix: str = "") -> Transaction:
        acc = account_of(row.account)
        category_id = None
        if tx_type in ("income", "expense") and row.category:
            parent = _get_or_create_category(db, user_id, cat_cache, row.category, tx_type,
                                             None, created_categories)
            category_id = parent.id
            if row.subcategory:
                child = _get_or_create_category(db, user_id, cat_cache, row.subcategory, tx_type,
                                                parent.id, created_categories)
                category_id = child.id
        note = ((row.note or "") + note_suffix).strip() or None
        return Transaction(
            user_id=user_id,
            type=tx_type, date=row.date, account_id=acc.id, category_id=category_id,
            amount_minor=to_minor(row.amount), currency=row.currency,
            inr_minor=to_minor(row.inr), note=note, import_batch_id=batch.id,
        )

    # Income / expense rows (skip any sitting directly on a pseudo-account).
    for row in plain:
        if row.account.lower() in pseudo:
            issues.append(f"row {row.index}: {row.row_type} on pseudo-account "
                          f"'{row.account}' skipped — enter manually if needed")
            skipped_pseudo += 1
            continue
        pending.append((row.sort_key, build_plain(row, row.row_type)))

    # Transfers — possibly converted to loan entries.
    for spec in specs:
        row = spec.row
        src_pseudo = pseudo.get(spec.src.lower())
        dst_pseudo = pseudo.get(spec.dst.lower())
        if src_pseudo and dst_pseudo:
            issues.append(f"row {row.index}: transfer between pseudo-accounts "
                          f"'{spec.src}'→'{spec.dst}' skipped — a debt reassignment; enter manually")
            skipped_pseudo += 2 if spec.from_pair else 1
            continue
        if dst_pseudo:  # real -> person
            kind = "lend" if dst_pseudo["owes"] == "me" else "repay_out"
            person = _get_or_create_person(db, user_id, person_cache, dst_pseudo["person"], created_persons)
            acc = account_of(spec.src)
        elif src_pseudo:  # person -> real
            kind = "repay_in" if src_pseudo["owes"] == "me" else "borrow"
            person = _get_or_create_person(db, user_id, person_cache, src_pseudo["person"], created_persons)
            acc = account_of(spec.dst)
        else:
            pending.append((row.sort_key, Transaction(
                user_id=user_id,
                type="transfer", date=row.date,
                account_id=account_of(spec.src).id, to_account_id=account_of(spec.dst).id,
                amount_minor=to_minor(row.amount), currency=row.currency,
                inr_minor=to_minor(row.inr), note=row.note, import_batch_id=batch.id,
            )))
            transfer_count += 1
            continue
        loan_counts[kind] += 1
        pending.append((row.sort_key, Transaction(
            user_id=user_id,
            type=kind, date=row.date, account_id=acc.id, person_id=person.id,
            amount_minor=to_minor(row.amount), currency=row.currency,
            inr_minor=to_minor(row.inr), note=row.note, import_batch_id=batch.id,
        )))

    # Transfer halves with no pair and no counterpart: expense/income fallback.
    for row in hopeless:
        fallback = "expense" if row.row_type == "transfer_out" else "income"
        pending.append((row.sort_key, build_plain(row, fallback, " [transfer-unmatched]")))

    # Insert ascending so autoincrement ids preserve intra-day order (the file
    # arrives newest-first; reverse before the stable sort so equal keys also
    # end up oldest-first).
    pending.reverse()
    pending.sort(key=lambda item: item[0])
    for _, tx in pending:
        db.add(tx)

    batch.row_count = len(pending)
    db.commit()

    report = {
        "batch_id": batch.id,
        "imported": len(pending),
        "transfers": transfer_count,
        "loan_entries": loan_counts,
        "skipped_pseudo_rows": skipped_pseudo,
        "transfers_unmatched": len(hopeless),
        "skipped": len(parsed.issues),
        "created_accounts": created_accounts,
        "created_persons": created_persons,
        "created_categories": created_categories,
        "date_range": [dates[0], dates[-1]] if dates else None,
        "issues": issues[:50],
        "undo_hint": f"undo_import with batch_id {batch.id} removes all imported transactions",
    }
    if duplicate_of:
        report["duplicate_warning"] = (
            f"this file looks identical to import batch {duplicate_of.id} "
            f"('{duplicate_of.filename}') — you may have imported it already"
        )
    return report
