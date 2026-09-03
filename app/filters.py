"""Date-range filtering, applied to parsed sources before reconciliation.

A controller rarely wants to reconcile everything ever uploaded -- they
want "this month" or "this quarter." Filtering happens here, on the
already-parsed `Transaction` lists, rather than inside `reconcile()`
itself: it's a separate, composable step (`filter_by_date_range(sources,
...)` then `reconcile(...)`), not a new parameter threaded through the
matching pipeline.

Both bounds are inclusive, matching how a person actually specifies a
range ("Jan 1 to Jan 31" means both days are in scope).
"""

from datetime import date

from app.models import Source, Transaction


def filter_by_date_range(
    sources: dict[Source, list[Transaction]],
    start: date | None = None,
    end: date | None = None,
) -> dict[Source, list[Transaction]]:
    """Returns a new sources dict with each source's transactions filtered
    to `start <= t.date <= end`. Either bound may be omitted for an
    open-ended range; omitting both returns `sources` filtered to itself
    (a no-op copy, not the same object).

    Raises ValueError if `start` is after `end` -- that range can never
    match anything, so it's almost certainly a caller mistake, not an
    intentionally empty result.
    """
    if start is not None and end is not None and start > end:
        raise ValueError(f"start date {start} is after end date {end}.")

    return {
        source: [
            t
            for t in transactions
            if (start is None or t.date >= start) and (end is None or t.date <= end)
        ]
        for source, transactions in sources.items()
    }
