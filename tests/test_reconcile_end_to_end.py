"""End-to-end reconciliation test across all three sources, covering both
positive (should match) and negative (should correctly refuse to match)
scenarios in one coherent, cross-referenced dataset.

The fixture dataset itself (`parsed_sources`) lives in conftest.py, shared
with tests/test_reconcile_multi_way.py which runs the same data through
narrower (2-way) reconciliations. See conftest.py's module docstring for
the scenario-by-scenario breakdown of what each order number covers.
"""

import pytest

from app.reconcile import reconcile
from tests.conftest import SETTLEMENT_ROWS


@pytest.fixture
def reconciliation_result(parsed_sources):
    return reconcile(parsed_sources)


class TestPositiveScenarios:
    def test_pending_order_never_enters_reconciliation(self, reconciliation_result):
        all_row_ids = {
            t.source_row_id
            for m in reconciliation_result.matches
            for t in m.transactions
        } | {t.source_row_id for e in reconciliation_result.exceptions for t in e.transactions}
        assert "#4477" not in all_row_ids

    def test_clean_exact_match_on_both_legs(self, reconciliation_result):
        order_4471_matches = [
            m
            for m in reconciliation_result.matches
            if any(t.order_id == "#4471" or t.source_row_id == "pay_1" for t in m.transactions)
        ]
        # Two separate MatchResults: ledger<->settlement, settlement<->bank.
        assert len(order_4471_matches) == 2
        assert all(m.tier == "exact" for m in order_4471_matches)

    def test_batched_settlement_matches_one_bank_row_to_two_orders(self, reconciliation_result):
        batch_match = next(
            m
            for m in reconciliation_result.matches
            if any(t.settlement_utr == "RZRP1000000002" for t in m.transactions)
            and any(t.source == "bank_statement" for t in m.transactions)
        )
        assert len(batch_match.transactions) == 3  # 2 settlement rows + 1 bank row
        assert batch_match.tier == "exact"

    def test_truncated_utr_falls_back_to_fuzzy_match(self, reconciliation_result):
        order_4474_bank_match = next(
            (
                m
                for m in reconciliation_result.matches
                if any(t.order_id == "#4474" for t in m.transactions)
                or any(
                    t.source == "razorpay_settlement" and t.settlement_utr == "RZRP1000000004"
                    for t in m.transactions
                )
            ),
            None,
        )
        settlement_bank_matches_for_4474 = [
            m
            for m in reconciliation_result.matches
            if any(
                t.source == "razorpay_settlement" and t.order_id == "#4474"
                for t in m.transactions
            )
            and any(t.source == "bank_statement" for t in m.transactions)
        ]
        assert len(settlement_bank_matches_for_4474) == 1
        assert settlement_bank_matches_for_4474[0].tier == "fuzzy"


class TestNegativeScenarios:
    def test_order_with_no_settlement_becomes_exception_not_forced_match(
        self, reconciliation_result
    ):
        matched_row_ids = {
            t.source_row_id for m in reconciliation_result.matches for t in m.transactions
        }
        assert "#4475" not in matched_row_ids

        exception = next(
            e
            for e in reconciliation_result.exceptions
            for t in e.transactions
            if t.order_id == "#4475"
        )
        assert exception.code == "NO_COUNTERPART_FOUND"
        assert "razorpay_settlement" in exception.reason

    def test_wildly_mismatched_amount_is_refused_not_force_matched(self, reconciliation_result):
        # pay_6 legitimately DOES match on the ledger<->settlement leg
        # (order #4476 is real) -- that's correct. What must NOT happen is
        # a settlement<->bank link forming despite the amount mismatch.
        settlement_bank_matches_for_pay_6 = [
            m
            for m in reconciliation_result.matches
            if any(t.source_row_id == "pay_6" for t in m.transactions)
            and any(t.source == "bank_statement" for t in m.transactions)
        ]
        assert settlement_bank_matches_for_pay_6 == []

        settlement_exception = next(
            e
            for e in reconciliation_result.exceptions
            for t in e.transactions
            if t.source_row_id == "pay_6"
        )
        # Structured code, not just free text -- and both the settlement
        # row AND the wrong-amount bank row are attached to the SAME
        # exception, since they're the same incident.
        assert settlement_exception.code == "AMOUNT_MISMATCH"
        assert len(settlement_exception.transactions) == 2
        assert {t.source for t in settlement_exception.transactions} == {
            "razorpay_settlement",
            "bank_statement",
        }

    def test_exceptions_are_sorted_by_rupee_value_descending(self, reconciliation_result):
        values = [e.total_amount for e in reconciliation_result.exceptions_by_value]
        assert values == sorted(values, reverse=True)

    def test_nothing_is_silently_dropped(self, reconciliation_result):
        # Every transaction from every source ends up either matched or
        # accounted for in an exception -- never just missing.
        expected_settlement_ids = {row[0] for row in SETTLEMENT_ROWS}
        seen_settlement_ids = {
            t.source_row_id
            for m in reconciliation_result.matches
            for t in m.transactions
            if t.source == "razorpay_settlement"
        } | {
            t.source_row_id
            for e in reconciliation_result.exceptions
            for t in e.transactions
            if t.source == "razorpay_settlement"
        }
        assert expected_settlement_ids == seen_settlement_ids
