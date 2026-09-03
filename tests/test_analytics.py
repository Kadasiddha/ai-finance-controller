"""Tests for on-demand result analytics, run against the same shared
fixture dataset used by the end-to-end reconciliation tests
(tests/conftest.py).
"""

from datetime import date
from decimal import Decimal

import pytest

from app.analytics import summarize
from app.models import ExceptionRecord, MatchResult, Transaction
from app.reconcile import ReconciliationResult, reconcile


@pytest.fixture
def reconciliation_result(parsed_sources):
    return reconcile(parsed_sources)


@pytest.fixture
def summary(reconciliation_result):
    return summarize(reconciliation_result)


def test_matched_and_exception_transaction_counts_are_disjoint_and_complete(
    summary, reconciliation_result
):
    expected_matched = sum(len(m.transactions) for m in reconciliation_result.matches)
    expected_exception = sum(len(e.transactions) for e in reconciliation_result.exceptions)
    assert summary.matched_transaction_count == expected_matched
    assert summary.exception_transaction_count == expected_exception


def test_match_rate_is_between_zero_and_one(summary):
    assert 0.0 < summary.match_rate_by_transaction_count < 1.0


def test_match_rate_is_none_when_nothing_to_reconcile():
    empty_result = ReconciliationResult(matches=[], exceptions=[])
    empty_summary = summarize(empty_result)
    assert empty_summary.match_rate_by_transaction_count is None
    assert empty_summary.mean_exception_value is None
    assert empty_summary.median_exception_value is None
    assert empty_summary.largest_exception is None


def test_match_count_by_tier_includes_both_exact_and_fuzzy(summary):
    assert summary.match_count_by_tier["exact"] > 0
    assert summary.match_count_by_tier["fuzzy"] > 0


def test_matched_value_by_source_does_not_cross_contaminate_sources(summary):
    assert "order_ledger" in summary.matched_value_by_source
    assert "razorpay_settlement" in summary.matched_value_by_source
    assert "bank_statement" in summary.matched_value_by_source
    # order_ledger's matched value is the sum of gross order totals, which
    # must NOT equal razorpay_settlement's matched value (net of fee/tax) --
    # if they were equal, sources were summed together instead of kept apart.
    assert (
        summary.matched_value_by_source["order_ledger"]
        != summary.matched_value_by_source["razorpay_settlement"]
    )


def test_exception_count_by_code_matches_known_scenarios(summary):
    assert summary.exception_count_by_code["NO_COUNTERPART_FOUND"] >= 1
    assert summary.exception_count_by_code["AMOUNT_MISMATCH"] >= 1


def test_total_exception_value_equals_sum_of_per_code_values(summary):
    assert summary.total_exception_value == sum(
        summary.exception_value_by_code.values(), start=Decimal("0")
    )


def test_largest_exception_is_the_highest_value_one(summary, reconciliation_result):
    assert summary.largest_exception == reconciliation_result.exceptions_by_value[0]


def test_mean_and_median_are_within_the_min_max_range(summary, reconciliation_result):
    values = [e.total_amount for e in reconciliation_result.exceptions]
    assert min(values) <= summary.mean_exception_value <= max(values)
    assert min(values) <= summary.median_exception_value <= max(values)


def test_mean_and_median_are_exact_decimals_not_floats(summary):
    assert isinstance(summary.mean_exception_value, Decimal)
    assert isinstance(summary.median_exception_value, Decimal)


def test_two_matched_transactions_at_the_same_amount_sum_correctly():
    # A source-isolated sanity check independent of the shared fixture:
    # two matched transactions from the same source must sum plainly.
    t1 = Transaction(source="order_ledger", source_row_id="#1", amount=Decimal("100.00"), date=date(2026, 1, 1))
    t2 = Transaction(source="order_ledger", source_row_id="#2", amount=Decimal("50.00"), date=date(2026, 1, 2))
    result = ReconciliationResult(
        matches=[MatchResult(tier="exact", transactions=[t1, t2], reasoning="test")],
        exceptions=[],
    )
    summary = summarize(result)
    assert summary.matched_value_by_source["order_ledger"] == Decimal("150.00")
