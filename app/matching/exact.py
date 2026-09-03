"""Tier 1: exact matching.

Groups transactions that share a non-null value for a given key (e.g.
`order_id` linking the order ledger to a Razorpay settlement row, or
`settlement_utr` linking a batch of settlement rows to the single bank
credit that pays them out) -- and that span more than one source. A group
entirely from one source isn't a cross-source match, it's just that
source's own rows sharing a key by coincidence.
"""

from collections import defaultdict

from app.models import MatchResult, Transaction


def match_by_key(
    transactions: list[Transaction], key: str
) -> tuple[list[MatchResult], list[Transaction]]:
    groups: dict[str, list[Transaction]] = defaultdict(list)
    unmatched: list[Transaction] = []

    for txn in transactions:
        value = getattr(txn, key)
        if value:
            groups[value].append(txn)
        else:
            unmatched.append(txn)

    matches: list[MatchResult] = []

    for value, group in groups.items():
        sources = {t.source for t in group}
        if len(sources) >= 2:
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
        else:
            unmatched.extend(group)

    return matches, unmatched
