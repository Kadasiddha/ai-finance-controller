"""Parser for Razorpay's settlement reconciliation report.

Schema is real, not guessed: taken directly from Razorpay's documented
Settlement Recon API response (razorpay.com/docs/api/settlements/fetch-recon/),
which is also what the dashboard's downloadable combined settlement CSV
export uses. Column names below match that documented schema exactly.

Two things about Razorpay's convention that are easy to get wrong:
- Amounts (`amount`, `fee`, `tax`, `debit`, `credit`) are in currency
  subunits (paise for INR), not rupees -- must divide by 100.
- `created_at`/`settled_at` are Unix timestamps, not date strings.

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
    "amount",
    "fee",
    "tax",
    "created_at",
    "settlement_utr",
    "order_id",
}


def _paise_to_rupees(value: str) -> Decimal:
    if not value:
        return Decimal("0")
    return Decimal(value) / Decimal(100)


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
            transactions.append(
                Transaction(
                    source="razorpay_settlement",
                    source_row_id=row["entity_id"],
                    amount=_paise_to_rupees(row["amount"]),
                    date=_parse_timestamp(row["created_at"]).date(),
                    order_id=row.get("order_id") or None,
                    settlement_utr=row.get("settlement_utr") or None,
                    description=row.get("description") or row["type"],
                    raw=dict(row),
                )
            )

    return transactions
