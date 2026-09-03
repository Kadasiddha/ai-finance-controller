"""Parser for the business's own order ledger -- NOT YET IMPLEMENTED.

Unlike the Razorpay settlement report, there's no public standard for
this: it's whatever the user's own system exports. Deliberately not
guessing at a plausible-looking schema here -- see the project README's
"Status" section and app/parsers/razorpay_settlement.py's module docstring
for why (this project's whole premise is proving real numbers reconcile;
building against an invented format would defeat that before it starts).

Implement this once a real order ledger export/schema is available.
"""

from pathlib import Path

from app.models import Transaction


def parse_order_ledger(path: str | Path) -> list[Transaction]:
    raise NotImplementedError(
        "Order ledger format is business-specific and not yet provided -- "
        "see this module's docstring."
    )
