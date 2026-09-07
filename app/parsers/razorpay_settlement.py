"""Parser for Razorpay's settlement reconciliation report.

Schema is real, not guessed: taken directly from Razorpay's documented
Settlement Recon API response (razorpay.com/docs/api/settlements/fetch-recon/),
which is also what the dashboard's downloadable combined settlement CSV
export uses. Column names below match that documented schema exactly.

Four things about Razorpay's convention that are easy to get wrong:
- Amounts (`amount`, `fee`, `tax`, `debit`, `credit`) are in currency
  subunits (paise for INR), not rupees -- must divide by 100.
- `created_at`/`settled_at` are Unix timestamps, not date strings.
- `amount` is the unsigned GROSS transaction amount -- it does NOT tell you
  direction. A refund row has the same positive `amount` as the payment it
  reverses; the only place direction actually lives is `debit`/`credit`
  (one of the two is populated, the other is zero, depending on whether
  money left or arrived). Using `amount` directly, as an earlier version
  of this parser did, silently treats every refund as if it were more
  incoming money instead of money going back out -- a real bug found and
  fixed here.
- `Transaction.amount` is therefore `(credit - debit) - fee - tax`: signed,
  and net of fee/tax -- what actually moves, in the direction it actually
  moves. This is also what makes refund-aware netting work for free: when
  several settlement rows (a payment plus a refund against it) are summed
  by `amounts_reconcile` in a shared-UTR batch, a correctly-signed refund
  naturally subtracts rather than needing special-cased refund logic.
  Original gross `amount`/`debit`/`credit`/`fee`/`tax` are preserved in
  `raw` for reference.

This is the file that links the other two sources together:
- `order_id` ties a settlement row back to the order ledger.
- `settlement_utr` ties a *group* of settlement rows (everything batched
  into one payout) to the single lump-sum credit in the bank statement.
"""

import csv
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from app.models import Transaction

REQUIRED_COLUMNS = {
    "entity_id",
    "type",
    "debit",
    "credit",
    "fee",
    "tax",
    "created_at",
    "settlement_utr",
    "order_id",
}


_CENTS = Decimal("0.01")


def _paise_to_rupees(value: str) -> Decimal:
    if not value:
        return Decimal("0.00")
    # Plain division by 100 doesn't guarantee 2 decimal places -- Decimal
    # keeps only the precision the exact division actually needs, so
    # 720/100 comes out as 7.2 (one decimal place) while 4000/100 comes
    # out as 40 (zero). Downstream arithmetic (fee/tax subtraction) then
    # inherits whichever operand's scale is smallest, producing a `net`
    # amount with an inconsistent, sometimes-wrong-looking number of
    # decimal places for what's supposed to be a fixed 2-decimal currency
    # value. Quantizing here guarantees every rupee amount this parser
    # produces is consistently 2 decimal places, the same way real money
    # is always represented.
    return (Decimal(value) / Decimal(100)).quantize(_CENTS)


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromtimestamp(int(value), tz=timezone.utc)


def parse_settlement_report(path: str | Path) -> list[Transaction]:
    """Parse a Razorpay combined settlement report CSV into normalized
    Transactions. Every row (payment, refund, transfer, adjustment) is
    included -- the matching tiers decide what to do with `type`, not
    the parser.
    """
    path = Path(path)
    transactions: list[Transaction] = []

    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)

        missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])
        if missing:
            raise ValueError(
                f"Settlement report is missing expected columns: {sorted(missing)}. "
                f"Found: {reader.fieldnames}"
            )

        for row in reader:
            credit = _paise_to_rupees(row["credit"])
            debit = _paise_to_rupees(row["debit"])
            fee = _paise_to_rupees(row["fee"])
            tax = _paise_to_rupees(row["tax"])
            net = (credit - debit) - fee - tax

            transactions.append(
                Transaction(
                    source="razorpay_settlement",
                    source_row_id=row["entity_id"],
                    amount=net,
                    date=_parse_timestamp(row["created_at"]).date(),
                    order_id=row.get("order_id") or None,
                    settlement_utr=row.get("settlement_utr") or None,
                    description=row.get("description") or row["type"],
                    raw=dict(row),
                )
            )

    return transactions
