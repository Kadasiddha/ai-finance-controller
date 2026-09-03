# AI Finance Controller

A tiered reconciliation engine that matches the same money across three
records that never line up directly: an order ledger, a Razorpay settlement
report, and a bank statement.

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

**Working end-to-end (39 passing tests, verified from a clean venv):**
upload/read an order ledger, a Razorpay settlement report, and a bank
statement → get back confirmed matches and an honest, reason-tagged
exception list. `app/reconcile.py` is the entry point.

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
- **`app/reconcile.py`** — orchestrates both legs (ledger↔settlement via
  `order_id`, settlement↔bank via `settlement_utr`) and produces the
  confirmed-matches + exception-list output, with each exception carrying a
  structured `code` (`NO_COUNTERPART_FOUND`, `AMOUNT_MISMATCH`, ...) so
  exceptions can be counted/aggregated by cause, not just read one at a
  time. Proven against a fixture dataset covering clean matches, batched
  settlements, a truncated-UTR fuzzy-fallback case, a genuinely missing
  counterpart, and a misapplied-reference amount mismatch — both what
  should match and what should correctly refuse to.
- **`app/matching/adjudicate.py`** (tier 3, LLM adjudication) —
  deliberately unimplemented. Needs real leftover-after-tiers-1-2 examples
  to design the prompt against, not a guess at what "genuinely ambiguous"
  looks like here.

Run tests: `pip install -r requirements.txt && pytest tests/ -v`
