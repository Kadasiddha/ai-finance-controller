"""Tests for the Stripe itemized payout reconciliation report parser.

The CSV fixture below uses column names taken directly from Stripe's
documented `payout_reconciliation.itemized.7` report (see the parser's
module docstring for the source) -- a constructed fixture for testing
parser logic, not a stand-in for real business data.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.parsers.stripe_settlement import parse_stripe_settlement

FIXTURE_CSV = """balance_transaction_id,reporting_category,created,currency,gross,fee,net,order_id,trace_id,automatic_payout_id
txn_charge001,charge,2026-01-03 14:30:00,usd,50.00,1.75,48.25,order_9001,,po_batch01
txn_refund001,refund,2026-01-05 09:00:00,usd,-20.00,0,-20.00,order_9002,,po_batch01
txn_charge002,charge,2026-01-04 11:00:00,usd,120.00,3.78,116.22,order_9003,7UF6L35ME6bh3bk3cj51L7o93ky79X5Pb58i5LO1e,po_batch01
"""


@pytest.fixture
def settlement_csv(tmp_path: Path) -> Path:
    csv_path = tmp_path / "stripe_settlement.csv"
    csv_path.write_text(FIXTURE_CSV, encoding="utf-8")
    return csv_path


def test_parses_all_rows(settlement_csv: Path):
    transactions = parse_stripe_settlement(settlement_csv)
    assert len(transactions) == 3


def test_charge_amount_is_signed_net_directly_no_conversion(settlement_csv: Path):
    transactions = parse_stripe_settlement(settlement_csv)
    charge = next(t for t in transactions if t.source_row_id == "txn_charge001")
    # net is already major-unit and already signed -- unlike Razorpay's
    # paise-and-debit/credit reconstruction, this is just Decimal(net).
    assert charge.amount == Decimal("48.25")
    assert charge.date == date(2026, 1, 3)


def test_refund_amount_is_negative_directly_from_signed_net(settlement_csv: Path):
    # No debit/credit arithmetic needed -- Stripe's net is already negative
    # for money leaving the account, unlike Razorpay's unsigned amount
    # column (see test_razorpay_settlement.py's equivalent refund test).
    transactions = parse_stripe_settlement(settlement_csv)
    refund = next(t for t in transactions if t.source_row_id == "txn_refund001")
    assert refund.amount == Decimal("-20.00")


def test_order_id_and_trace_id_are_linked(settlement_csv: Path):
    transactions = parse_stripe_settlement(settlement_csv)
    charge = next(t for t in transactions if t.source_row_id == "txn_charge002")
    assert charge.order_id == "order_9003"
    assert charge.settlement_utr == "7UF6L35ME6bh3bk3cj51L7o93ky79X5Pb58i5LO1e"


def test_missing_trace_id_is_none_not_a_missing_column_error(settlement_csv: Path):
    transactions = parse_stripe_settlement(settlement_csv)
    charge = next(t for t in transactions if t.source_row_id == "txn_charge001")
    assert charge.settlement_utr is None


def test_gross_and_fee_preserved_in_raw(settlement_csv: Path):
    transactions = parse_stripe_settlement(settlement_csv)
    charge = next(t for t in transactions if t.source_row_id == "txn_charge001")
    assert charge.raw["gross"] == "50.00"
    assert charge.raw["fee"] == "1.75"


def test_missing_required_column_raises(tmp_path: Path):
    bad_csv = tmp_path / "bad.csv"
    bad_csv.write_text("balance_transaction_id,net\ntxn_1,10.00\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing expected columns"):
        parse_stripe_settlement(bad_csv)
