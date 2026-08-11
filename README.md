# Money Manager POC

A personal finance tracker built to replace the RealByte Money Manager iOS app,
with the two pain points fixed structurally and a Claude-powered chat agent that
knows your full spending history.

- **Credit cards done right** — purchases are expenses on the card; the bill
  payment is a one-tap *transfer* bank → card. It never shows up as an expense,
  so your stats stay honest.
- **Friend loans** — lend / borrow / settle with per-person running balances,
  partial settlements, and split bills ("I paid ₹1,200, Raj owes half").
- **Chat agent** — "I spent 500 on lunch and 2000 on groceries today" records
  both; "who owes me money?" answers from the database. It asks one clarifying
  question when something (like the account) is ambiguous. Runs on your Claude
  subscription via the Claude Agent SDK — no API billing.
- **File attachments in chat** (📎) — attach your Money Manager Excel export and
  the agent inspects it, summarizes what's inside, imports on your go-ahead, and
  can undo the batch conversationally. Attach a receipt photo and it reads the
  merchant/total/date (even the card used, if printed) and records the expense.

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
it crashes — no terminal needed. Logs go to `data/server.log`. Manage it with:

```bash
launchctl kickstart -k gui/$(id -u)/com.sanjaysuthraye.moneymanager   # restart
launchctl bootout   gui/$(id -u)/com.sanjaysuthraye.moneymanager      # stop (then ./run.sh works manually)
```

## Use it from your iPhone

```bash
./phone.sh    # prints the URLs + a QR code — scan it with the iPhone camera
```

- **Same Wi-Fi**: open `http://Sanjays-MacBook-Air.local:8010` in Safari, then
  Share → **Add to Home Screen** — you get a ₹ icon and a full-screen app.
- **From anywhere** (cellular): install **Tailscale** (free) on both devices —
  Mac App Store + iOS App Store — sign into the same account on both, toggle the
  iPhone VPN on, and use the `Anywhere` URL that `./phone.sh` prints once
  Tailscale is connected. Traffic stays private between your own devices.
- **Caveats**: the Mac must be awake to serve — keep it plugged in (on AC it
  never sleeps; on battery it sleeps after a minute). And since the app has no
  login screen, never expose it with router port-forwarding — Tailscale is the
  safe way to reach it remotely.

## Chat agent auth

The agent shares the Claude Code subscription login already on this machine —
zero setup. Alternatives (in `.env` at the project root):

- `CLAUDE_CODE_OAUTH_TOKEN=...` — explicit token from `claude setup-token`
- `AGENT_USE_API_KEY=1` — use the `ANTHROPIC_API_KEY` from `api_keys.env`
  (pay-per-use API billing; off by default on purpose)
- `AGENT_MODEL=haiku` — cheaper/faster model (default `sonnet`)

Only one credential is ever passed to the agent subprocess, so an exported
`ANTHROPIC_API_KEY` can't silently switch you to API billing.

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

## Roadmap (post-POC)

Budgets per category/month · recurring transactions · receipt-photo entry via
the agent · multi-person splits · card statement/due-date view · import UI ·
search · balance-over-time charts · React Native / native iOS port on this
backend.
