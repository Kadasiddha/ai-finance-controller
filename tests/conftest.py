"""Shared fixture dataset for reconciliation tests.

Realistic in shape (real Razorpay settlement schema, real Shopify
order-export shape, real Indian bank statement conventions) but synthetic
in content, built specifically to exercise this pipeline's matching and
exception-handling logic. Not a stand-in for real business data.

Scenarios, by order:
- #4471: clean 1:1 exact match on both legs.                    (positive)
- #4472 + #4473: batched settlement, one bank credit for two orders.
                                                                   (positive)
- #4474: bank narration truncates the UTR -- tier 1 (exact) can't match
  it, tier 2 (amount + date) catches it instead.                 (positive)
- #4475: paid in the ledger, but no settlement row exists at all.
                                                                   (negative)
- #4476: settlement exists, but the bank credit for that UTR is for a
  wildly different amount (a misapplied/duplicate reference) -- must be
  refused at both tiers, not force-matched.                      (negative)
- #4477: still "pending" in the ledger -- correctly excluded before
  reconciliation even starts, not a false positive or a false exception.
"""

import csv
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from app.parsers.bank_statement import parse_bank_statement
from app.parsers.order_ledger import parse_order_ledger
from app.parsers.razorpay_settlement import parse_settlement_report


def _epoch(y, m, d, hh=12, mm=0) -> int:
    return int(datetime(y, m, d, hh, mm, tzinfo=timezone.utc).timestamp())


ORDER_LEDGER_ROWS = [
    # Name,  Financial Status, Paid at,                     Total
    ("#4471", "paid", "2026-01-01 10:00:00 +0000", "2000.00"),
    ("#4472", "paid", "2026-01-02 09:00:00 +0000", "1000.00"),
    ("#4473", "paid", "2026-01-02 09:05:00 +0000", "980.00"),
    ("#4474", "paid", "2026-01-04 08:00:00 +0000", "1500.00"),
    ("#4475", "paid", "2026-01-05 08:00:00 +0000", "700.00"),
    ("#4476", "paid", "2026-01-06 08:00:00 +0000", "5000.00"),
    ("#4477", "pending", "", "300.00"),  # unpaid -- must be excluded entirely
]

# (entity_id, order_id, gross, fee, tax, utr, created_at epoch)
SETTLEMENT_ROWS = [
    ("pay_1", "#4471", "200000", "4000", "720", "RZRP1000000001", _epoch(2026, 1, 3)),
    ("pay_2", "#4472", "100000", "2000", "360", "RZRP1000000002", _epoch(2026, 1, 4)),
    ("pay_3", "#4473", "98000", "1960", "353", "RZRP1000000002", _epoch(2026, 1, 4)),
    ("pay_4", "#4474", "150000", "3000", "540", "RZRP1000000004", _epoch(2026, 1, 6)),
    # #4475 has no settlement row at all -- negative scenario.
    ("pay_6", "#4476", "500000", "10000", "1800", "RZRP1000000006", _epoch(2026, 1, 7)),
]

# (date, narration, deposit)
BANK_ROWS = [
    ("2026-01-03", "NEFT CR:RZRP1000000001 RAZORPAY SOFTWARE PRIVATE", "1952.80"),
    ("2026-01-04", "NEFT CR:RZRP1000000002 RAZORPAY SOFTWARE PRIVATE", "1933.27"),
    # Truncated UTR (bank cut the trailing "04") -- exact match will miss,
    # amount ties out (net 150000-3000-540 = 146460 paise = 1464.60) so
    # tier 2 (amount + date) should catch it instead.
    ("2026-01-06", "NEFT CR:RZRP10000000 RAZORPAY SOFTWARE", "1464.60"),
    # Correct UTR, but the WRONG amount -- net for pay_6 is 4882.00, this
    # bank row claims 3000.00. Must be refused at both tiers.
    ("2026-01-07", "NEFT CR:RZRP1000000006 RAZORPAY SOFTWARE PRIVATE", "3000.00"),
]


def _write_order_ledger_csv(path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Name", "Financial Status", "Paid at", "Total"])
        writer.writerows(ORDER_LEDGER_ROWS)


def _write_settlement_csv(path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["entity_id", "type", "debit", "credit", "fee", "tax", "created_at", "settlement_utr", "order_id"]
        )
        for entity_id, order_id, gross, fee, tax, utr, created_at in SETTLEMENT_ROWS:
            # Fixture rows are all payments (money in) -- credit=gross, debit=0.
            writer.writerow([entity_id, "payment", "0", gross, fee, tax, created_at, utr, order_id])


def _write_bank_statement_csv(path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Date", "Narration", "Reference", "Withdrawal", "Deposit", "Balance"])
        balance = Decimal("100000.00")
        for d, narration, deposit in BANK_ROWS:
            balance += Decimal(deposit)
            writer.writerow([d, narration, "", "0", deposit, str(balance)])


@pytest.fixture
def parsed_sources(tmp_path: Path) -> dict:
    ledger_csv = tmp_path / "orders_export.csv"
    settlement_csv = tmp_path / "settlement_report.csv"
    bank_csv = tmp_path / "bank_statement.csv"

    _write_order_ledger_csv(ledger_csv)
    _write_settlement_csv(settlement_csv)
    _write_bank_statement_csv(bank_csv)

    return {
        "order_ledger": parse_order_ledger(ledger_csv),
        "razorpay_settlement": parse_settlement_report(settlement_csv),
        "bank_statement": parse_bank_statement(bank_csv),
    }
