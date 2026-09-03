"""Tests for the generalized N-way reconciliation: the caller selects
which 2+ of the known sources to reconcile, not always all three.

Reuses the same fixture dataset as the end-to-end test
(`tests/test_reconcile_end_to_end.py`) via the shared `parsed_sources`
fixture, just running `reconcile()` against subsets of it.
"""

import pytest

from app.reconcile import reconcile


class TestTwoWayReconciliation:
    def test_ledger_and_settlement_only_runs_one_leg(self, parsed_sources):
        result = reconcile(
            {
                "order_ledger": parsed_sources["order_ledger"],
                "razorpay_settlement": parsed_sources["razorpay_settlement"],
            }
        )

        # Only the order_id leg can run -- no bank_statement present, so
        # no match should ever include a bank_statement transaction.
        for match in result.matches:
            assert all(t.source != "bank_statement" for t in match.transactions)

        # #4471's order<->settlement link still confirms even without the
        # bank statement in scope.
        order_4471_match = next(
            m for m in result.matches if any(t.order_id == "#4471" for t in m.transactions)
        )
        assert order_4471_match.tier == "exact"
        assert {t.source for t in order_4471_match.transactions} == {
            "order_ledger",
            "razorpay_settlement",
        }

    def test_settlement_and_bank_only_runs_one_leg(self, parsed_sources):
        result = reconcile(
            {
                "razorpay_settlement": parsed_sources["razorpay_settlement"],
                "bank_statement": parsed_sources["bank_statement"],
            }
        )

        for match in result.matches:
            assert all(t.source != "order_ledger" for t in match.transactions)

        # The batched settlement (#4472 + #4473 -> one bank credit) still
        # confirms without the order ledger in scope.
        batch_match = next(
            m
            for m in result.matches
            if any(t.settlement_utr == "RZRP1000000002" for t in m.transactions)
        )
        assert len(batch_match.transactions) == 3

    def test_ledger_and_bank_alone_has_no_known_relationship(self, parsed_sources):
        # order_ledger and bank_statement have no direct shared key --
        # the real link is always through razorpay_settlement. Selecting
        # just these two should fail loudly, not silently produce nothing.
        with pytest.raises(ValueError, match="No known relationship"):
            reconcile(
                {
                    "order_ledger": parsed_sources["order_ledger"],
                    "bank_statement": parsed_sources["bank_statement"],
                }
            )

    def test_single_source_raises(self, parsed_sources):
        with pytest.raises(ValueError, match="at least 2 sources"):
            reconcile({"order_ledger": parsed_sources["order_ledger"]})
