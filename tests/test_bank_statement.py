"""Tests for the bank statement parser.

Fixture narration strings mirror the real, documented patterns for Indian
bank NEFT/RTGS credit narrations (see the parser's module docstring) --
constructed for testing, not a stand-in for a real bank export.
"""

from decimal import Decimal
from pathlib import Path

import pytest

from app.parsers.bank_statement import extract_settlement_utr, parse_bank_statement

FIXTURE_CSV = """Date,Narration,Reference,Withdrawal,Deposit,Balance
2026-01-03,NEFT CR:RZRP173069230703 RAZORPAY SOFTWARE PRIVATE,,0,2933.00,152933.00
2026-01-05,ATM WDL CASH,,5000,0,147933.00
2026-01-06,NEFT CR:RZRP17306923 RAZORPAY SOFTWARE,,0,1000.00,148933.00
"""


@pytest.fixture
def statement_csv(tmp_path: Path) -> Path:
    csv_path = tmp_path / "bank_statement.csv"
    csv_path.write_text(FIXTURE_CSV, encoding="utf-8")
    return csv_path


class TestUtrExtraction:
    def test_extracts_full_utr_from_narration(self):
        narration = "NEFT CR:RZRP173069230703 RAZORPAY SOFTWARE PRIVATE"
        assert extract_settlement_utr(narration) == "RZRP173069230703"

    def test_extracts_truncated_utr(self):
        # Bank truncated the trailing digits -- still extract what's there.
        narration = "NEFT CR:RZRP17306923 RAZORPAY SOFTWARE"
        assert extract_settlement_utr(narration) == "RZRP17306923"

    def test_no_utr_present_returns_none(self):
        assert extract_settlement_utr("ATM WDL CASH") is None

    def test_empty_narration_returns_none(self):
        assert extract_settlement_utr("") is None


class TestParseBankStatement:
    def test_withdrawals_are_excluded(self, statement_csv: Path):
        transactions = parse_bank_statement(statement_csv)
        assert len(transactions) == 2  # the ATM withdrawal row is dropped

    def test_deposit_amount_and_date(self, statement_csv: Path):
        transactions = parse_bank_statement(statement_csv)
        first = transactions[0]
        assert first.amount == Decimal("2933.00")
        assert str(first.date) == "2026-01-03"

    def test_utr_populated_when_extractable(self, statement_csv: Path):
        transactions = parse_bank_statement(statement_csv)
        assert transactions[0].settlement_utr == "RZRP173069230703"

    def test_truncated_utr_is_still_captured(self, statement_csv: Path):
        transactions = parse_bank_statement(statement_csv)
        truncated_row = transactions[1]
        assert truncated_row.settlement_utr == "RZRP17306923"

    def test_missing_required_column_raises(self, tmp_path: Path):
        bad_csv = tmp_path / "bad.csv"
        bad_csv.write_text("Date,Narration\n2026-01-01,test\n", encoding="utf-8")
        with pytest.raises(ValueError, match="missing expected columns"):
            parse_bank_statement(bad_csv)
