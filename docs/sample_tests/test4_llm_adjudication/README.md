# Test 4 -- tier 3 (LLM adjudication): the many-to-many case tiers 1-2 can't touch

Two orders settle via two **separate** Razorpay payout batches (different
`settlement_utr`s), but the bank, for its own processing reasons, wires
both amounts as a **single combined credit**. Neither deterministic tier
can resolve this:

- **Tier 1 (exact)** can't group the two settlement rows with the bank
  row -- they don't share a UTR, and the bank narration doesn't contain
  either one.
- **Tier 2 (fuzzy)** is strictly one-to-one (see `app/matching/fuzzy.py`'s
  own docstring) -- neither settlement row's individual amount (₹590.00
  or ₹410.00) is anywhere close to the bank credit (₹1000.00) within the
  default ₹5.00 tolerance.

Today, without tier 3, this deterministically becomes 3 separate
`NO_COUNTERPART_FOUND` exceptions (`output/reconciliation_report_without_tier3.csv`).
With tier 3 opted in, the LLM proposes that `pay_a` + `pay_b` together
match the bank credit, and -- critically -- that proposal is
**independently re-verified with `amounts_reconcile()`** before being
accepted (see `app/matching/adjudicate.py`'s module docstring: the model
proposes, code verifies, never the other way around).
`output/reconciliation_report_with_tier3.csv` shows the result: one
`tier=llm` match spanning all 3 transactions.

## A real, honest finding: the live model is not deterministic

The committed `output/reconciliation_report_with_tier3.csv` was
generated with a **fixed example LLM response**, not a live call --
same reasoning `tests/test_adjudicate.py`/`tests/test_reconcile_llm_tier.py`
use, for the same reason: reproducible, instant, no Ollama dependency to
view this example.

A **real** local Ollama call (`qwen2.5:7b-instruct`) was also run
against this exact scenario during development. It's genuinely
non-deterministic run to run: sometimes it correctly proposes the
`pay_a` + `pay_b` combination with confidence ~0.9 and a coherent
reasoning string; other times, on the same prompt, it declines and
proposes nothing at all. Both outcomes are handled correctly by
design -- a decline just means the 3 transactions stay as exceptions,
never a wrong or forced match. This variance is exactly why the
propose-then-verify split matters: an LLM's *judgment* about which
transactions to group is allowed to vary, but the *arithmetic* that
decides whether a proposal is actually accepted never does.

## Run it (with a real Ollama server)

Requires Ollama running locally with `qwen2.5:7b-instruct` pulled (`ollama pull qwen2.5:7b-instruct`).

```bash
python3 -c "
from app.parsers.order_ledger import parse_order_ledger
from app.parsers.razorpay_settlement import parse_settlement_report
from app.parsers.bank_statement import parse_bank_statement
from app.matching.adjudicate import adjudicate
from app.reconcile import reconcile

d = 'docs/sample_tests/test4_llm_adjudication/input'
sources = {
    'order_ledger': parse_order_ledger(f'{d}/orders_export.csv'),
    'razorpay_settlement': parse_settlement_report(f'{d}/razorpay_settlement.csv'),
    'bank_statement': parse_bank_statement(f'{d}/bank_statement.csv'),
}
result = reconcile(sources, adjudicate_fn=adjudicate)  # real Ollama call
print(f'{len(result.matches)} matches, {len(result.exceptions)} exceptions')
for m in result.matches:
    print(m.tier, m.confidence, m.reasoning)
"
```

Run it without `adjudicate_fn` (or just call `reconcile(sources)`) to see
today's deterministic-only behavior instead.
