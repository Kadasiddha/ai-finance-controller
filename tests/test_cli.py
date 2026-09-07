"""Tests for the minimal CLI (app/cli.py), run through main(argv) directly
rather than a subprocess -- faster, and stdout/stderr are easy to assert
on via pytest's capsys.
"""

import csv
from pathlib import Path

import pytest

from app.cli import main
from tests.conftest import _write_bank_statement_csv, _write_order_ledger_csv, _write_settlement_csv


@pytest.fixture
def three_source_csvs(tmp_path: Path) -> dict:
    ledger = tmp_path / "orders.csv"
    settlement = tmp_path / "settlement.csv"
    bank = tmp_path / "bank.csv"
    _write_order_ledger_csv(ledger)
    _write_settlement_csv(settlement)
    _write_bank_statement_csv(bank)
    return {"ledger": ledger, "settlement": settlement, "bank": bank}


def test_prints_match_and_exception_counts(three_source_csvs, capsys):
    exit_code = main(
        [
            "--order-ledger", str(three_source_csvs["ledger"]),
            "--razorpay-settlement", str(three_source_csvs["settlement"]),
            "--bank-statement", str(three_source_csvs["bank"]),
        ]
    )
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "matches" in out
    assert "exceptions" in out


def test_fewer_than_two_sources_errors_cleanly_not_a_traceback(tmp_path: Path, capsys):
    ledger = tmp_path / "orders.csv"
    _write_order_ledger_csv(ledger)

    exit_code = main(["--order-ledger", str(ledger)])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "error:" in captured.err


def test_report_flag_writes_a_real_csv_file(three_source_csvs, tmp_path: Path):
    report_path = tmp_path / "out" / "report.csv"
    exit_code = main(
        [
            "--order-ledger", str(three_source_csvs["ledger"]),
            "--razorpay-settlement", str(three_source_csvs["settlement"]),
            "--bank-statement", str(three_source_csvs["bank"]),
            "--report", str(report_path),
        ]
    )
    assert exit_code == 0
    assert report_path.exists()
    with report_path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) > 0


def test_summary_flag_prints_analytics_fields(three_source_csvs, capsys):
    exit_code = main(
        [
            "--order-ledger", str(three_source_csvs["ledger"]),
            "--razorpay-settlement", str(three_source_csvs["settlement"]),
            "--bank-statement", str(three_source_csvs["bank"]),
            "--summary",
        ]
    )
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "Analytics summary" in out
    assert "match_rate_by_transaction_count" in out


def test_verbose_flag_prints_individual_matches(three_source_csvs, capsys):
    exit_code = main(
        [
            "--order-ledger", str(three_source_csvs["ledger"]),
            "--razorpay-settlement", str(three_source_csvs["settlement"]),
            "--bank-statement", str(three_source_csvs["bank"]),
            "--verbose",
        ]
    )
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "MATCH [exact]" in out


def test_date_filter_narrows_the_reconciled_set(three_source_csvs, capsys):
    exit_code = main(
        [
            "--order-ledger", str(three_source_csvs["ledger"]),
            "--razorpay-settlement", str(three_source_csvs["settlement"]),
            "--bank-statement", str(three_source_csvs["bank"]),
            "--start-date", "2026-01-01",
            "--end-date", "2026-01-03",
            "--verbose",
        ]
    )
    out = capsys.readouterr().out
    assert exit_code == 0
    # #4474/#4476's later-dated rows must not appear once the window excludes them.
    assert "#4476" not in out


def test_invalid_date_format_errors_cleanly(three_source_csvs):
    with pytest.raises(SystemExit):
        main(
            [
                "--order-ledger", str(three_source_csvs["ledger"]),
                "--razorpay-settlement", str(three_source_csvs["settlement"]),
                "--start-date", "not-a-date",
            ]
        )


def test_llm_flag_does_not_crash_when_nothing_is_left_to_adjudicate(tmp_path: Path, capsys):
    # A deliberately minimal, fully-clean fixture (NOT the shared
    # conftest one, which intentionally includes unmatched/negative
    # scenarios) -- everything here resolves at tier 1, so both sides
    # are genuinely empty when adjudicate() is invoked and it hits its
    # own fast-path (see app/matching/adjudicate.py) without making a
    # real network call. Using conftest's richer fixture here would
    # leave a real leftover transaction on one side, which would trigger
    # an actual Ollama call -- exactly the mistake this test exists to
    # avoid.
    ledger = tmp_path / "orders.csv"
    ledger.write_text(
        "Name,Financial Status,Paid at,Total\n"
        "#1,paid,2026-01-01 10:00:00 +0000,100.00\n",
        encoding="utf-8",
    )
    settlement = tmp_path / "settlement.csv"
    settlement.write_text(
        "entity_id,type,debit,credit,amount,currency,fee,tax,on_hold,settled,created_at,settled_at,settlement_id,settlement_utr,order_id,payment_id,description\n"
        "pay_1,payment,0,10000,10000,INR,0,0,False,True,1767268800,1767268800,setl_1,UTR1,#1,pay_1,\n",
        encoding="utf-8",
    )

    exit_code = main(
        [
            "--order-ledger", str(ledger),
            "--razorpay-settlement", str(settlement),
            "--llm",
        ]
    )
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "1 matches, 0 exceptions" in out


def test_missing_file_errors_cleanly_not_a_traceback(three_source_csvs, tmp_path: Path, capsys):
    exit_code = main(
        [
            "--order-ledger", str(three_source_csvs["ledger"]),
            "--razorpay-settlement", str(tmp_path / "does_not_exist.csv"),
        ]
    )
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "error:" in captured.err
