# AI Finance Controller

A tiered reconciliation engine that matches the same money across records
that never line up directly: an order ledger, a payment gateway's settlement
report (Razorpay, Stripe, or PayU), and a bank statement. Reconciles any 2
or all 3 of these — select just the ledger and settlement report, just the
settlement report and bank statement, or all three; the engine only runs
the comparisons that make sense for whatever you select.

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
- Date-range filtering (reconcile just a chosen window, not everything
  ever uploaded) and a downloadable CSV report of the result.
- On-demand deeper analytics (match rate, per-source/per-code value
  breakdowns, mean/median exception size) for when a controller wants
  more than the raw match/exception list.

Explicitly **out of scope for the MVP** (may come later, not blocking this
version): fee/GST verification, duplicate detection, cash-flow anomaly
detection, an investigation/agent layer, audit trail, human approval
workflow, AI governance/evaluation tooling.

## Status

**Working end-to-end (103 passing tests, verified from a clean venv):**
upload/read any 2 or all 3 sources → get back confirmed matches and an
honest, reason-tagged exception list. `app/reconcile.py` is the entry
point — `reconcile({"order_ledger": [...], "razorpay_settlement": [...]})`
for a 2-way reconciliation, add `"bank_statement"` for the full 3-way.
Selecting two sources with no known relationship between them (e.g. ledger
+ bank, skipping settlement — there's no direct shared key between those
two) raises rather than silently doing nothing.

- **`app/parsers/razorpay_settlement.py`** — real. Built against Razorpay's
  documented Settlement Recon API schema, including paise→rupee conversion,
  Unix-timestamp handling, and computing a *signed net* amount
  (`(credit - debit) - fee - tax`) rather than the unsigned gross `amount`
  column — a refund row carries the same positive gross `amount` as the
  payment it reverses, with direction only ever signaled by `debit`/`credit`.
  Reading the gross column directly, as an earlier version did, silently
  treated every refund as more incoming money instead of money leaving.
- **`app/parsers/stripe_settlement.py`** — real, built against Stripe's
  documented Itemized Payout Reconciliation report
  (`payout_reconciliation.itemized.7`), the second gateway added to prove
  the reconciliation engine actually generalizes rather than being
  Razorpay-shaped by accident. Two genuine structural differences from
  Razorpay, both discovered from Stripe's own docs, not assumed: Stripe's
  `net` is already signed (a refund row is already negative, no
  `debit`/`credit` reconstruction needed) and already expressed in major
  currency units (no paise-style `/100`). A third difference changes
  behavior, not just parsing: Stripe's `trace_id` (the closest analog to
  Razorpay's settlement UTR) is bank-determined in format, often
  unavailable, and isn't documented to appear in a bank statement
  narration the way Razorpay's UTR reliably does — so a Stripe settlement
  row realistically reaches the bank statement via tier 2 (fuzzy
  amount+date), not tier 1 exact match. `bank_statement.py`'s narration
  extractor was deliberately left untouched (still Razorpay-UTR-specific)
  rather than inventing a fake recognizable Stripe narration pattern with
  no real evidence behind it.
- **`app/parsers/payu_settlement.py`** — real, built against PayU's
  documented Settlement Detail Range API response (confirmed against a
  full real example — PayU's dashboard also offers a CSV/XLSX settlement
  export, but its column names aren't documented anywhere public, so
  building against the JSON API response is the actually-grounded choice,
  not a guess). The third gateway, and the first non-CSV source in this
  project — a real structural stress test, not just another differently
  named format: a PayU settlement batch is nested JSON (batch-level
  fields plus an embedded `transaction[]` array), and it still reduces to
  a plain `list[Transaction]` with zero changes needed anywhere in
  `reconcile()`. A second real finding changes the parser's design:
  refunds/chargebacks are **batch-level aggregate fields**
  (`adjustmentAmount`, `refundAmount`, `chargebackAmount`, ...), not
  individual rows with their own order reference — confirmed by the math
  in PayU's own example (`settlementAmount == merchantNetAmount +
  adjustmentAmount`, exactly). To keep a batch's total honest, the parser
  emits one synthetic "unattributed adjustment" `Transaction` per batch
  (only when non-zero) — `order_id=None`, since that money genuinely
  isn't traceable to one order given what PayU's API exposes, but the
  amount is real and needed for `amounts_reconcile`'s batch-total check
  to mean anything. PayU's `utrNumber` is bare numeric (not `RZRP`-style
  prefixed), so it hits the same narration-recognition gap as Stripe's
  `trace_id` — `bank_statement.py`'s extractor was left untouched here
  too, for the same reason.
- **`app/parsers/order_ledger.py`** — built against Shopify's real,
  documented order-export CSV schema (still a stand-in for whatever the
  user's own system exports, but grounded in a real reference rather than
  invented).
- **`app/parsers/bank_statement.py`** — built against the common real shape
  of Indian bank CSV exports, with a settlement-UTR extractor that handles
  the realistic case: banks truncate narrations, dropping the trailing
  digits that make a UTR unique. A truncated UTR falls through to fuzzy
  matching instead of winning an exact match — that's the intended
  behavior, not a bug. Reads both `Deposit` and `Withdrawal` into a single
  signed `amount` (`deposit - withdrawal`) — an earlier version only read
  `Deposit` and skipped every `Withdrawal` row outright, meaning a refund
  or cashback payout leaving the account was invisible to the engine.
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
  at both the full 3-way and narrower 2-way selections. Adding Stripe and
  then PayU as a second and third gateway proved the design: each took
  only a new parser and two `Leg` declarations (e.g.
  `order_ledger`↔`stripe_settlement`, `stripe_settlement`↔`bank_statement`)
  — zero changes to `_run_leg`, `reconcile()`, or either matching tier,
  all already fully generic over source names. PayU in particular proved
  it holds even when the input format itself is structurally different
  (nested JSON, not a flat CSV row) — the pipeline only ever sees the
  normalized `list[Transaction]` output, never the source format.
- **`app/matching/adjudicate.py`** (tier 3, LLM adjudication) —
  deliberately unimplemented. Needs real leftover-after-tiers-1-2 examples
  to design the prompt against, not a guess at what "genuinely ambiguous"
  looks like here.
- **`app/evaluation.py`** — the actual point of this project, not an
  afterthought: "I matched 98%" is meaningless without knowing whether
  that 98% is *correct*. Takes a `ReconciliationResult` plus a
  `GroundTruthEntry` list (known-correct expected outcomes for a golden
  dataset) and computes auto-match precision, recall, false-match rate,
  exception coverage, and total unresolved rupee value — the metrics
  named in the project's own Evaluation Framework spec. Verified two ways:
  against the real fixture (precision/recall/false-match-rate all come out
  perfect, since the tiered design's whole point is never forcing a wrong
  match), and against a deliberately-wrong synthetic result to prove the
  evaluator actually catches a false match rather than just reporting
  zeros because the current engine happens to behave.
- **`app/filters.py`** — `filter_by_date_range(sources, start, end)`, run
  before `reconcile()`, not folded into it: reconciling "just this month"
  is filtering the input, not a new mode of the matching pipeline itself.
  Both bounds inclusive; either may be omitted for an open-ended range.
- **`app/report.py`** — `write_csv_report(result, path)`, the actual
  downloadable artifact. One CSV (not a matches/exceptions file pair) with
  a `record_type` column so it's filterable/sortable in Excel; matches
  first, then exceptions sorted highest-value-first, since the costliest
  discrepancy is what a controller should see first, not the last row.
- **`app/analytics.py`** — `summarize(result)`, on-demand descriptive
  stats: match rate, match counts by tier, exception counts/values by
  code, mean/median exception size. Pure Python + `Decimal`
  (`statistics.mean`/`median` handle `Decimal` natively) — deliberately
  not numpy, which would mean converting exact amounts to `float64` and
  reintroducing the rounding error this project exists to avoid, for a
  performance benefit that doesn't matter at these batch sizes anyway.
  Matched/exception value is reported **per source**, not summed across
  sources: a matched group spans sources whose amounts represent the same
  money at different points (a settlement row's net vs. the bank credit
  paying it out) — summing them would double-count the same rupees rather
  than describe a real total.

**Refunds and cashbacks (money flowing back out)** are handled — asked
about directly, and a real gap when first checked. Every amount in the
pipeline is now signed (`(credit - debit) - fee - tax` for settlement rows,
`deposit - withdrawal` for bank rows), so a refund naturally *subtracts*
from a batch total instead of needing bespoke refund-matching logic:
`amounts_reconcile`'s existing sum-based check already nets a refund
against the payment it reverses for free, as long as both rows share the
same `settlement_utr` batch. `REFUND_OR_CHARGEBACK_CONFLICT` remains
reserved for a refund that genuinely can't be netted this way (no shared
batch, no counterpart) — still not wired to real logic, since that needs
a real ambiguous example to design against, same as tier 3.

**Known gaps surfaced by adding a second and third gateway** (found, not
hidden — the whole point of testing the abstraction against something
genuinely different from Razorpay):
1. `Transaction.settlement_utr` and `bank_statement.py`'s
   `extract_settlement_utr` keep their Razorpay-flavored names even
   though the field now also holds Stripe's `trace_id` and PayU's
   `utrNumber` — a real, separate cleanup (rename to something
   gateway-neutral) deliberately not bundled into this pass, since it
   touches nearly every file in the repo for no functional gain on its
   own.
2. `app/matching/fuzzy.py`'s default date window/amount tolerance are
   still tuned to Razorpay's documented T+2 settlement cycle, not
   validated against Stripe's or PayU's (both vary by country/bank).
3. `Transaction` has no `currency` field — nothing would stop an
   accidental cross-currency match if two differently-denominated sources
   were reconciled together. Not exercised by any current test, which
   each stay single-currency.
4. **Multi-row settlement batches can't be reconstructed for a gateway
   without a bank-echoed reference**: fuzzy matching (tier 2) is strictly
   one-to-one, so if a gateway batches multiple rows (e.g. a charge and a
   refund, or a capture and PayU's synthetic adjustment row) into one
   payout and there's no shared exact-match key surviving to the bank
   side, the batch can't be netted into one match — each row correctly
   becomes its own `NO_COUNTERPART_FOUND` exception instead (proven in
   both `tests/test_reconcile_stripe.py` and
   `tests/test_reconcile_payu.py`), which is honest but not the single
   netted answer a human would recognize. For PayU specifically, this is
   really gap #1 wearing a different hat: `utrNumber` is a real, likely
   bank-narration-visible reference (unlike Stripe's `trace_id`), but the
   extractor doesn't yet know its bare-numeric shape — closing gap #1
   properly, with a real narration sample to confirm the pattern against,
   would likely close this one for PayU too.
5. **PayU's batch-level refunds/chargebacks aren't attributable to a
   specific order**: the synthetic adjustment row (`order_id=None`) keeps
   a batch's *total* honest, but PayU's own API doesn't expose which
   original order(s) a batch's refund/chargeback belongs to — a real
   limitation of PayU's data model, not a parsing shortcut taken here.

Run tests: `pip install -r requirements.txt && pytest tests/ -v`
