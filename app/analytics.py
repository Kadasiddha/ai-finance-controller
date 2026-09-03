"""Deeper, on-demand analytics over a ReconciliationResult -- "what's
actually going on with this batch," computed when asked, not folded into
reconcile() itself (a routine reconciliation run doesn't need summary
statistics every time).

Pure Python + Decimal throughout, not numpy: numpy arrays are float64 by
default, and this project's whole point is proving money reconciles
exactly (see app/models.py's docstring) -- converting amounts to float
for "analysis" would reintroduce the exact rounding error the project
exists to avoid. Numpy's real advantage is vectorized performance on
large arrays, which also isn't a genuine need at the batch sizes
reconciliation actually runs at (hundreds/thousands of transactions, not
millions). `statistics` (stdlib) computes mean/median natively on
Decimal without any conversion.

Matched/exception value is reported **per source**, not as one summed
cross-source total: a matched group spans multiple sources whose amounts
represent the same money at different points (e.g. a settlement row's
net amount and the bank credit that pays it out) -- summing them
together would double-count the same rupees rather than describe a real
total. Per-source totals avoid that ambiguity and answer the question a
controller actually asks ("how much of my ledger is confirmed matched?").
"""

import statistics
from dataclasses import dataclass
from decimal import Decimal

from app.models import ExceptionCode, ExceptionRecord, MatchTier, Source, Transaction
from app.reconcile import ReconciliationResult


def _sum_by_source(transactions: list[Transaction]) -> dict[Source, Decimal]:
    totals: dict[Source, Decimal] = {}
    for t in transactions:
        totals[t.source] = totals.get(t.source, Decimal("0")) + t.amount
    return totals


@dataclass
class AnalyticsSummary:
    matched_transaction_count: int
    exception_transaction_count: int
    match_rate_by_transaction_count: float | None  # None if there's nothing to reconcile at all

    match_count_by_tier: dict[MatchTier, int]
    matched_value_by_source: dict[Source, Decimal]

    exception_count_by_code: dict[ExceptionCode, int]
    exception_value_by_source: dict[Source, Decimal]
    exception_value_by_code: dict[ExceptionCode, Decimal]
    total_exception_value: Decimal

    mean_exception_value: Decimal | None
    median_exception_value: Decimal | None
    largest_exception: ExceptionRecord | None


def summarize(result: ReconciliationResult) -> AnalyticsSummary:
    matched_transactions = [t for m in result.matches for t in m.transactions]
    exception_transactions = [t for e in result.exceptions for t in e.transactions]

    matched_count = len(matched_transactions)
    exception_count = len(exception_transactions)
    total_count = matched_count + exception_count

    match_count_by_tier: dict[MatchTier, int] = {}
    for m in result.matches:
        match_count_by_tier[m.tier] = match_count_by_tier.get(m.tier, 0) + 1

    exception_count_by_code: dict[ExceptionCode, int] = {}
    exception_value_by_code: dict[ExceptionCode, Decimal] = {}
    for e in result.exceptions:
        exception_count_by_code[e.code] = exception_count_by_code.get(e.code, 0) + 1
        exception_value_by_code[e.code] = (
            exception_value_by_code.get(e.code, Decimal("0")) + e.total_amount
        )

    exception_values = [e.total_amount for e in result.exceptions]
    exceptions_by_value = result.exceptions_by_value

    return AnalyticsSummary(
        matched_transaction_count=matched_count,
        exception_transaction_count=exception_count,
        match_rate_by_transaction_count=(
            matched_count / total_count if total_count > 0 else None
        ),
        match_count_by_tier=match_count_by_tier,
        matched_value_by_source=_sum_by_source(matched_transactions),
        exception_count_by_code=exception_count_by_code,
        exception_value_by_source=_sum_by_source(exception_transactions),
        exception_value_by_code=exception_value_by_code,
        total_exception_value=sum(exception_values, start=Decimal("0")),
        mean_exception_value=(
            statistics.mean(exception_values) if exception_values else None
        ),
        median_exception_value=(
            statistics.median(exception_values) if exception_values else None
        ),
        largest_exception=exceptions_by_value[0] if exceptions_by_value else None,
    )
