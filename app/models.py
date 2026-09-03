from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Literal

Source = Literal["order_ledger", "razorpay_settlement", "bank_statement"]
MatchTier = Literal["exact", "fuzzy", "llm"]


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
    """

    transactions: list[Transaction]
    reason: str

    @property
    def total_amount(self) -> Decimal:
        return sum((t.amount for t in self.transactions), Decimal("0"))
