"""Top-level reconciliation pipeline.

Two separate legs, not one 3-way join: ledger<->settlement (linked by
order_id) and settlement<->bank (linked by settlement_utr). A single
settlement transaction naturally participates in both legs at once --
that's normal, not a bug, since it's genuinely the thing that connects
the order to the payout.

Each leg runs exact matching first (tier 1, cheap and provably correct),
then fuzzy matching on whatever's left (tier 2, still no LLM). Tier 3 (LLM
adjudication) is not wired in yet -- see app/matching/adjudicate.py.

Nothing that fails to confidently match at any tier is force-matched.
Unmatched transactions become ExceptionRecords with an explicit reason,
sorted by rupee value -- the exception list is the actual deliverable
here, not an afterthought.
"""

from dataclasses import dataclass

from app.matching.exact import amounts_reconcile, match_by_key
from app.matching.fuzzy import match_by_amount_and_date
from app.models import ExceptionCode, ExceptionRecord, MatchResult, Transaction


@dataclass
class ReconciliationResult:
    matches: list[MatchResult]
    exceptions: list[ExceptionRecord]

    @property
    def exceptions_by_value(self) -> list[ExceptionRecord]:
        return sorted(self.exceptions, key=lambda e: e.total_amount, reverse=True)


def _reconcile_leg(
    left: list[Transaction],
    right: list[Transaction],
    key: str,
    validate=None,
) -> tuple[list[MatchResult], list[Transaction], list[Transaction], list[list[Transaction]]]:
    """One leg: exact match by `key`, then fuzzy match whatever had no key
    at all. Groups that shared a key but failed `validate` don't get a
    second chance at fuzzy matching -- a failed key-based claim is a more
    specific problem than "no candidate found", not a lesser one.

    Returns (matches, still-unmatched-left, still-unmatched-right, rejected).
    """
    combined = left + right
    exact_matches, unmatched, rejected = match_by_key(combined, key, validate=validate)

    unmatched_left = [t for t in unmatched if t in left]
    unmatched_right = [t for t in unmatched if t in right]

    fuzzy_matches, still_unmatched_left, still_unmatched_right = match_by_amount_and_date(
        unmatched_left, unmatched_right
    )

    return exact_matches + fuzzy_matches, still_unmatched_left, still_unmatched_right, rejected


def reconcile(
    ledger: list[Transaction],
    settlement: list[Transaction],
    bank: list[Transaction],
) -> ReconciliationResult:
    (
        ledger_settlement_matches,
        unmatched_ledger,
        unmatched_settlement_no_ledger,
        rejected_ledger_settlement,
    ) = _reconcile_leg(ledger, settlement, "order_id")
    (
        settlement_bank_matches,
        unmatched_settlement_no_bank,
        unmatched_bank,
        rejected_settlement_bank,
    ) = _reconcile_leg(settlement, bank, "settlement_utr", validate=amounts_reconcile)

    all_matches = ledger_settlement_matches + settlement_bank_matches

    # A settlement row can legitimately be missing its ledger counterpart,
    # its bank counterpart, or both -- these are different diagnoses (an
    # order with no payment record vs. a payment that hasn't been paid out
    # yet) and collapsing them into one generic reason would throw away
    # exactly the detail an exception list exists to preserve.
    missing_ledger_ids = {t.source_row_id for t in unmatched_settlement_no_ledger}
    missing_bank_ids = {t.source_row_id for t in unmatched_settlement_no_bank}
    settlement_by_id = {
        t.source_row_id: t
        for t in unmatched_settlement_no_ledger + unmatched_settlement_no_bank
    }

    exceptions: list[ExceptionRecord] = []
    exceptions += [
        ExceptionRecord(
            [t],
            "NO_COUNTERPART_FOUND",
            "No matching settlement transaction found for this order.",
        )
        for t in unmatched_ledger
    ]
    for row_id, t in settlement_by_id.items():
        missing_sides = []
        if row_id in missing_ledger_ids:
            missing_sides.append("order ledger (no order_id match)")
        if row_id in missing_bank_ids:
            missing_sides.append("bank statement (no settlement_utr match)")
        exceptions.append(
            ExceptionRecord(
                [t],
                "NO_COUNTERPART_FOUND",
                f"No counterpart found in: {', '.join(missing_sides)}.",
            )
        )
    exceptions += [
        ExceptionRecord(
            [t],
            "NO_COUNTERPART_FOUND",
            "No matching settlement transaction found for this bank credit.",
        )
        for t in unmatched_bank
    ]

    # Rejected groups shared a reference/UTR but the amounts didn't add up --
    # each rejected group is its own incident (see match_by_key), so each
    # becomes its own exception rather than being lumped with unrelated rows.
    for group in rejected_ledger_settlement + rejected_settlement_bank:
        exceptions.append(
            ExceptionRecord(
                group,
                "AMOUNT_MISMATCH",
                (
                    "Rows share a reference, but the amounts don't add up -- "
                    "possible duplicate or misapplied reference."
                ),
            )
        )

    return ReconciliationResult(matches=all_matches, exceptions=exceptions)
