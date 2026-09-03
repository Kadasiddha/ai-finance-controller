"""Tier 2: fuzzy matching -- rules and tolerances, still no LLM.

For transactions that survived exact matching unmatched: e.g. a settlement
row whose payout amount is the order amount minus Razorpay's fee, landing
a day or two after the order date. This tier only ever produces one-to-one
matches -- reconstructing many-to-one batches is exact matching's job (via
`settlement_utr`), not this tier's.
"""

from datetime import timedelta
from decimal import Decimal

from app.models import MatchResult, Transaction

# Razorpay's standard settlement cycle is T+2 -- payment on day T, payout by day T+2.
DEFAULT_MAX_DATE_DELTA = timedelta(days=2)
DEFAULT_AMOUNT_TOLERANCE = Decimal("5.00")


def match_by_amount_and_date(
    left: list[Transaction],
    right: list[Transaction],
    amount_tolerance: Decimal = DEFAULT_AMOUNT_TOLERANCE,
    max_date_delta: timedelta = DEFAULT_MAX_DATE_DELTA,
) -> tuple[list[MatchResult], list[Transaction], list[Transaction]]:
    """Greedy nearest-amount match within tolerance and date window.
    Returns (matches, unmatched_left, unmatched_right).
    """
    matches: list[MatchResult] = []
    remaining_right = list(right)
    unmatched_left: list[Transaction] = []

    for l_txn in left:
        best_match: Transaction | None = None
        best_diff: Decimal | None = None

        for r_txn in remaining_right:
            amount_diff = abs(l_txn.amount - r_txn.amount)
            date_diff = abs((l_txn.date - r_txn.date).days)

            if amount_diff <= amount_tolerance and date_diff <= max_date_delta.days:
                if best_diff is None or amount_diff < best_diff:
                    best_match = r_txn
                    best_diff = amount_diff

        if best_match is not None:
            remaining_right.remove(best_match)
            confidence = (
                float(1 - (best_diff / amount_tolerance))
                if amount_tolerance > 0
                else 1.0
            )
            matches.append(
                MatchResult(
                    tier="fuzzy",
                    transactions=[l_txn, best_match],
                    reasoning=(
                        f"Amount within tolerance (diff=₹{best_diff}, "
                        f"limit=₹{amount_tolerance}) and date within "
                        f"{max_date_delta.days} day(s) "
                        f"({l_txn.date} vs {best_match.date})."
                    ),
                    confidence=round(confidence, 3),
                )
            )
        else:
            unmatched_left.append(l_txn)

    return matches, unmatched_left, remaining_right
