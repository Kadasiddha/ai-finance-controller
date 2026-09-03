"""Tests for the Razorpay settlement report parser.

The CSV fixture below uses column names and value shapes taken directly
from Razorpay's documented Settlement Recon API schema (see the parser's
module docstring for the source) -- it's a constructed fixture for testing
parser logic, not a stand-in for real business data anywhere in the actual
reconciliation output.
"""

from decimal import Decimal
from pathlib import Path

import pytest

from app.parsers.razorpay_settlement import parse_settlement_report

FIXTURE_CSV = """entity_id,type,debit,credit,amount,currency,fee,tax,on_hold,settled,created_at,settled_at,settlement_id,settlement_utr,order_id,payment_id,description
pay_ABC123,payment,0,200000,200000,INR,4000,720,False,True,1735689600,1735776000,setl_XYZ789,UTR8823,order_4471,pay_ABC123,
rfnd_DEF456,refund,50000,0,50000,INR,0,0,False,True,1735776000,1735776000,setl_XYZ789,UTR8823,order_4400,pay_OLD001,Partial refund
"""


@pytest.fixture
def settlement_csv(tmp_path: Path) -> Path:
    csv_path = tmp_path / "settlement_report.csv"
    csv_path.write_text(FIXTURE_CSV, encoding="utf-8")
    return csv_path


def test_parses_all_rows(settlement_csv: Path):
    transactions = parse_settlement_report(settlement_csv)
    assert len(transactions) == 2


def test_converts_paise_to_rupees(settlement_csv: Path):
    transactions = parse_settlement_report(settlement_csv)
    payment = next(t for t in transactions if t.source_row_id == "pay_ABC123")
    # Gross ₹2000.00, fee ₹40.00, tax ₹7.20 -- amount is the NET (what
    # actually lands in the bank), not the gross transaction amount.
    assert payment.amount == Decimal("1952.80")


def test_gross_amount_preserved_in_raw(settlement_csv: Path):
    transactions = parse_settlement_report(settlement_csv)
    payment = next(t for t in transactions if t.source_row_id == "pay_ABC123")
    assert payment.raw["amount"] == "200000"  # gross, in paise, untouched
    assert payment.raw["fee"] == "4000"
    assert payment.raw["tax"] == "720"


def test_links_order_id_and_settlement_utr(settlement_csv: Path):
    transactions = parse_settlement_report(settlement_csv)
    payment = next(t for t in transactions if t.source_row_id == "pay_ABC123")
    assert payment.order_id == "order_4471"
    assert payment.settlement_utr == "UTR8823"


def test_missing_required_column_raises(tmp_path: Path):
    bad_csv = tmp_path / "bad.csv"
    bad_csv.write_text("entity_id,type\npay_1,payment\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing expected columns"):
        parse_settlement_report(bad_csv)
