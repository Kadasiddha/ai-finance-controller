from datetime import date
from decimal import Decimal

from app.matching.exact import amounts_reconcile, match_by_key
from app.models import Transaction


def _txn(source, row_id, amount, utr) -> Transaction:
    return Transaction(
        source=source,
        source_row_id=row_id,
        amount=Decimal(amount),
        date=date(2026, 1, 3),
        settlement_utr=utr,
    )


class TestAmountsReconcile:
    def test_matching_totals_reconcile(self):
        settlement_rows = [
            _txn("razorpay_settlement", "S1", "1952.80", "RZRP001"),
            _txn("razorpay_settlement", "S2", "980.00", "RZRP001"),
        ]
        bank_row = _txn("bank_statement", "B1", "2932.80", "RZRP001")
        assert amounts_reconcile(settlement_rows + [bank_row])

    def test_mismatched_totals_do_not_reconcile(self):
        # Same UTR by coincidence/error, but the amounts don't actually add up.
        settlement_row = _txn("razorpay_settlement", "S1", "1952.80", "RZRP001")
        bank_row = _txn("bank_statement", "B1", "500.00", "RZRP001")
        assert not amounts_reconcile([settlement_row, bank_row])

    def test_within_tolerance_reconciles(self):
        settlement_row = _txn("razorpay_settlement", "S1", "1952.80", "RZRP001")
        bank_row = _txn("bank_statement", "B1", "1952.81", "RZRP001")  # rounding
        assert amounts_reconcile([settlement_row, bank_row], tolerance=Decimal("0.05"))


class TestMatchByKeyWithValidation:
    def test_rejected_group_is_reported_separately_from_unmatched(self):
        # A duplicate/misapplied UTR: same key, but amounts don't reconcile --
        # should NOT be accepted as a match, must be inspected by a human,
        # and flagged as a *different* diagnosis than "no candidate at all".
        settlement_row = _txn("razorpay_settlement", "S1", "1952.80", "RZRP001")
        wrong_bank_row = _txn("bank_statement", "B1", "500.00", "RZRP001")

        matches, unmatched, rejected = match_by_key(
            [settlement_row, wrong_bank_row], "settlement_utr", validate=amounts_reconcile
        )

        assert matches == []
        assert unmatched == []
        assert len(rejected) == 1  # one rejected group, not two loose rows
        assert set(rejected[0]) == {settlement_row, wrong_bank_row}

    def test_valid_group_is_still_matched(self):
        settlement_row = _txn("razorpay_settlement", "S1", "1952.80", "RZRP001")
        bank_row = _txn("bank_statement", "B1", "1952.80", "RZRP001")

        matches, unmatched, rejected = match_by_key(
            [settlement_row, bank_row], "settlement_utr", validate=amounts_reconcile
        )

        assert len(matches) == 1
        assert unmatched == []
        assert rejected == []

    def test_two_separate_rejected_incidents_are_not_merged(self):
        # Two DIFFERENT misapplied UTRs, each with its own wrong amount.
        # These are two separate incidents -- an investigator looking at
        # one exception must never see unrelated rows from the other.
        s1 = _txn("razorpay_settlement", "S1", "1952.80", "RZRP001")
        wrong_bank_1 = _txn("bank_statement", "B1", "500.00", "RZRP001")
        s2 = _txn("razorpay_settlement", "S2", "300.00", "RZRP002")
        wrong_bank_2 = _txn("bank_statement", "B2", "9999.00", "RZRP002")

        matches, unmatched, rejected = match_by_key(
            [s1, wrong_bank_1, s2, wrong_bank_2],
            "settlement_utr",
            validate=amounts_reconcile,
        )

        assert matches == []
        assert len(rejected) == 2
        rejected_sets = {frozenset(group) for group in rejected}
        assert rejected_sets == {
            frozenset({s1, wrong_bank_1}),
            frozenset({s2, wrong_bank_2}),
        }
