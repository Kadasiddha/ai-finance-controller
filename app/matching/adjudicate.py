"""Tier 3: LLM adjudication for the one thing tiers 1-2 structurally
can't do -- reasoning over combinations.

Tier 1 groups by a shared key; tier 2 is strictly one-to-one (see its own
docstring). Neither can resolve a real, if uncommon, pattern: two
settlement rows with genuinely different batch references (two separate
payout instructions from the gateway's own perspective) that a bank, for
its own processing reasons, combines into a single wire credit. Today
that deterministically produces separate NO_COUNTERPART_FOUND exceptions
for every row involved, even though a human glancing at the amounts and
dates would immediately suspect they belong together.

Design principle -- the LLM proposes, code verifies: an LLM is unreliable
at precise arithmetic, and this project's whole premise is exact money
reconciliation. So the model's job here is candidate generation ("these
transactions plausibly belong to the same real-world settlement"), never
final arithmetic authority. Every group it proposes is independently
re-verified with the already-tested `amounts_reconcile()` before being
accepted -- the model never gets to unilaterally declare a match true,
the same way tier 1's shared-key groups don't either. A proposal that
fails any of the checks below (references a transaction that doesn't
exist, is below the confidence floor, or doesn't actually reconcile) is
rejected, not trusted, and its transactions stay unmatched -- same
"never force-matched" principle as every other tier.

Deliberately OFF by default in `reconcile()` (see app/reconcile.py) --
this is the only tier with a real runtime/infra dependency (a locally
running Ollama server) and real latency, unlike tiers 1-2 which are
instant and dependency-free. A caller opts in explicitly by passing
`adjudicate_fn=adjudicate`.
"""

import json
from decimal import Decimal, InvalidOperation

from app.matching.exact import amounts_reconcile
from app.models import MatchResult, Transaction

# A judgment call, not tuned against real data yet (there isn't any) --
# revisit once this has run against genuine ambiguous leftovers.
MIN_CONFIDENCE = 0.6


def _transaction_key(t: Transaction) -> str:
    # Same composite-key convention app/report.py already uses for
    # transaction_ids -- one consistent way to unambiguously name a
    # transaction across the whole codebase.
    return f"{t.source}:{t.source_row_id}"


def _build_prompt(transactions: list[Transaction]) -> str:
    lines = [
        f"- {_transaction_key(t)}: amount={t.amount}, date={t.date}, "
        f"description={t.description or '(none)'}"
        for t in transactions
    ]
    return f"""You are reconciling financial transactions that could not be matched
by exact reference or by a simple one-to-one amount+date comparison.

Some of these transactions may, when GROUPED TOGETHER, represent the
same real-world settlement -- e.g. two separate payout amounts that a
bank combined into one wire credit. Only propose a group if the
transactions plausibly belong together (amounts that sum sensibly across
sources, dates close together). Do NOT force every transaction into a
group -- most may genuinely be unrelated, and leaving them out is
correct.

Transactions:
{chr(10).join(lines)}

Respond with ONLY this JSON shape, no other text:
{{"groups": [{{"transaction_ids": ["source:row_id", ...], "reasoning": "...", "confidence": 0.0-1.0}}]}}

If no group is plausible, respond with {{"groups": []}}."""


def adjudicate(
    left: list[Transaction],
    right: list[Transaction],
    call_llm=None,
) -> tuple[list[MatchResult], list[Transaction], list[Transaction]]:
    """Same shape as `match_by_amount_and_date`: takes still-unmatched
    transactions from both sides of a leg, returns (matches,
    unmatched_left, unmatched_right).

    `call_llm` defaults to `app.llm_client.call_ollama` -- injectable so
    tests never need a real Ollama server (see tests/test_adjudicate.py).
    """
    if call_llm is None:
        from app.llm_client import call_ollama

        call_llm = call_ollama

    all_transactions = left + right
    if not all_transactions:
        return [], left, right

    by_key = {_transaction_key(t): t for t in all_transactions}

    try:
        raw_response = call_llm(_build_prompt(all_transactions))
        proposal = json.loads(raw_response)
        groups = proposal["groups"]
    except (json.JSONDecodeError, KeyError, TypeError):
        # Malformed response -- never crash, never guess. Everything
        # stays unmatched, same as if tier 3 had never run.
        return [], left, right

    matches: list[MatchResult] = []
    claimed_keys: set[str] = set()

    for group in groups:
        try:
            transaction_ids = group["transaction_ids"]
            reasoning = group["reasoning"]
            confidence = float(group["confidence"])
        except (KeyError, TypeError, ValueError):
            continue

        if confidence < MIN_CONFIDENCE:
            continue

        # Hallucination guard: every referenced transaction must be one
        # we actually gave the model, nothing invented.
        if not all(key in by_key for key in transaction_ids):
            continue

        # A transaction already claimed by an earlier-accepted group
        # can't also belong to this one -- first-accepted-wins, rather
        # than letting the same money get counted in two matches.
        if any(key in claimed_keys for key in transaction_ids):
            continue

        group_transactions = [by_key[key] for key in transaction_ids]
        sources = {t.source for t in group_transactions}
        if len(sources) < 2:
            continue  # not a real cross-source match, same rule as tier 1

        # The model proposes; this is the actual arithmetic authority.
        try:
            if not amounts_reconcile(group_transactions):
                continue
        except (InvalidOperation, TypeError):
            continue

        matches.append(
            MatchResult(
                tier="llm",
                transactions=group_transactions,
                reasoning=reasoning,
                confidence=confidence,
            )
        )
        claimed_keys.update(transaction_ids)

    unmatched_left = [t for t in left if _transaction_key(t) not in claimed_keys]
    unmatched_right = [t for t in right if _transaction_key(t) not in claimed_keys]

    return matches, unmatched_left, unmatched_right
