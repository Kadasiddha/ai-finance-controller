"""Tests for CSV export of a ReconciliationResult -- run against the same
shared fixture dataset used by the end-to-end reconciliation tests
(tests/conftest.py), so this proves the report reflects a real result,
not a hand-built one.
"""

import csv
from decimal import Decimal
from pathlib import Path

import pytest

from app.reconcile import reconcile
from app.report import REPORT_FIELDNAMES, write_csv_report


@pytest.fixture
def reconciliation_result(parsed_sources):
    return reconcile(parsed_sources)


@pytest.fixture
def report_rows(reconciliation_result, tmp_path: Path) -> list[dict]:
    report_path = write_csv_report(reconciliation_result, tmp_path / "report.csv")
    with report_path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_returns_the_path_it_wrote(reconciliation_result, tmp_path: Path):
    report_path = write_csv_report(reconciliation_result, tmp_path / "report.csv")
    assert report_path == tmp_path / "report.csv"
    assert report_path.exists()


def test_every_row_has_all_declared_fields(report_rows):
    for row in report_rows:
        assert set(row.keys()) == set(REPORT_FIELDNAMES)


def test_row_count_equals_matches_plus_exceptions(report_rows, reconciliation_result):
    expected = len(reconciliation_result.matches) + len(reconciliation_result.exceptions)
    assert len(report_rows) == expected


def test_record_type_is_only_match_or_exception(report_rows):
    assert {row["record_type"] for row in report_rows} <= {"match", "exception"}


def test_exceptions_appear_in_descending_value_order(report_rows):
    exception_values = [
        Decimal(row["total_amount"]) for row in report_rows if row["record_type"] == "exception"
    ]
    assert exception_values == sorted(exception_values, reverse=True)


def test_matches_are_written_before_exceptions(report_rows):
    record_types = [row["record_type"] for row in report_rows]
    first_exception_index = record_types.index("exception")
    assert "match" not in record_types[first_exception_index:]


def test_a_known_exact_match_row_has_correct_ids_and_sources(report_rows):
    row = next(
        r
        for r in report_rows
        if r["record_type"] == "match" and "pay_1" in r["transaction_ids"]
    )
    assert row["tier_or_code"] == "exact"
    assert "order_ledger:#4471" in row["transaction_ids"]
    assert "order_ledger" in row["sources_involved"]
    assert "razorpay_settlement" in row["sources_involved"]


def test_amount_mismatch_exception_carries_its_code_and_reason(report_rows):
    row = next(r for r in report_rows if r["tier_or_code"] == "AMOUNT_MISMATCH")
    assert row["record_type"] == "exception"
    assert "amounts don't add up" in row["detail"]


def test_no_counterpart_exception_lists_the_missing_source(report_rows):
    row = next(
        r
        for r in report_rows
        if r["record_type"] == "exception" and "#4475" in r["transaction_ids"]
    )
    assert row["tier_or_code"] == "NO_COUNTERPART_FOUND"
    assert "razorpay_settlement" in row["detail"]


def test_every_total_amount_is_a_valid_decimal(report_rows):
    for row in report_rows:
        Decimal(row["total_amount"])  # raises if malformed
