# Test 1 -- single gateway (Razorpay), 3-way reconciliation

The simplest real case: one order ledger, one Razorpay settlement report,
one bank statement.

## Scenario

| Order | Status | Total | What happens |
|---|---|---|---|
| #1001 | paid | ₹1000.00 | Settles cleanly: matches its settlement row by `order_id`, and that settlement row matches its bank credit by `settlement_utr`. Two separate exact matches, chained through the settlement row. |
| #1002 | paid | ₹450.00 | No settlement row exists at all -- correctly becomes a `NO_COUNTERPART_FOUND` exception, not a forced/guessed match. |
| #1003 | pending | ₹300.00 | Excluded before reconciliation even starts -- not paid yet, nothing to reconcile. |

## Run it

```bash
python3 -c "
from app.parsers.order_ledger import parse_order_ledger
from app.parsers.razorpay_settlement import parse_settlement_report
from app.parsers.bank_statement import parse_bank_statement
from app.reconcile import reconcile

sources = {
    'order_ledger': parse_order_ledger('docs/sample_tests/test1_single_gateway/input/orders_export.csv'),
    'razorpay_settlement': parse_settlement_report('docs/sample_tests/test1_single_gateway/input/razorpay_settlement.csv'),
    'bank_statement': parse_bank_statement('docs/sample_tests/test1_single_gateway/input/bank_statement.csv'),
}
result = reconcile(sources)
print(f'{len(result.matches)} matches, {len(result.exceptions)} exceptions')
"
```

See `output/reconciliation_report.csv` for the actual CSV report and
`output/analytics_summary.txt` for the analytics breakdown.
