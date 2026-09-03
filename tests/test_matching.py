from datetime import date
from decimal import Decimal

from app.matching.exact import match_by_key
from app.matching.fuzzy import match_by_amount_and_date
from app.models import Transaction


def _txn(source, row_id, amount, d, order_id=None, utr=None) -> Transaction:
    return Transaction(
        source=source,
        source_row_id=row_id,
        amount=Decimal(amount),
        date=d,
        order_id=order_id,
        settlement_utr=utr,
    )


class TestExactMatch:
    def test_matches_across_two_sources_by_order_id(self):
        ledger = _txn("order_ledger", "L1", "2000.00", date(2026, 1, 1), order_id="order_4471")
        settlement = _txn(
            "razorpay_settlement", "S1", "1953.00", date(2026, 1, 3), order_id="order_4471"
        )
        matches, unmatched = match_by_key([ledger, settlement], "order_id")

        assert len(matches) == 1
        assert matches[0].tier == "exact"
        assert set(matches[0].transactions) == {ledger, settlement}
        assert unmatched == []

    def test_same_source_sharing_key_is_not_a_match(self):
        a = _txn("razorpay_settlement", "S1", "100.00", date(2026, 1, 1), order_id="order_1")
        b = _txn("razorpay_settlement", "S2", "50.00", date(2026, 1, 1), order_id="order_1")
        matches, unmatched = match_by_key([a, b], "order_id")

        assert matches == []
        assert set(unmatched) == {a, b}

    def test_null_key_goes_to_unmatched(self):
        txn = _txn("order_ledger", "L1", "100.00", date(2026, 1, 1), order_id=None)
        matches, unmatched = match_by_key([txn], "order_id")

        assert matches == []
        assert unmatched == [txn]

    def test_settlement_utr_groups_many_settlement_rows_to_one_bank_row(self):
        # This is the batching case: many settlement transactions, one bank credit.
        s1 = _txn("razorpay_settlement", "S1", "1953.00", date(2026, 1, 3), utr="UTR8823")
        s2 = _txn("razorpay_settlement", "S2", "980.00", date(2026, 1, 3), utr="UTR8823")
        bank = _txn("bank_statement", "B1", "2933.00", date(2026, 1, 3), utr="UTR8823")

        matches, unmatched = match_by_key([s1, s2, bank], "settlement_utr")

        assert len(matches) == 1
        assert len(matches[0].transactions) == 3
        assert unmatched == []


class TestFuzzyMatch:
    def test_matches_within_amount_tolerance_and_date_window(self):
        ledger = _txn("order_ledger", "L1", "2000.00", date(2026, 1, 1))
        settlement = _txn("razorpay_settlement", "S1", "1953.00", date(2026, 1, 3))

        matches, unmatched_left, unmatched_right = match_by_amount_and_date(
            [ledger], [settlement], amount_tolerance=Decimal("60.00")
        )

        assert len(matches) == 1
        assert matches[0].tier == "fuzzy"
        assert unmatched_left == []
        assert unmatched_right == []

    def test_outside_amount_tolerance_is_unmatched(self):
        ledger = _txn("order_ledger", "L1", "2000.00", date(2026, 1, 1))
        settlement = _txn("razorpay_settlement", "S1", "1000.00", date(2026, 1, 3))

        matches, unmatched_left, unmatched_right = match_by_amount_and_date(
            [ledger], [settlement], amount_tolerance=Decimal("5.00")
        )

        assert matches == []
        assert unmatched_left == [ledger]
        assert unmatched_right == [settlement]

    def test_outside_date_window_is_unmatched(self):
        from datetime import timedelta

        ledger = _txn("order_ledger", "L1", "2000.00", date(2026, 1, 1))
        settlement = _txn("razorpay_settlement", "S1", "2000.00", date(2026, 1, 10))

        matches, unmatched_left, unmatched_right = match_by_amount_and_date(
            [ledger], [settlement], max_date_delta=timedelta(days=2)
        )

        assert matches == []
        assert unmatched_left == [ledger]

    def test_picks_closest_amount_when_multiple_candidates_in_range(self):
        ledger = _txn("order_ledger", "L1", "2000.00", date(2026, 1, 1))
        far = _txn("razorpay_settlement", "S1", "1950.00", date(2026, 1, 2))
        close = _txn("razorpay_settlement", "S2", "1990.00", date(2026, 1, 2))

        matches, _, remaining_right = match_by_amount_and_date(
            [ledger], [far, close], amount_tolerance=Decimal("60.00")
        )

        assert len(matches) == 1
        assert close in matches[0].transactions
        assert remaining_right == [far]
