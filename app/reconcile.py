"""Top-level reconciliation pipeline.

Generalized to reconcile any 2 or more of the known sources, selected by
the caller -- not hardcoded to always requiring all three. A "leg" is one
pairwise relationship between two sources, linked by a specific shared key
(e.g. `order_id` links order_ledger to razorpay_settlement). Only legs
where BOTH sides are present in the caller's selected sources actually
run: select just order_ledger + razorpay_settlement and only that one leg
runs; select all three and both known legs run, chained through the
settlement transaction that naturally participates in both.

Adding a new known source later means adding its leg(s) to KNOWN_LEGS, not
rewriting this pipeline. This deliberately does NOT attempt to support
arbitrary unknown source types with unknown keys -- guessing at how to
relate two unfamiliar sources would be exactly the kind of invented
behavior this project's own principles reject. Selecting two sources with
no known relationship between them (e.g. order_ledger + bank_statement,
skipping settlement) raises rather than silently doing nothing.

Each leg runs exact matching first (tier 1, cheap and provably correct),
then fuzzy matching on whatever had no key at all (tier 2, still no LLM).
Tier 3 (LLM adjudication) is not wired in yet -- see
app/matching/adjudicate.py.

Nothing that fails to confidently match at any tier is force-matched.
Unmatched transactions become ExceptionRecords with a structured code and
an explicit reason, sorted by rupee value -- the exception list is the
actual deliverable here, not an afterthought.
"""

from collections import defaultdict
from dataclasses import dataclass
from typing import Callable, NamedTuple

from app.matching.exact import amounts_reconcile, match_by_key
from app.matching.fuzzy import match_by_amount_and_date
from app.models import ExceptionRecord, MatchResult, Source, Transaction


class Leg(NamedTuple):
    left: Source
    right: Source
    key: str
    validate: Callable[[list[Transaction]], bool] | None = None


KNOWN_LEGS: list[Leg] = [
    Leg("order_ledger", "razorpay_settlement", "order_id"),
    Leg("razorpay_settlement", "bank_statement", "settlement_utr", amounts_reconcile),
    Leg("order_ledger", "stripe_settlement", "order_id"),
    Leg("stripe_settlement", "bank_statement", "settlement_utr", amounts_reconcile),
    Leg("order_ledger", "payu_settlement", "order_id"),
    Leg("payu_settlement", "bank_statement", "settlement_utr", amounts_reconcile),
]


@dataclass
class ReconciliationResult:
    matches: list[MatchResult]
    exceptions: list[ExceptionRecord]

    @property
    def exceptions_by_value(self) -> list[ExceptionRecord]:
        return sorted(self.exceptions, key=lambda e: e.total_amount, reverse=True)


def _run_leg(
    leg: Leg,
    left: list[Transaction],
    right: list[Transaction],
) -> tuple[list[MatchResult], list[Transaction], list[Transaction], list[list[Transaction]]]:
    """Returns (matches, still-unmatched-left, still-unmatched-right, rejected)."""
    combined = left + right
    exact_matches, unmatched, rejected = match_by_key(combined, leg.key, validate=leg.validate)

    unmatched_left = [t for t in unmatched if t in left]
    unmatched_right = [t for t in unmatched if t in right]

    fuzzy_matches, still_unmatched_left, still_unmatched_right = match_by_amount_and_date(
        unmatched_left, unmatched_right
    )

    return exact_matches + fuzzy_matches, still_unmatched_left, still_unmatched_right, rejected


def reconcile(sources: dict[Source, list[Transaction]]) -> ReconciliationResult:
    """Reconcile whichever of the known sources the caller selects (2 or
    more). `sources` maps source name -> that source's parsed transactions;
    omit a source entirely to run a narrower reconciliation (e.g. just
    order_ledger + razorpay_settlement, skipping the bank statement).

    Raises ValueError if fewer than 2 sources are given, or if none of the
    selected sources have a known relationship linking them.
    """
    if len(sources) < 2:
        raise ValueError("Need at least 2 sources to reconcile anything.")

    applicable_legs = [
        leg for leg in KNOWN_LEGS if leg.left in sources and leg.right in sources
    ]
    if not applicable_legs:
        known = [(leg.left, leg.right) for leg in KNOWN_LEGS]
        raise ValueError(
            f"No known relationship links any pair of the selected sources "
            f"{sorted(sources)}. Known relationships: {known}."
        )

    all_matches: list[MatchResult] = []
    # A row unmatched in one leg it participated in is still fine if it
    # matched in a *different* leg -- e.g. a settlement row can match its
    # order but still be legitimately missing a bank credit (not yet
    # settled). Track per-row which side(s) it's missing across all legs
    # it was eligible for, not just whether it matched at least once.
    unmatched_by_row: dict[str, Transaction] = {}
    missing_from_by_row: dict[str, set[Source]] = defaultdict(set)
    rejected_groups: list[list[Transaction]] = []

    for leg in applicable_legs:
        matches, left_unmatched, right_unmatched, rejected = _run_leg(
            leg, sources[leg.left], sources[leg.right]
        )
        all_matches.extend(matches)
        rejected_groups.extend(rejected)

        for t in left_unmatched:
            unmatched_by_row[t.source_row_id] = t
            missing_from_by_row[t.source_row_id].add(leg.right)
        for t in right_unmatched:
            unmatched_by_row[t.source_row_id] = t
            missing_from_by_row[t.source_row_id].add(leg.left)

    exceptions: list[ExceptionRecord] = [
        ExceptionRecord(
            [t],
            "NO_COUNTERPART_FOUND",
            f"No counterpart found in: {', '.join(sorted(missing_from_by_row[row_id]))}.",
        )
        for row_id, t in unmatched_by_row.items()
    ]

    # Rejected groups shared a reference/UTR but the amounts didn't add up --
    # each rejected group is its own incident (see match_by_key), so each
    # becomes its own exception rather than being lumped with unrelated rows.
    exceptions += [
        ExceptionRecord(
            group,
            "AMOUNT_MISMATCH",
            (
                "Rows share a reference, but the amounts don't add up -- "
                "possible duplicate or misapplied reference."
            ),
        )
        for group in rejected_groups
    ]

    return ReconciliationResult(matches=all_matches, exceptions=exceptions)
