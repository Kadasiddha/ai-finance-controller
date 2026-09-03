"""Tests for date-range filtering, applied to parsed sources before
reconciliation.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.filters import filter_by_date_range
from app.models import Transaction


def _txn(source, row_id, d: date) -> Transaction:
    return Transaction(source=source, source_row_id=row_id, amount=Decimal("100.00"), date=d)


@pytest.fixture
def sources() -> dict:
    return {
        "order_ledger": [
            _txn("order_ledger", "#1", date(2026, 1, 1)),
            _txn("order_ledger", "#2", date(2026, 1, 15)),
            _txn("order_ledger", "#3", date(2026, 1, 31)),
            _txn("order_ledger", "#4", date(2026, 2, 1)),
        ],
        "razorpay_settlement": [
            _txn("razorpay_settlement", "pay_1", date(2026, 1, 3)),
            _txn("razorpay_settlement", "pay_2", date(2026, 2, 5)),
        ],
    }


def test_no_bounds_returns_everything(sources):
    result = filter_by_date_range(sources)
    assert {t.source_row_id for t in result["order_ledger"]} == {"#1", "#2", "#3", "#4"}
    assert {t.source_row_id for t in result["razorpay_settlement"]} == {"pay_1", "pay_2"}


def test_no_bounds_returns_a_new_dict_not_the_same_object(sources):
    result = filter_by_date_range(sources)
    assert result is not sources


def test_start_and_end_are_both_inclusive(sources):
    result = filter_by_date_range(sources, start=date(2026, 1, 1), end=date(2026, 1, 31))
    assert {t.source_row_id for t in result["order_ledger"]} == {"#1", "#2", "#3"}


def test_start_only_is_open_ended(sources):
    result = filter_by_date_range(sources, start=date(2026, 1, 15))
    assert {t.source_row_id for t in result["order_ledger"]} == {"#2", "#3", "#4"}


def test_end_only_is_open_ended(sources):
    result = filter_by_date_range(sources, end=date(2026, 1, 15))
    assert {t.source_row_id for t in result["order_ledger"]} == {"#1", "#2"}


def test_each_source_is_filtered_independently(sources):
    result = filter_by_date_range(sources, start=date(2026, 2, 1), end=date(2026, 2, 28))
    assert {t.source_row_id for t in result["order_ledger"]} == {"#4"}
    assert {t.source_row_id for t in result["razorpay_settlement"]} == {"pay_2"}


def test_range_matching_nothing_returns_empty_lists_not_an_error(sources):
    result = filter_by_date_range(sources, start=date(2027, 1, 1), end=date(2027, 1, 31))
    assert result["order_ledger"] == []
    assert result["razorpay_settlement"] == []


def test_start_after_end_raises(sources):
    with pytest.raises(ValueError, match="after"):
        filter_by_date_range(sources, start=date(2026, 2, 1), end=date(2026, 1, 1))
