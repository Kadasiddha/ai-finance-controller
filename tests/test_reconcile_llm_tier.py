"""End-to-end proof of tier 3: the many-to-many scenario tiers 1-2
structurally can't resolve (two different-UTR settlement rows whose
combined amount matches one bank credit), and the opt-in behavior itself
-- reconcile() without adjudicate_fn must leave this exactly as it is
today (3 separate exceptions), unchanged.
"""

import json
from datetime import date
from decimal import Decimal

from app.matching.adjudicate import adjudicate
from app.models import Transaction
from app.reconcile import reconcile


def _settlement_txn(row_id, amount, utr, d=date(2026, 4, 1)):
    return Transaction(
        source="razorpay_settlement",
        source_row_id=row_id,
        amount=Decimal(amount),
        date=d,
        settlement_utr=utr,
    )


def _bank_txn(row_id, amount, d=date(2026, 4, 1)):
    return Transaction(source="bank_statement", source_row_id=row_id, amount=Decimal(amount), date=d)


def _sources():
    return {
        "razorpay_settlement": [
            _settlement_txn("pay_a", "590.00", "RZRP_UTR_A"),
            _settlement_txn("pay_b", "410.00", "RZRP_UTR_B"),
        ],
        "bank_statement": [_bank_txn("bank_combined", "1000.00")],
    }


def _fake_llm_that_finds_the_combination(prompt: str) -> str:
    return json.dumps(
        {
            "groups": [
                {
                    "transaction_ids": [
                        "razorpay_settlement:pay_a",
                        "razorpay_settlement:pay_b",
                        "bank_statement:bank_combined",
                    ],
                    "reasoning": (
                        "pay_a (590.00) and pay_b (410.00) sum to 1000.00, matching "
                        "bank_combined's credit on the same date -- plausibly a "
                        "combined payout from two separate settlement batches."
                    ),
                    "confidence": 0.8,
                }
            ]
        }
    )


def test_default_reconcile_leaves_the_many_to_many_case_as_three_exceptions():
    # No adjudicate_fn -- tier 3 is off. This must be identical to today's
    # behavior: neither tier 1 (different UTRs) nor tier 2 (strictly 1:1,
    # neither individual amount is close to 1000.00) can touch this.
    result = reconcile(_sources())

    assert result.matches == []
    assert len(result.exceptions) == 3
    assert all(e.code == "NO_COUNTERPART_FOUND" for e in result.exceptions)


def _adjudicate_with_fake(left, right):
    # reconcile()'s adjudicate_fn contract is a 2-arg callable (left,
    # right) -> (matches, unmatched_left, unmatched_right) -- adjudicate()
    # itself takes a third call_llm arg, so it's wrapped here rather than
    # passed bare. Passing bare `adjudicate` would fall through to its
    # real Ollama default, which is exactly what every other test in this
    # project avoids (a real network call would make this test dependent
    # on a locally running Ollama server and ~20s slower, breaking the
    # "whole suite runs instantly" property every other test here has).
    return adjudicate(left, right, call_llm=_fake_llm_that_finds_the_combination)


def test_opting_into_tier_3_resolves_the_combination():
    result = reconcile(_sources(), adjudicate_fn=_adjudicate_with_fake)

    assert len(result.matches) == 1
    match = result.matches[0]
    assert match.tier == "llm"
    assert len(match.transactions) == 3
    assert result.exceptions == []
