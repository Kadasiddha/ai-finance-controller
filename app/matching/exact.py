"""Tier 1: exact matching.

Groups transactions that share a non-null value for a given key (e.g.
`order_id` linking the order ledger to a Razorpay settlement row, or
`settlement_utr` linking a batch of settlement rows to the single bank
credit that pays them out) -- and that span more than one source. A group
entirely from one source isn't a cross-source match, it's just that
source's own rows sharing a key by coincidence.

A shared key alone is not sufficient proof of a real match, though: two
unrelated rows can end up sharing a reference by data-entry error (a
duplicate/misapplied UTR). An optional `validate` callback lets a caller
reject a same-key group whose amounts don't actually add up -- rejected
groups are returned separately from `unmatched` (see `rejected` below),
since "found a candidate but the amounts don't reconcile" is a materially
different, more specific diagnosis than "no candidate at all," and the
exception it produces should say so.
"""

from collections import defaultdict
from decimal import Decimal
from typing import Callable

from app.models import MatchResult, Transaction


def match_by_key(
    transactions: list[Transaction],
    key: str,
    validate: Callable[[list[Transaction]], bool] | None = None,
) -> tuple[list[MatchResult], list[Transaction], list[list[Transaction]]]:
    """Returns (matches, unmatched, rejected). `unmatched` had no key or no
    cross-source group at all -- a flat list, since these have no group
    identity worth preserving. `rejected` had a real cross-source group
    that `validate` refused -- a list of *groups*, not a flattened list,
    so two separate rejected incidents (e.g. two different misapplied
    UTRs) never get merged into one exception covering unrelated rows.
    """
    groups: dict[str, list[Transaction]] = defaultdict(list)
    unmatched: list[Transaction] = []

    for txn in transactions:
        value = getattr(txn, key)
        if value:
            groups[value].append(txn)
        else:
            unmatched.append(txn)

    matches: list[MatchResult] = []
    rejected: list[list[Transaction]] = []

    for value, group in groups.items():
        sources = {t.source for t in group}
        if len(sources) < 2:
            unmatched.extend(group)
            continue

        if validate is not None and not validate(group):
            rejected.append(group)
            continue

        matches.append(
            MatchResult(
                tier="exact",
                transactions=group,
                reasoning=(
                    f"Exact match on {key}={value!r} across "
                    f"{', '.join(sorted(sources))}."
                ),
            )
        )

    return matches, unmatched, rejected


def amounts_reconcile(
    group: list[Transaction], tolerance: Decimal = Decimal("1.00")
) -> bool:
    """Checks that each source's total amount within the group agrees with
    every other source's total, within a small tolerance -- catches rows
    that share a key by coincidence/error rather than genuinely belonging
    to the same batch (e.g. a duplicate or misapplied settlement UTR).
    """
    totals_by_source: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for txn in group:
        totals_by_source[txn.source] += txn.amount

    totals = list(totals_by_source.values())
    return all(abs(totals[0] - t) <= tolerance for t in totals[1:])
