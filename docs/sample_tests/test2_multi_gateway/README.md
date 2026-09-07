# Test 2 -- three gateways reconciled together

All 3 supported settlement gateways (Razorpay, Stripe, PayU) reconciled
in a single `reconcile()` call, against one shared order ledger and one
shared bank statement -- the scenario a business that's migrated gateways
or runs several in parallel would actually have.

## Scenario

| Order | Gateway | What happens |
|---|---|---|
| #7001 | Razorpay | Clean match: exact on `order_id`, exact on `settlement_utr` (Razorpay's UTR is recognized in the bank narration). |
| #7002 | Stripe | Clean match: exact on `order_id`, but falls through to **fuzzy** (amount+date) for the settlement↔bank leg -- Stripe's `trace_id` isn't expected to appear in a bank narration (see `app/parsers/stripe_settlement.py`), so this is the realistic path, not a failure. |
| #7003 | PayU | Same shape as Stripe: exact on `order_id`, fuzzy on settlement↔bank -- PayU's UTR is bare numeric and not yet recognized by the narration extractor either. |
| #7004 | Razorpay (intended) | No settlement row anywhere, in any gateway -- correctly becomes exactly **one** `NO_COUNTERPART_FOUND` exception, not three (one per gateway it doesn't belong to). |
| #7005 | -- | Pending, excluded before reconciliation starts. |
| #7006 | Razorpay | Settlement exists and matches its order, but the bank credit for that UTR is for the wrong amount (₹3000.00 instead of ₹3124.48) -- refused as `AMOUNT_MISMATCH`, not force-matched. |
| (unrelated) | -- | An ATM withdrawal in the bank statement, unrelated to any gateway -- correctly becomes its own exception (genuinely nothing to reconcile it against), not silently dropped. |

## A real bug this scenario caught

Running 3 gateways simultaneously against a *shared* order ledger and
bank statement is a genuinely different case from testing each gateway
in isolation (which is all `tests/test_reconcile_stripe.py` and
`tests/test_reconcile_payu.py` do on their own). Building this scenario
end-to-end surfaced a real bug: `order_ledger`/`bank_statement` rows that
matched via their *real* gateway were **also** being reported as
`NO_COUNTERPART_FOUND` for the other two gateways they were never
associated with -- e.g. order #7001 (Razorpay) showing up both as a
confirmed match *and* an exception saying "no counterpart in
stripe_settlement, payu_settlement." Fixed in `app/reconcile.py`
(exceptions are now tracked per `(row, key)`, not just per row -- a match
under a given key suppresses "missing" reports under that *same* key,
while a genuinely different relationship, like a settlement row's
separate order-leg vs. bank-leg, still correctly gets its own exception).
Covered by `tests/test_reconcile_multi_gateway.py`.

## Run it

```bash
python3 -c "
from app.parsers.order_ledger import parse_order_ledger
from app.parsers.razorpay_settlement import parse_settlement_report
from app.parsers.stripe_settlement import parse_stripe_settlement
from app.parsers.payu_settlement import parse_payu_settlement
from app.parsers.bank_statement import parse_bank_statement
from app.reconcile import reconcile

d = 'docs/sample_tests/test2_multi_gateway/input'
sources = {
    'order_ledger': parse_order_ledger(f'{d}/orders_export.csv'),
    'razorpay_settlement': parse_settlement_report(f'{d}/razorpay_settlement.csv'),
    'stripe_settlement': parse_stripe_settlement(f'{d}/stripe_settlement.csv'),
    'payu_settlement': parse_payu_settlement(f'{d}/payu_settlement.json'),
    'bank_statement': parse_bank_statement(f'{d}/bank_statement.csv'),
}
result = reconcile(sources)
print(f'{len(result.matches)} matches, {len(result.exceptions)} exceptions')
"
```

See `output/reconciliation_report.csv` and `output/analytics_summary.txt`
for the full real output.
