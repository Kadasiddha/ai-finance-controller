"""Parser for the business's own order ledger.

There's no universal standard for this the way there is for Razorpay's API
-- it's whatever the merchant's own system exports. Rather than invent a
schema from imagination, this is built against a real, publicly documented
one: Shopify's order export CSV
(help.shopify.com/en/manual/fulfillment/managing-orders/exporting-orders),
trimmed to the columns that matter for reconciliation. `Name` is the order
number as it displays in the store admin -- the natural value a merchant
would put in Razorpay's `receipt` field when creating the order, which is
the real link back to the settlement side (see razorpay_settlement.py).

This is still a stand-in for whatever the user's actual business system
exports -- swap the column mapping below once a real export is available,
the Shopify shape is a realistic reference point, not a guarantee of an
exact match.
"""

import csv
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from app.models import Transaction

REQUIRED_COLUMNS = {"Name", "Total", "Paid at", "Financial Status"}


def _parse_shopify_datetime(value: str):
    # Shopify exports e.g. "2026-01-01 14:32:10 +0530"
    return datetime.strptime(value.strip()[:19], "%Y-%m-%d %H:%M:%S")


def parse_order_ledger(path: str | Path) -> list[Transaction]:
    path = Path(path)
    transactions: list[Transaction] = []

    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)

        missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])
        if missing:
            raise ValueError(
                f"Order ledger is missing expected columns: {sorted(missing)}. "
                f"Found: {reader.fieldnames}"
            )

        for row in reader:
            if row["Financial Status"].strip().lower() != "paid":
                # Unpaid/pending orders have no money to reconcile yet --
                # only paid orders should ever be expected to show up on
                # the settlement/bank side.
                continue

            paid_at = row.get("Paid at") or ""
            if not paid_at:
                continue

            transactions.append(
                Transaction(
                    source="order_ledger",
                    source_row_id=row["Name"],
                    amount=Decimal(row["Total"]),
                    date=_parse_shopify_datetime(paid_at).date(),
                    order_id=row["Name"],
                    description=row.get("Lineitem name") or None,
                    raw=dict(row),
                )
            )

    return transactions
