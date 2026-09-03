"""Parser for the bank statement.

Built against the common real shape of Indian bank CSV exports: one
transaction per row with Date, Narration, Reference, Withdrawal, Deposit,
and running Balance columns (this convention -- and the fact that banks
differ on delimiters and truncate narrations to 50-100 chars -- is
consistent across multiple independent real sources, not invented here).
Still a stand-in for a specific bank's actual export until a real one is
available -- the exact column names/order vary by bank.

The interesting real problem lives in `Narration`, not in a clean
dedicated UTR column: a Razorpay settlement credit shows up as free text
like `"NEFT CR:RZRP173069230703 RAZORPAY SOFTWARE PRIVATE"`, and banks
routinely truncate the trailing digits -- the ones that make the UTR
unique. Razorpay's own settlement UTRs are recognizably prefixed `RZRP`
(confirmed against real fixtures from razorpay-python's test suite), so
extraction looks for that prefix specifically rather than trying to parse
arbitrary bank reference formats in general.

Because truncation is common and expected, this extractor deliberately
does NOT try to guarantee a complete UTR -- it returns whatever's present,
truncated or not. A truncated UTR simply won't win an exact match in
tier 1; that's tier 2 (amount + date window)'s job to catch instead, which
is the realistic behavior, not a bug to work around here.

`Transaction.amount` is signed: `Deposit - Withdrawal`. An earlier version
of this parser only read `Deposit` and skipped every `Withdrawal` row
outright -- meaning refund/cashback payouts (money leaving the account)
were invisible to the engine entirely, a real gap found when asked
directly whether refunds were handled. Reading both and signing the
result is also what makes a refund correctly net against its original
payment in `amounts_reconcile`'s batch-total check, without needing
separate refund-matching logic.
"""

import csv
import re
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from app.models import Transaction

REQUIRED_COLUMNS = {"Date", "Narration", "Deposit", "Withdrawal"}

# Razorpay settlement UTRs are alphanumeric, prefixed "RZRP" -- extract
# whatever run of alphanumeric characters follows, truncated or not.
_UTR_PATTERN = re.compile(r"RZRP[A-Z0-9]+", re.IGNORECASE)


def extract_settlement_utr(narration: str) -> str | None:
    match = _UTR_PATTERN.search(narration or "")
    return match.group(0).upper() if match else None


def parse_bank_statement(path: str | Path) -> list[Transaction]:
    path = Path(path)
    transactions: list[Transaction] = []

    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)

        missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])
        if missing:
            raise ValueError(
                f"Bank statement is missing expected columns: {sorted(missing)}. "
                f"Found: {reader.fieldnames}"
            )

        for row in reader:
            deposit = Decimal((row.get("Deposit") or "0").strip() or "0")
            withdrawal = Decimal((row.get("Withdrawal") or "0").strip() or "0")
            if deposit == 0 and withdrawal == 0:
                continue  # nothing moved -- not a real transaction row

            narration = row.get("Narration") or ""
            transactions.append(
                Transaction(
                    source="bank_statement",
                    source_row_id=row.get("Reference") or f"{row['Date']}:{narration[:30]}",
                    amount=deposit - withdrawal,
                    date=datetime.strptime(row["Date"].strip(), "%Y-%m-%d").date(),
                    settlement_utr=extract_settlement_utr(narration),
                    description=narration,
                    raw=dict(row),
                )
            )

    return transactions
