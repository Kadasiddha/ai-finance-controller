"""Evaluation framework: measures a ReconciliationResult against a known-
correct ground truth (a "golden dataset"), producing the metrics this
project's blueprint specifies. "I matched 98%" is a meaningless claim on
its own -- the actual deliverable is knowing whether that 98% is *correct*,
and exactly how much rupee value is still unexplained and why.
"""

from dataclasses import dataclass
from decimal import Decimal

from app.reconcile import ReconciliationResult


@dataclass(frozen=True)
class GroundTruthEntry:
    """What SHOULD happen to a specific group of source rows (identified
    by their `source_row_id`s).

    `expected="matched"`: this exact group of rows should appear as one
    MatchResult.

    `expected="exception"`: these rows must NOT be matched together --
    whether they end up as one exception, several, or are simply never
    matched is a separate question this framework doesn't score (that
    depends on refund/adjustment handling and tier 3, neither built yet);
    the one thing that's always wrong is confidently matching them.
    """

    row_ids: frozenset[str]
    expected: str  # "matched" or "exception"
    note: str = ""


@dataclass(frozen=True)
class EvaluationReport:
    total_ground_truth_entries: int
    correct_matches: int
    missed_matches: int
    false_matches: int  # ground truth says "exception", engine matched it anyway -- the worst outcome
    total_matches_produced: int
    total_exceptions_produced: int

    # Metrics, named to match the project blueprint's Evaluation Framework.
    auto_match_precision: float  # (matches produced - false matches) / matches produced
    auto_match_recall: float  # correct matches / all matches ground truth expects
    false_match_rate: float  # false matches / matches produced
    exception_coverage_count: int  # transaction rows that ended up in an exception
    unresolved_value: Decimal  # total rupee value still unexplained


def evaluate(
    result: ReconciliationResult, ground_truth: list[GroundTruthEntry]
) -> EvaluationReport:
    actual_match_groups = {
        frozenset(t.source_row_id for t in m.transactions) for m in result.matches
    }

    correct_matches = 0
    missed_matches = 0
    false_matches = 0
    expected_match_count = 0

    for entry in ground_truth:
        if entry.expected == "matched":
            expected_match_count += 1
            if entry.row_ids in actual_match_groups:
                correct_matches += 1
            else:
                missed_matches += 1
        elif entry.expected == "exception":
            if entry.row_ids in actual_match_groups:
                false_matches += 1
        else:
            raise ValueError(
                f"GroundTruthEntry.expected must be 'matched' or 'exception', got {entry.expected!r}"
            )

    total_matches_produced = len(result.matches)
    total_exceptions_produced = len(result.exceptions)

    auto_match_precision = (
        (total_matches_produced - false_matches) / total_matches_produced
        if total_matches_produced
        else 1.0
    )
    auto_match_recall = (
        correct_matches / expected_match_count if expected_match_count else 1.0
    )
    false_match_rate = (
        false_matches / total_matches_produced if total_matches_produced else 0.0
    )

    unresolved_value = sum((e.total_amount for e in result.exceptions), Decimal("0"))
    exception_row_count = sum(len(e.transactions) for e in result.exceptions)

    return EvaluationReport(
        total_ground_truth_entries=len(ground_truth),
        correct_matches=correct_matches,
        missed_matches=missed_matches,
        false_matches=false_matches,
        total_matches_produced=total_matches_produced,
        total_exceptions_produced=total_exceptions_produced,
        auto_match_precision=round(auto_match_precision, 4),
        auto_match_recall=round(auto_match_recall, 4),
        false_match_rate=round(false_match_rate, 4),
        exception_coverage_count=exception_row_count,
        unresolved_value=unresolved_value,
    )
