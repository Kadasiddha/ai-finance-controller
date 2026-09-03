from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Literal

Source = Literal["order_ledger", "razorpay_settlement", "bank_statement", "stripe_settlement"]
MatchTier = Literal["exact", "fuzzy", "llm"]

# Structured exception categories -- lets exceptions be counted/aggregated
# by cause, not just read one at a time. Some of these aren't triggerable
# yet by the current matching logic (no refund/adjustment/partial-
# settlement handling, no tier 3 LLM) -- they're declared now so the code
# doesn't need renaming later, not because they're all in use today.
ExceptionCode = Literal[
    "NO_COUNTERPART_FOUND",
    "AMOUNT_MISMATCH",
    "DATE_WINDOW_EXCEEDED",
    "DUPLICATE_REFERENCE",
    "DUPLICATE_CANDIDATE",
    "UNEXPECTED_FEE_OR_TAX",
    "REFUND_OR_CHARGEBACK_CONFLICT",
    "PARTIAL_SETTLEMENT",
    "SPLIT_OR_MERGED_SETTLEMENT",
    "LOW_CONFIDENCE_LLM",
    "CONTRADICTORY_DATA",
]


@dataclass(frozen=True)
class Transaction:
    """A single record from any of the three sources, normalized to a
    common shape so the matching tiers never need to know which source a
    row came from.

    Amounts are Decimal, not float -- this project exists to prove money
    reconciles exactly, so floating-point rounding error is not
    acceptable here.
    """

    source: Source
    source_row_id: str
    amount: Decimal
    date: date
    order_id: str | None = None
    settlement_utr: str | None = None
    description: str | None = None
    # Excluded from eq/hash: a dict can't be hashed, and two Transactions
    # representing the same normalized row should compare equal regardless
    # of which raw source columns happened to produce them.
    raw: dict[str, Any] = field(default_factory=dict, compare=False, hash=False)


@dataclass(frozen=True)
class MatchResult:
    """A confirmed match between transactions from different sources.

    `transactions` is a group, not a pair -- settlement is batched, so an
    exact match on `settlement_utr` naturally groups many settlement-report
    rows against one bank-statement row.
    """

    tier: MatchTier
    transactions: list[Transaction]
    reasoning: str
    confidence: float | None = None  # only meaningful for "fuzzy"/"llm" tiers


@dataclass(frozen=True)
class ExceptionRecord:
    """Something that did not confidently match at any tier. Never a
    forced/guessed match -- always carries an explicit reason so a human
    knows exactly what to go look at.

    `code` is the structured category (for counting/aggregating exceptions
    by cause -- "how many AMOUNT_MISMATCH vs NO_COUNTERPART_FOUND this
    month"); `reason` is the human-readable detail for that specific case.
    """

    transactions: list[Transaction]
    code: ExceptionCode
    reason: str

    @property
    def total_amount(self) -> Decimal:
        return sum((t.amount for t in self.transactions), Decimal("0"))
