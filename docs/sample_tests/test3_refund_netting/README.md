# Test 3 -- refund netting and an unattributable refund

Demonstrates the signed-amounts design (`app/parsers/razorpay_settlement.py`,
`app/parsers/bank_statement.py`) that lets a refund net against its
original payment for free, without any bespoke refund-matching logic --
plus the honest failure mode when a refund payout can't be traced back to
anything.

## Scenario

| Item | What happens |
|---|---|
| Order #2001 (₹1000.00) | Settled (net ₹978.76 after fees), then **partially refunded ₹300.00** in the same settlement batch (same `settlement_utr`). The batch's signed total (978.76 + -300.00 = 678.76) matches the bank credit of ₹678.76 exactly -- `amounts_reconcile` accepts the group without any refund-specific code. |
| Order #2002 (₹500.00) | Ordinary clean match, no refund -- included as a control case. |
| Unattributed refund payout | A bank withdrawal (`REFUND DR:RZRP5000000099 ...`) referencing a UTR that has **no** corresponding settlement row anywhere. Correctly surfaces as its own `NO_COUNTERPART_FOUND` exception -- a real, legitimate "needs investigation" case, not silently matched or dropped. |

## Run it

```bash
python3 -c "
from app.parsers.order_ledger import parse_order_ledger
from app.parsers.razorpay_settlement import parse_settlement_report
from app.parsers.bank_statement import parse_bank_statement
from app.reconcile import reconcile

d = 'docs/sample_tests/test3_refund_netting/input'
sources = {
    'order_ledger': parse_order_ledger(f'{d}/orders_export.csv'),
    'razorpay_settlement': parse_settlement_report(f'{d}/razorpay_settlement.csv'),
    'bank_statement': parse_bank_statement(f'{d}/bank_statement.csv'),
}
result = reconcile(sources)
for m in result.matches:
    print('MATCH', m.tier, [(t.source, t.source_row_id, str(t.amount)) for t in m.transactions])
for e in result.exceptions:
    print('EXCEPTION', e.code, e.reason)
"
```

See `output/reconciliation_report.csv` and `output/analytics_summary.txt`
for the full real output.
