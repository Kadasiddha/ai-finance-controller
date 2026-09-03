"""Tests for the order ledger parser.

Fixture columns match Shopify's real, publicly documented order export
schema (see the parser's module docstring) -- constructed for testing,
not a stand-in for real business data.
"""

from decimal import Decimal
from pathlib import Path

import pytest

from app.parsers.order_ledger import parse_order_ledger

FIXTURE_CSV = """Name,Email,Financial Status,Paid at,Fulfillment Status,Currency,Total,Created at,Lineitem name
#4471,customer@example.com,paid,2026-01-01 10:00:00 +0530,fulfilled,INR,2000.00,2026-01-01 09:55:00 +0530,Widget A
#4472,customer2@example.com,pending,,unfulfilled,INR,1500.00,2026-01-02 09:00:00 +0530,Widget B
#4473,customer3@example.com,paid,2026-01-03 11:00:00 +0530,fulfilled,INR,980.00,2026-01-03 10:58:00 +0530,Widget C
"""


@pytest.fixture
def ledger_csv(tmp_path: Path) -> Path:
    csv_path = tmp_path / "orders_export.csv"
    csv_path.write_text(FIXTURE_CSV, encoding="utf-8")
    return csv_path


def test_only_paid_orders_are_included(ledger_csv: Path):
    transactions = parse_order_ledger(ledger_csv)
    order_ids = {t.order_id for t in transactions}
    assert order_ids == {"#4471", "#4473"}


def test_amounts_and_dates(ledger_csv: Path):
    transactions = parse_order_ledger(ledger_csv)
    order_4471 = next(t for t in transactions if t.order_id == "#4471")
    assert order_4471.amount == Decimal("2000.00")
    assert str(order_4471.date) == "2026-01-01"


def test_missing_required_column_raises(tmp_path: Path):
    bad_csv = tmp_path / "bad.csv"
    bad_csv.write_text("Name,Total\n#1,100\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing expected columns"):
        parse_order_ledger(bad_csv)
