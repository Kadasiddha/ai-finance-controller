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
    def test_rejected_group_falls_through_to_unmatched(self):
        # A duplicate/misapplied UTR: same key, but amounts don't reconcile --
        # should NOT be accepted as a match, must be inspected by a human.
        settlement_row = _txn("razorpay_settlement", "S1", "1952.80", "RZRP001")
        wrong_bank_row = _txn("bank_statement", "B1", "500.00", "RZRP001")

        matches, unmatched = match_by_key(
            [settlement_row, wrong_bank_row], "settlement_utr", validate=amounts_reconcile
        )

        assert matches == []
        assert set(unmatched) == {settlement_row, wrong_bank_row}

    def test_valid_group_is_still_matched(self):
        settlement_row = _txn("razorpay_settlement", "S1", "1952.80", "RZRP001")
        bank_row = _txn("bank_statement", "B1", "1952.80", "RZRP001")

        matches, unmatched = match_by_key(
            [settlement_row, bank_row], "settlement_utr", validate=amounts_reconcile
        )

        assert len(matches) == 1
        assert unmatched == []
