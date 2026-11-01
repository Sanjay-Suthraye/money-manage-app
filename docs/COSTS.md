# What it costs to run this on AWS and sell it on Play

Every figure below was re-derived from a primary source and then adversarially
re-checked by a second pass. Seven of the eight research areas came back with
**major corrections**, so where a number here disagrees with something said
earlier in planning, this file wins.

FX: **₹95.83/USD** (ECB reference, 2026-09-30). The rupee is 8.0% weaker
year-on-year, so any older rupee figure is optimistic.

---

## The headline

You are already fully on AWS: Lightsail in ap-south-1, Route 53, Amazon
Registrar, and an S3 + CloudFront static site on the apex. "Hosting on Amazon"
is not a migration — it is a question of what to add.

| | Monthly |
| --- | ---: |
| Infrastructure (verified, GST-inclusive) | **₹1,600 – 1,800** |
| Compliance and accounting | **₹2,400 – 6,150** |
| Your own time at steady state | **₹12,000 – 24,000** |

**Infrastructure is the smallest line by an order of magnitude.** The research
priced hosting to four decimal places and the business not at all. Read the
sections after the AWS table before concluding anything from it.

---

## AWS, monthly

Recommended launch configuration. The box steps up from 1 GB to 2 GB because
the chat path still spawns the `claude` CLI as a Node subprocess.

| Line | USD | Note |
| --- | ---: | --- |
| Lightsail 2 GB, ap-south-1 | $12.00 | 2 vCPU, 60 GB SSD. Static IPv4 **included** |
| Lightsail snapshots | $0.65 | $0.05/GB-mo, incremental |
| S3 backups + Litestream PUTs | $1.30 | request-priced, not storage-priced |
| Route 53 zone + .com renewal | $1.83 | **already paid** — incremental cost ₹0 |
| **Subtotal** | **$15.78** | |
| 18% GST | $2.84 | |
| **Total** | **$18.62** | **≈ ₹1,785/month** |

Tier reference, all ap-south-1, all 2 vCPU except the last:

| RAM | USD/mo | SSD | Transfer |
| ---: | ---: | ---: | ---: |
| 0.5 GB | $5 | 20 GB | 500 GB |
| **1 GB** (current) | **$7** | 40 GB | 1 TB |
| **2 GB** (recommended) | **$12** | 60 GB | 1.5 TB |
| 4 GB | $24 | 80 GB | 2 TB |
| 8 GB | $44 | 160 GB | 2.5 TB |
| 16 GB (4 vCPU) | $84 | 320 GB | 3 TB |

### Five things that are not obvious

1. **Mumbai gets half the advertised data transfer.** Every published Lightsail
   allowance is halved in ap-south-1 — 1 TB on the 1 GB bundle, not 2 TB. And
   **transfer IN counts against it.** Overage is **$0.13/GB** (the `$0.1093`
   figure that circulates is the general internet-egress rate, a different SKU).
2. **The current box is $7, not $5.** The $5 tier is 512 MB. Confirm in the
   console whether the running instance is on a grandfathered plan — it is the
   base of every total here and only the console settles it.
3. **Lightsail is exempt from the IPv4 charge** that EC2 has billed since Feb
   2024. The IPv4 is baked into the bundle ($1.50–$4 of it, visible by
   comparing the IPv6-only tiers). But an **unattached** static IP costs
   $0.005/hr — a silent ₹350/month if you rebuild the instance and keep the IP.
4. **The AWS Free Tier no longer exists in the form people remember.** Since 15
   July 2025 it is credit-based: $100 at signup plus up to $100 earned, valid 6
   months, after which a Free-plan account closes. Your existing account is
   ineligible. There is no 12-month free tier.
5. **Input tax credit is not automatic.** Reclaiming the 18% GST needs a GSTIN
   in AWS Tax Settings **and output liability to offset it against.** Pre-revenue
   you have none, so GST is a real cash cost until you are selling.

### Options that do not fit

- **App Runner is closed to new customers.** Its successor is ECS Express Mode.
- **Fargate and ECS are wrong for this app**, not on price (ARM 0.25/0.5 is
  ~$5.30/mo) but because storage is ephemeral: the SQLite WAL file is destroyed
  on every deploy, and EFS-backed SQLite breaks POSIX locking.
- **EC2 is not cheaper once you count everything.** t4g.small all-in at 744 h is
  $14.79 against Lightsail 2 GB at $12 with more SSD and 1.5 TB of transfer. A
  1-year RI gets EC2 to ~$11.57, which is a $0.43 saving for Savings Plan
  lock-in, VPC management and a separate IPv4 bill.
- **NAT Gateway, if you ever go private-subnet, is ~$41/month** before a single
  GB — more than the entire current stack.

**Stay on Lightsail.** The verification strengthened rather than weakened this.

---

## Google Play, and the tax stack nobody mentions

### One-time

| Item | Cost |
| --- | ---: |
| Play Console registration | $25 → **₹2,480 – 2,520** landed, incl. ~3.5% card forex markup and 18% GST on that markup |

### The 15% fee is not 15%

**Google does not remit output GST for developers located in India.** It does
that only for developers outside India. You determine and pay it yourself, on
GST-inclusive prices. On a ₹199/month subscription:

| | ₹ |
| --- | ---: |
| Gross (what the user pays, GST-inclusive) | 199.00 |
| − Output GST you remit (18/118 = 15.25%) | −30.36 |
| − Play service fee (15%) | −29.85 |
| − Reverse-charge GST on the Play fee (ITC-claimable) | −5.37 |
| **Net to you, GST-registered** | **≈ 138.80** |

That is an **effective take of ~30%, not 15%.** Google additionally withholds
0.1% income tax under s.194-O (5% without a PAN on file) and 1% GST TCS.

### Three rules that are easy to get wrong

- **The 15% first-$1M rate requires enrolment.** It is not automatic — you must
  join the reduced-fee tier and set up an Account Group. Unenrolled by default,
  this is a **15-percentage-point swing on every rupee**.
- **India stays on the legacy 15%/30% table until 30 Sep 2027.** The 2026
  "10% + 5% billing fee" structure covers only Australia, the EEA, Japan, the UK
  and the US.
- **User-choice billing in India is not worth it.** 15% − 4% = 11%, but a PSP
  takes ~2% plus GST on its fee, netting ~+1.9%. Play has supported UPI Autopay
  natively since Nov 2022, so the "UPI is required in India" argument is already
  satisfied inside Google billing.

### Organization account — a correction to earlier advice

> **The organization-account requirement for financial products and services
> has been in force worldwide since July 2024. There is no India grace period.**

Earlier planning treated a personal account as the default and an organization
account as an optional shortcut past closed testing. That is wrong if review
classifies this as a financial product.

The app is defensibly *not* one — no lending, no payments, no bank connectivity,
it only records money that moved elsewhere — and the listing must say exactly
that. But if classification goes the other way, the chain is: D-U-N-S number →
registered entity → Pvt Ltd or LLP incorporation (₹17,000–40,000 one-time,
estimated) → ROC filings, statutory audit and DIN KYC at ₹15,000–35,000/year.

Separately, the **30 Sep 2026 Brazil/Indonesia/Singapore/Thailand dates** that
circulate belong to *Android developer verification*, a different programme.

### Gates, with their real durations

- **Closed test: 12 testers opted in continuously for 14 days** — personal
  accounts created after 13 Nov 2023. Calendar-bound, cannot be compressed.
- **Production access review: "seven days or less, occasionally longer."**
- **Target API 36 has been mandatory since 31 Aug 2026** (extensions to 1 Nov
  2026). Bubblewrap's template must target it.
- **Play Integrity's free quota is 10,000 requests/day per APP**, not per Cloud
  project.
- **Account deletion** — both an in-app path and a public web URL — is a launch
  gate, and `delete_user()` does not exist yet.

---

## Compliance, which costs more than compute

| Line | Monthly ₹ | Basis |
| --- | ---: | --- |
| CA retainer (GSTR-1, GSTR-3B monthly; GSTR-9 annual) | 1,000 – 3,000 | estimate |
| ROC / audit / DIN KYC, **if** an entity is required | 1,250 – 2,900 | estimate |
| **Total** | **2,400 – 6,150** | **1.4 – 3.6× the AWS bill** |

GST **registration** is free; the **filing obligation** is not, and it is monthly
and mandatory from day one at zero revenue, with per-day late fees. At zero
revenue, cost-per-rupee-of-revenue is undefined — the compliance floor is
incurred whether or not the app ever sells anything.

Unpriced one-time items, all currently at ₹0 in every plan:

- **Trademark: ₹9,000–20,000** across class 9 and class 42 (TM-A e-filing is
  ₹4,500/class for an individual or recognised startup, ₹9,000 otherwise, plus
  attorney fees), 12–18 months to registration. This matters more than the fee
  suggests: the package name `com.skybolabs.money`, the verified App Links host
  and the install base are welded together, so **a rename after launch means a
  new listing with no upgrade path.**
- Privacy policy, terms, DPDP consent notice: ₹0 (template) to ₹25,000+
  (counsel). The privacy policy URL must be live before any track goes live.
- An off-laptop copy of `.env`, the SSH key and the Play upload keystore. All
  three currently exist in exactly one place.
- Bank current account and FIRC/merchant verification for foreign revenue.

---

## Risks with a rupee figure attached

**Egress abuse — the largest unhedged exposure in the plan.** The 2 GB bundle
includes 1.5 TB; overage is $0.13/GB. A sustained 100 Mbps flood is ~32 TB/month
= **(32,000 − 1,500) × $0.13 ≈ $3,965/month, about ₹4.5 lakh.** Lightsail offers
no spend ceiling and no WAF. The usual answer — Cloudflare in front — **does not
work as currently deployed**, because the origin IP is published in `README.md`
and still served by its own Caddy site block, so an attacker bypasses the proxy.
Closing it means firewalling 80/443 to Cloudflare ranges, which breaks the
legacy bare-IP PWA installs that block exists to preserve. Retiring the IP block
is therefore a security fix, not just tidiness.

**Free-tier farming converts into a global outage by design.** Each account gets
a 40-credit grant worth up to $0.08. There is no email verification, no OTP, no
device attestation. **Ten thousand scripted signups cost $800 and trip
`CREDIT_GLOBAL_DAILY_COST_CAP_USD = $25`, taking AI offline for every real user
for the rest of the IST day.** The cap is simultaneously the only cost control
and the attack's payload. Cheapest fix is email verification via SES — note SES
starts in a 200-message/day sandbox that must be lifted *before* launch.

**Two zero-cost DoS paths exist today.** `/api/chat/upload` accepts 20 MB × 5
attachments, writes no `ai_usage` row so the daily valve never counts it, and
nothing ever deletes an upload — one authenticated user fills the 40 GB disk, at
which point SQLite cannot checkpoint its WAL and the app stops accepting
transactions. Separately `importer.py` does `await file.read()` with no size
limit on a 1 GB box: one 500 MB POST is an OOM kill.

**Working capital.** Google pays out around the 15th of the following month
subject to a threshold, while Anthropic bills on consumption — a **30–45 day gap
between paying COGS and collecting revenue** that widens with growth.

**Monitoring costs more than the backup it watches.** CloudWatch custom metrics
in Mumbai are $0.30/metric-month; an S3 request-metrics filter is ~16 metrics ≈
$4.80/month, plus $0.10 per alarm-month, against ~$1.95 of backup.

---

## Unit economics — where the decision actually lives

Contribution per paying user is ≈ **₹81/month** (₹199 gross → ₹138.80 net, less
AI spend assumed at 3× the free grant).

Break-even on **AI cost alone**, before infrastructure, compliance or time, at
conversion rate *c* and average grant utilisation *u*:

| Grant utilisation *u* | Break-even conversion *c* |
| ---: | ---: |
| 1.00 | **8.7%** |
| 0.50 | **4.5%** |
| 0.22 | **2.0%** |

`settings.py` sizes the 40-credit grant to break even at ~1.6%. **That is only
reachable at ~20% average utilisation, and it ignores GST, the Play fee,
infrastructure, compliance and time.** The tax-and-fee stack alone moves true
break-even to ~2.2%.

At 10,000 MAU with 2% conversion and 50% utilisation, the app loses roughly
**₹25,000/month** — while the AWS bill everyone optimises is about ₹3,100. The
$25/day global cap bounds that loss at ₹72,000/month, and enforces the bound by
switching the product off for everyone.

### Two things that change the model

**The Phase 0 pricing fix doubled the true cost of a free user.** Users were
being debited ~2× the credits their turns actually cost, because `credits_svc`
was fed the `claude` CLI's inflated `total_cost_usd`. Fixing it was correct and
has shipped — but it means 40 credits now buys up to the full $0.08 of real API
spend rather than ~$0.039. **Cost per fully-utilised free user went from ~₹3.71
to ~₹7.68.** Every break-even figure above already reflects the post-fix number.

**Rewarded ads cannot fund the free tier, and as configured they lose money.**
One rewarded impression in India yields roughly $0.001–0.003; one credit costs
$0.002 of API spend. `services/ads.py` currently grants `REWARD_CREDITS = 15`
per impression — **3–6× underwater.** The impression-to-credit rate has to be
derived from measured eCPM, not chosen for generosity. Even at a sane rate, ads
recover perhaps 20–40% of free-tier cost (₹0.60–2.90 per MAU against ₹7.68).

### Measure these four before changing any price

Nothing here can substitute for them:

1. **Average free-grant utilisation** — the single number the go/no-go turns on,
   and no data exists for it.
2. **Conversion rate.**
3. **Indian ad eCPM**, measured, before `AD_PROVIDER` is ever set.
4. **Your own hourly rate** — at ₹800–1,500/hour the launch backlog is
   20–26 working days, i.e. **₹1.3–3.1 lakh one-time**, about 13 years of hosting.

---

## Evidence quality

Stated plainly, because some of this is load-bearing:

- **AWS and Play unit prices: strong.** Re-derived from the AWS Price List API
  feeds (published 11–29 Sep 2026) and Google's own policy pages.
- **Per-chat-turn AI cost: weak.** Corrected mean is **$0.010930/turn**, but
  n = 3, one user, one day, and one of the three rows was mispriced by the CLI.
  Everything downstream — the DAU ceiling, grant economics, the "AI dominates
  AWS by 10–30×" thesis — rests on that sample.
- **Indian trademark status: unverified.** IP India's public search is now
  OTP-gated and Justia/Trademarkia return 403. TMview's India mirror is good
  evidence but lags pending applications. **Commission a human class 9/36/42
  search before spending on any name.**
- **Compliance and professional-services figures are estimates**, not quotes.
  Confirm with a CA — in particular whether Play assesses its 15% on gross or
  net-of-GST, which this analysis could not settle.
