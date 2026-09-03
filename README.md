# AI Finance Controller

A tiered reconciliation engine that matches the same money across records
that never line up directly: an order ledger, a Razorpay settlement report,
and a bank statement. Reconciles any 2 or all 3 of these — select just the
ledger and settlement report, just the settlement report and bank
statement, or all three; the engine only runs the comparisons that make
sense for whatever you select.

## The problem

Settlements are batched (one bank credit = many orders, no order number in
it), fees are deducted before payout (₹2,000 in sales arrives as ₹1,953),
settlement lands T+2 after the payment, and refunds/chargebacks net out of
unrelated batches. Reconciling these by hand is exactly the "spreadsheet
archaeology" a financial controller does every day — this project automates
that loop.

## How it works — three passes, cost-ascending

1. **Exact matching** — reference numbers line up directly. Fast, free,
   provably correct.
2. **Fuzzy matching** — rules and tolerances (amount minus fee tolerance,
   date within the T+2 settlement window). Still no LLM involved.
3. **LLM adjudication** — only on whatever survives passes 1 and 2. Reserved
   for genuinely ambiguous cases (a partial refund tangled into a split
   settlement) where real reasoning is needed, not pattern matching.

Anything that doesn't confidently match at any tier is **never force-matched
or guessed at** — it goes on an exception list with a structured code
(`NO_COUNTERPART_FOUND`, `AMOUNT_MISMATCH`, ...) plus a human-readable
reason, sorted by rupee value. The system must be able to answer both
*"why did you match these two transactions?"* and *"why did you refuse to
match these two?"* — explainability on both the accept and reject path is
the core design principle, not an afterthought.

## MVP scope (this repo)

- Data normalization for the three real source formats (order ledger,
  Razorpay settlement report, bank statement).
- The three-pass matching pipeline above.
- A confirmed-matches output and an honest, reason-tagged exception list.

Explicitly **out of scope for the MVP** (may come later, not blocking this
version): fee/GST verification, duplicate detection, cash-flow anomaly
detection, an investigation/agent layer, audit trail, human approval
workflow, AI governance/evaluation tooling.

## Status

**Working end-to-end (43 passing tests, verified from a clean venv):**
upload/read any 2 or all 3 sources → get back confirmed matches and an
honest, reason-tagged exception list. `app/reconcile.py` is the entry
point — `reconcile({"order_ledger": [...], "razorpay_settlement": [...]})`
for a 2-way reconciliation, add `"bank_statement"` for the full 3-way.
Selecting two sources with no known relationship between them (e.g. ledger
+ bank, skipping settlement — there's no direct shared key between those
two) raises rather than silently doing nothing.

- **`app/parsers/razorpay_settlement.py`** — real. Built against Razorpay's
  documented Settlement Recon API schema, including paise→rupee conversion,
  Unix-timestamp handling, and computing the *net* settled amount
  (`amount - fee - tax`) rather than the gross transaction amount — the
  net is what actually reaches the bank, which is the whole point.
- **`app/parsers/order_ledger.py`** — built against Shopify's real,
  documented order-export CSV schema (still a stand-in for whatever the
  user's own system exports, but grounded in a real reference rather than
  invented).
- **`app/parsers/bank_statement.py`** — built against the common real shape
  of Indian bank CSV exports, with a settlement-UTR extractor that handles
  the realistic case: banks truncate narrations, dropping the trailing
  digits that make a UTR unique. A truncated UTR falls through to fuzzy
  matching instead of winning an exact match — that's the intended
  behavior, not a bug.
- **`app/matching/exact.py`** — tier 1, plus `amounts_reconcile`: a shared
  reference alone isn't proof of a real match (a duplicate/misapplied UTR
  can share a key by error) — a matched group's amounts must actually add
  up, or it's refused rather than trusted. Rejected groups are returned as
  separate incidents (not flattened into one list), so two unrelated
  misapplied references never get merged into a single confusing exception.
- **`app/matching/fuzzy.py`** — tier 2, amount tolerance + T+2 date window.
- **`app/reconcile.py`** — a `Leg` is one pairwise relationship between two
  sources linked by a known shared key (`order_id` for ledger↔settlement,
  `settlement_utr` for settlement↔bank). Only legs where both sides are
  present in the caller's selected sources run — pass 2 sources and get a
  2-way reconciliation, pass all 3 and both legs run, chained through the
  settlement transaction that naturally participates in both. Adding a new
  known source means adding its leg(s) to `KNOWN_LEGS`, not rewriting the
  pipeline — this deliberately does not attempt to support arbitrary
  unknown source types with unknown keys, since guessing at how to relate
  two unfamiliar sources would be exactly the kind of invented behavior
  this project's principles reject. Produces the confirmed-matches +
  exception-list output, with each exception carrying a structured `code`
  (`NO_COUNTERPART_FOUND`, `AMOUNT_MISMATCH`, ...) so exceptions can be
  counted/aggregated by cause. Proven against a fixture dataset covering
  clean matches, batched settlements, a truncated-UTR fuzzy-fallback case,
  a genuinely missing counterpart, and a misapplied-reference amount
  mismatch — both what should match and what should correctly refuse to,
  at both the full 3-way and narrower 2-way selections.
- **`app/matching/adjudicate.py`** (tier 3, LLM adjudication) —
  deliberately unimplemented. Needs real leftover-after-tiers-1-2 examples
  to design the prompt against, not a guess at what "genuinely ambiguous"
  looks like here.

Run tests: `pip install -r requirements.txt && pytest tests/ -v`
