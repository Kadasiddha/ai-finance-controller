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
or guessed at** — it goes on an exception list with an explicit reason
("no counterpart found," "amount off by more than fee tolerance," "duplicate
reference"), sorted by rupee value. The system must be able to answer both
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

Scaffolding only — not yet built against real data. Needs real Razorpay
export samples (order ledger + settlement report format) before the parsers
and matching logic can be written for real; no synthetic/mock data will be
used for this project.
