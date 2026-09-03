"""Parser for the bank statement -- NOT YET IMPLEMENTED.

Bank statement export formats vary by bank and are often messy in exactly
the way that matters here: the settlement UTR reference may be truncated,
padded, or embedded in a longer narration string rather than in its own
column. Deliberately not guessing at a plausible-looking schema -- see
app/parsers/order_ledger.py's module docstring for why.

(BAI2 -- a real standardized bank statement format -- came up during
scoping as a possible source of prior art, but was explicitly set aside
for this project rather than assumed reusable.)

Implement this once a real bank statement export is available.
"""

from pathlib import Path

from app.models import Transaction


def parse_bank_statement(path: str | Path) -> list[Transaction]:
    raise NotImplementedError(
        "Bank statement format has not been provided yet -- see this "
        "module's docstring."
    )
