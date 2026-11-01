# Money Manager POC

A personal finance tracker built to replace the RealByte Money Manager iOS app,
with the two pain points fixed structurally and a Claude-powered chat agent that
knows your full spending history.

- **Credit cards done right** — purchases are expenses on the card; the bill
  payment is a one-tap *transfer* bank → card. It never shows up as an expense,
  so your stats stay honest.
- **Friend loans** — lend / borrow / settle with per-person running balances,
  partial settlements, and split bills ("I paid ₹1,200, Raj owes half").
- **Quick add** — type "450 swiggy yesterday" in the bar above the + button and
  a confirmation chip appears (`₹450 · Food · GPay LITE · Yesterday`) with every
  field tappable. Nothing is saved until you tap ✓. Most entries are parsed in
  Python with no model call at all, and it learns: correct a category once and
  that merchant is filed your way from then on. See **Quick add** below.
- **Chat agent** — "I spent 500 on lunch and 2000 on groceries today" records
  both; "who owes me money?" answers from the database. It asks one clarifying
  question when something (like the account) is ambiguous. Runs on your Claude
  subscription via the Claude Agent SDK — no API billing.
- **File attachments in chat** (📎) — attach your Money Manager Excel export and
  the agent inspects it, summarizes what's inside, imports on your go-ahead, and
  can undo the batch conversationally. Attach a receipt photo and it reads the
  merchant/total/date (even the card used, if printed) and records the expense.
  Attach a PDF (a bank or card statement, a bill) and it reads the text — scans
  as page images, password-protected statements with the password you give it —
  then lists the entries it would add and records them on your go-ahead.

## Run it

```bash
# one-time setup
~/miniconda3/bin/conda create -n money python=3.12 -y
~/miniconda3/envs/money/bin/pip install -r requirements.txt

# optional: seed default categories (only into an empty DB; --sample adds demo data)
~/miniconda3/envs/money/bin/python -m backend.seed
# wipe every row (fresh start before importing real history):
~/miniconda3/envs/money/bin/python -m backend.seed --wipe

# start (port 8000 is taken by an SSH tunnel on this machine, hence 8010)
./run.sh          # = uvicorn backend.main:app --host 0.0.0.0 --port 8010
```

Open http://localhost:8010 on the Mac.

The server also runs as a **LaunchAgent** (`deploy/com.sanjaysuthraye.moneymanager.plist`,
installed to `~/Library/LaunchAgents/`): it starts at login and auto-restarts if
it crashes — no terminal needed. Manage it with:

```bash
launchctl kickstart -k gui/$(id -u)/com.sanjaysuthraye.moneymanager   # restart
launchctl bootout   gui/$(id -u)/com.sanjaysuthraye.moneymanager      # stop (then ./run.sh works manually)
```

**All logs stay inside this folder, under `data/logs/`** (gitignored with the rest
of `data/`): `launchd.log` is startup/crash output from the LaunchAgent, `app.log`
is the app's own rotated log, and `copilot.jsonl` is one line per quick-add.
Nothing is written outside the project.

> The plist hardcodes an absolute `WorkingDirectory`, and uvicorn imports
> `backend.main` — so **if you move or rename the project folder, update the plist
> and reinstall it**. A stale path fails with `ModuleNotFoundError: No module named
> 'backend'`, and `KeepAlive` turns that into an endless respawn loop that quietly
> fills a log file at the old location. Check with
> `launchctl list | grep money` — the middle column is the last exit code and
> should be `0`.

## Production (AWS Lightsail)

The app runs 24/7 at **https://13.234.241.7** (Lightsail `money-manager`,
ap-south-1, static IP) with the login screen enforced — credentials live in the
server's `.env` (backup copy: `~/Documents/money-server.env`).

- **Deploy**: `deploy/deploy.sh` — pushes local `main` to GitHub, then the
  server (a git checkout of `origin/main` using a read-only deploy key) pulls
  and restarts. Server-side `.env`, `data/`, `.venv` are untracked and survive
  every deploy.
- **HTTPS**: Caddy with a Let's Encrypt *IP-address* certificate (~6-day
  shortlived profile, auto-renewed). `default_sni` in the Caddyfile is required
  — IP clients send no SNI and Lightsail NAT hides the public IP.
- **Backups**: nightly cron → `s3://money-backups-707875361098/daily/` via a
  write-only IAM user. Server ops: `journalctl -u money-manager -f`, restart
  with `sudo systemctl restart money-manager`.

## Use it from your iPhone

Open **https://13.234.241.7** in Safari → log in → Share → **Add to Home
Screen** — you get a ₹ icon and a full-screen app (log in once more inside it;
installed web apps have their own cookie jar). Works from anywhere, ~30-day
sessions.

The Mac dev copy is still reachable on home Wi-Fi (`./phone.sh` prints the URL
and a QR code); it has no credentials configured, so it skips the login screen —
which is why it must never be exposed beyond the LAN with router port-forwarding.

## Chat agent auth

The agent shares the Claude Code subscription login already on this machine —
zero setup. Alternatives (in `.env` at the project root):

- `CLAUDE_CODE_OAUTH_TOKEN=...` — explicit token from `claude setup-token`
- `AGENT_USE_API_KEY=1` — use the `ANTHROPIC_API_KEY` from `api_keys.env`
  (pay-per-use API billing; off by default on purpose)
- `AGENT_MODEL=claude-haiku-4-5` — cheaper/faster model. The default is the
  pinned `claude-sonnet-5`, **not** a bare `sonnet` alias: aliases are resolved
  by the `claude` CLI, so a CLI upgrade would silently move you to a different
  model and price with nothing to catch it.
- `AGENT_FALLBACK_MODEL=...` — used when the primary is overloaded, instead of
  erroring the reply stream.

Only one credential is ever passed to the agent subprocess, so an exported
`ANTHROPIC_API_KEY` can't silently switch you to API billing.

The agent is given only the money tools (`tools=[]` drops the CLI's built-in
tool preset, which it never used). Measured on an identical turn, that cut input
from **18,686 to 8,326 tokens** and the turn's cost from **$0.110 to $0.047**.
Set `AGENT_LEAN_TOOLS=0` to restore the preset.

## Quick add (the cheap-first cascade)

Typing an entry goes through three tiers, and stops at the first one that is
confident. The point is that the common cases never reach a model:

| Tier | What it is | Cost |
|---|---|---|
| 1 | Regex + a ~230-entry India-specific merchant lexicon (swiggy, blinkit, ola, jio, irctc…), amounts (`2k`, `1.5k`, `2 lakh`, `₹1,200`), relative dates (`yesterday`, `last friday`, `12 sep`) | free, instant |
| 2 | What **you** confirmed last time for this merchant or phrase | free, instant |
| 3 | One small constrained-JSON call to `claude-haiku-4-5` | 1 AI entry |

Tier 2 is what makes it get better with use: every confirmation teaches it, and
a correction *overrides* the built-in lexicon — if you file Swiggy under
Groceries because you order Instamart through it, the app stops arguing after
one correction.

**Tier 3 is off by default**, because it is the only thing here that spends real
money. The chat agent runs on your Claude Code subscription (no API billing);
tier 3 would use the `ANTHROPIC_API_KEY` from `api_keys.env` at roughly **₹0.11
per entry that needs it** (measured: ~670 input + ~110 output tokens on
`claude-haiku-4-5`). Turn it on with `COPILOT_ENABLED=1` in `.env`. With it off,
tiers 1-2 still work and an entry they can't parse just opens the chip
half-filled for you to finish.

## AI entries (credits)

Both quick add and chat draw on one balance of **AI entries**, so runaway usage
is bounded. Defaults: **100 free per month**, reset lazily on the 1st (IST), no
rollover. Three buckets are spent in order — free → earned → purchased — and
**purchased entries never expire**.

Two rules the code enforces rather than merely documents:

- **Adding transactions manually is never gated.** At zero entries the app is a
  fully working expense tracker; only the AI features pause. Chat returns `402`
  with `manual_entry_ok: true` and shows a calm card, not an error.
- **A failed parse is never charged.** Timeouts, API errors, low confidence, a
  stopped chat turn, a dropped connection — all refund automatically, while the
  real cost is still recorded in `ai_usage` so it stays measurable.

Inspect and adjust from the CLI:

```bash
python -m backend.users credits show sanjay
python -m backend.users credits grant sanjay 50 --bucket purchased
python -m backend.users credits set sanjay --free 0          # test the zero case
python -m backend.users credits ledger sanjay --limit 10
python -m backend.users credits reconcile                    # audit: balances vs ledger
python -m backend.users chat-disable sanjay
```

Per-call cost, tokens and which tier answered are logged as JSONL to
`data/logs/copilot.jsonl` (the typed text is **not** logged unless you set
`COPILOT_LOG_TEXT=1`). The number worth watching:

```bash
jq -r 'select(.event=="quick_add") | .tier' data/logs/copilot.jsonl | sort | uniq -c
```

If tiers 1-2 dominate, the cascade is doing its job.

> Earning entries by watching ads is **not available**: this is a PWA, AdMob is
> native-only, and any endpoint that grants credits because the browser claims
> an ad was watched is just a free-credit button. The server-side-verification
> seam exists (`backend/services/ads.py`) but is dormant — a future native
> wrapper plugs into it without a schema change.

## Importing your Money Manager history

Easiest way: export from the iOS app (More → Export → Excel), then **attach the
file in the Chat tab** — the agent previews it and imports after you confirm.

The raw API alternative:

```bash
# 1. preview: shows the detected column mapping + sample parsed rows
curl -F "file=@export.xlsx" http://localhost:8010/api/import/preview

# 2. commit with the mapping from the preview (adjust if needed)
curl -F "file=@export.xlsx" -F 'mapping={"date":"Period","account":"Accounts",...}' \
     http://localhost:8010/api/import/commit

# undo a bad import wholesale
curl -X DELETE http://localhost:8010/api/import/<batch_id>
```

Transfer-Out/Transfer-In row pairs are merged into single transfers; unpairable
ones fall back to expense/income tagged `[transfer-unmatched]`. The parser is
mapping-driven because RealByte's headers vary by version — if your real export
maps oddly, the preview shows exactly what to fix.

## How money is modeled

- Amounts are stored as **integer paise**; the API speaks rupees.
- One `transactions` table holds everything:
  `income | expense | transfer | lend | borrow | repay_in | repay_out`.
- Account balance = opening + inflows − outflows − transfers out + transfers in.
- Card outstanding = max(0, −balance). Person net = (lend − repay_in) − (borrow − repay_out), positive means they owe you.
- Spending charts count only pure `expense` rows — loans and card repayments
  never pollute them.
- Non-INR transactions store the original amount + currency and a manually
  entered INR value.

## Layout

```
backend/
  main.py           app factory, routers, static mount
  models.py         SQLAlchemy schema (the contract)
  services/         one write path shared by REST and agent tools
  routers/          /api/* endpoints incl. /api/chat (SSE)
  importer/         Money Manager xlsx importer
  agent/            system prompt, MCP tools, ClaudeSDKClient lifecycle
frontend/           no-build Preact + htm + Chart.js, served by FastAPI
data/money.db       SQLite (WAL) — delete it to start fresh
```

## Tests

```bash
~/miniconda3/envs/money/bin/python -m pytest        # 134 tests, ~1s
```

They run against a temp database (`DB_PATH` is redirected in `tests/conftest.py`
before any backend import, and a session fixture refuses to run otherwise), so
they never touch `data/money.db`.

## Roadmap (post-POC)

Budgets per category/month · recurring transactions · receipt-photo entry via
the agent · multi-person splits · card statement/due-date view · import UI ·
search · balance-over-time charts · React Native / native iOS port on this
backend.
