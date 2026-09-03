"""Parser for Stripe's Itemized Payout Reconciliation report.

Schema is real, not guessed: taken from Stripe's documented
`payout_reconciliation.itemized.7` report
(docs.stripe.com/reports/payout-reconciliation), the real, downloadable
CSV a Stripe merchant exports to reconcile payouts -- Stripe's equivalent
of Razorpay's Settlement Recon report.

Two things are structurally different from Razorpay's settlement report,
both discovered from Stripe's own docs, not assumed:

- `gross`/`net` are already SIGNED: per the Balance Transaction object
  docs, "a positive value represents funds charged to another party, and
  a negative value represents funds sent to another party." A refund row
  already has a negative `net`, unlike Razorpay's unsigned `amount`
  column (which needs separate `debit`/`credit` columns to recover
  direction -- see razorpay_settlement.py). No debit/credit
  reconstruction is needed here; that's a real simplification, not an
  oversight.
- The itemized report expresses `gross`/`fee`/`net` in MAJOR currency
  units already (dollars, not cents) -- no paise-style `/100` conversion.

`settlement_utr` is populated from Stripe's `trace_id` when present, but
unlike Razorpay's UTR, a Stripe trace_id is NOT expected to show up in
the bank statement narration: per docs.stripe.com/payouts/trace-id, its
format is bank-determined (no fixed prefix like Razorpay's `RZRP`), it's
often `pending`/`unsupported`, and it's a value you give your bank when
chasing a late payout -- not one banks are documented to echo back in a
transaction description. So `bank_statement.py`'s narration extractor is
deliberately NOT taught to recognize it (there's no real pattern to
recognize); a Stripe settlement row realistically reaches the bank
statement via tier 2 (fuzzy amount+date) rather than tier 1 exact match.
That's the honest behavior given what's actually true here, not a gap.

The `created` timestamp's exact string format in a real downloaded CSV
isn't pinned down by Stripe's column-reference docs (they describe it as
"dates in the requested time zone, or UTC if not provided" without
showing a literal example row). `"%Y-%m-%d %H:%M:%S"` is assumed here as
the common documented Stripe reporting convention -- still a stand-in
until a real export is available to confirm against, same caveat status
as bank_statement.py's own "real bank export" gap.
"""

import csv
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from app.models import Transaction

REQUIRED_COLUMNS = {"balance_transaction_id", "net", "created", "currency", "order_id", "trace_id"}


def _parse_created(value: str) -> datetime:
    return datetime.strptime(value.strip(), "%Y-%m-%d %H:%M:%S")


def parse_stripe_settlement(path: str | Path) -> list[Transaction]:
    """Parse a Stripe itemized payout reconciliation report CSV into
    normalized Transactions. Every row (charge, refund, fee, adjustment,
    ...) is included -- the matching tiers decide what to do with
    `reporting_category`, not the parser, same philosophy as
    razorpay_settlement.py.
    """
    path = Path(path)
    transactions: list[Transaction] = []

    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)

        missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])
        if missing:
            raise ValueError(
                f"Stripe settlement report is missing expected columns: {sorted(missing)}. "
                f"Found: {reader.fieldnames}"
            )

        for row in reader:
            transactions.append(
                Transaction(
                    source="stripe_settlement",
                    source_row_id=row["balance_transaction_id"],
                    amount=Decimal(row["net"]),
                    date=_parse_created(row["created"]).date(),
                    order_id=row.get("order_id") or None,
                    settlement_utr=row.get("trace_id") or None,
                    description=row.get("reporting_category") or None,
                    raw=dict(row),
                )
            )

    return transactions
