"""Tier 3: LLM adjudication -- NOT YET IMPLEMENTED.

Deliberately deferred: this tier only runs on whatever survives exact and
fuzzy matching, so its prompt design needs to be built against what real
leftover cases actually look like (a partial refund tangled into a split
settlement, a many-to-many batch that fuzzy matching can't resolve
one-to-one, etc.) -- not guessed at before there's real data to see what
"genuinely ambiguous" means in practice here.

When this is implemented, the contract should stay the same shape as the
other two tiers: take unmatched Transactions in, return (matches,
still_unmatched) out, where every MatchResult carries a `reasoning` string
explaining the match -- and, just as importantly, every transaction that
still doesn't match should be traceable to why the model declined to
force a match, not silently dropped.
"""

from app.models import MatchResult, Transaction


def adjudicate(transactions: list[Transaction]) -> tuple[list[MatchResult], list[Transaction]]:
    raise NotImplementedError(
        "Tier 3 (LLM adjudication) is not implemented yet -- needs real "
        "unmatched-leftover examples from tiers 1-2 to design the prompt "
        "against. See app/matching/adjudicate.py's module docstring."
    )
