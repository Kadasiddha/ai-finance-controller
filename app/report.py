"""CSV export of a ReconciliationResult -- the actual downloadable
artifact a controller walks away with, not just an in-memory result.

One CSV, not a matches/exceptions file pair: a single file is what
"download the report" means to a non-technical user, and `record_type`
plus `tier_or_code` are enough to filter/sort it in Excel however they
want (e.g. show only AMOUNT_MISMATCH exceptions, or sort by value).

Exceptions are written highest-value first (`exceptions_by_value`) --
that's the actual triage order: the costliest discrepancy belongs at the
top of the sheet, not buried under matches.
"""

import csv
from decimal import Decimal
from pathlib import Path

from app.models import ExceptionRecord, MatchResult, Transaction
from app.reconcile import ReconciliationResult

REPORT_FIELDNAMES = [
    "record_type",
    "tier_or_code",
    "total_amount",
    "sources_involved",
    "transaction_ids",
    "dates",
    "confidence",
    "detail",
]


def _sources_involved(transactions: list[Transaction]) -> str:
    return ";".join(sorted({t.source for t in transactions}))


def _transaction_ids(transactions: list[Transaction]) -> str:
    return ";".join(f"{t.source}:{t.source_row_id}" for t in transactions)


def _dates(transactions: list[Transaction]) -> str:
    return ";".join(str(t.date) for t in transactions)


def _match_row(match: MatchResult) -> dict:
    total = sum((t.amount for t in match.transactions), start=Decimal("0"))
    return {
        "record_type": "match",
        "tier_or_code": match.tier,
        "total_amount": str(total),
        "sources_involved": _sources_involved(match.transactions),
        "transaction_ids": _transaction_ids(match.transactions),
        "dates": _dates(match.transactions),
        "confidence": "" if match.confidence is None else f"{match.confidence:.2f}",
        "detail": match.reasoning,
    }


def _exception_row(exception: ExceptionRecord) -> dict:
    return {
        "record_type": "exception",
        "tier_or_code": exception.code,
        "total_amount": str(exception.total_amount),
        "sources_involved": _sources_involved(exception.transactions),
        "transaction_ids": _transaction_ids(exception.transactions),
        "dates": _dates(exception.transactions),
        "confidence": "",
        "detail": exception.reason,
    }


def write_csv_report(result: ReconciliationResult, path: str | Path) -> Path:
    """Writes `result` (an `app.reconcile.ReconciliationResult`) to a CSV
    at `path`: all matches, then all exceptions sorted by rupee value
    descending. Returns `path` for convenient chaining.
    """
    path = Path(path)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=REPORT_FIELDNAMES)
        writer.writeheader()
        for match in result.matches:
            writer.writerow(_match_row(match))
        for exception in result.exceptions_by_value:
            writer.writerow(_exception_row(exception))
    return path
