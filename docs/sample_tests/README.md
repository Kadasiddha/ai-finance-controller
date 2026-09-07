# Sample test scenarios

Each folder below is a self-contained, runnable example: `input/` has the
source files (clearly-labeled synthetic data, not real business data --
see the main README's note on synthetic test data), `output/` has the
actual files the current code produces from them
(`reconciliation_report.csv` from `app/report.py`, `analytics_summary.txt`
from `app/analytics.py`). The output files are real tool output, not
hand-written examples -- regenerate them any time with:

```bash
python3 - <<'EOF'
from pathlib import Path
from app.analytics import summarize
from app.parsers.bank_statement import parse_bank_statement
from app.parsers.order_ledger import parse_order_ledger
from app.parsers.payu_settlement import parse_payu_settlement
from app.parsers.razorpay_settlement import parse_settlement_report
from app.parsers.stripe_settlement import parse_stripe_settlement
from app.reconcile import reconcile
from app.report import write_csv_report

test_dir = Path("docs/sample_tests/test1_single_gateway")  # swap per scenario
sources = {
    "order_ledger": parse_order_ledger(test_dir / "input/orders_export.csv"),
    "razorpay_settlement": parse_settlement_report(test_dir / "input/razorpay_settlement.csv"),
    "bank_statement": parse_bank_statement(test_dir / "input/bank_statement.csv"),
}
result = reconcile(sources)
write_csv_report(result, test_dir / "output/reconciliation_report.csv")
print(summarize(result))
EOF
```

## Scenarios

- **[test1_single_gateway](test1_single_gateway/)** -- the simplest real
  case: one gateway (Razorpay), 3-way reconciliation, one clean match and
  one genuinely missing settlement. Start here.
- **[test2_multi_gateway](test2_multi_gateway/)** -- all 3 supported
  gateways (Razorpay, Stripe, PayU) reconciled together in a single call,
  against a shared order ledger and bank statement. Also the scenario
  that caught a real bug during end-to-end testing (see its README).
- **[test3_refund_netting](test3_refund_netting/)** -- a partial refund
  batched with its original payment nets automatically via signed
  amounts, plus an unattributable refund payout that correctly surfaces
  as an exception rather than being silently matched or dropped.
- **[test4_llm_adjudication](test4_llm_adjudication/)** -- tier 3 (LLM
  adjudication, opt-in, local Ollama): a many-to-many case neither
  deterministic tier can resolve. Includes a real, honest finding about
  live-model non-determinism.
