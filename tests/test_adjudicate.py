"""Tests for tier 3 (LLM adjudication) using an injected fake `call_llm`
-- no real Ollama server needed, keeping the whole suite instant and
dependency-free like every other test in this project.
"""

import json
from datetime import date
from decimal import Decimal

import requests

from app.matching.adjudicate import adjudicate
from app.models import Transaction


def _txn(source, row_id, amount, d=date(2026, 4, 1), description=None):
    return Transaction(
        source=source, source_row_id=row_id, amount=Decimal(amount), date=d, description=description
    )


def _fake_llm(response: dict):
    def call_llm(prompt: str) -> str:
        return json.dumps(response)

    return call_llm


def test_valid_proposal_is_accepted_as_tier_llm():
    left = [_txn("razorpay_settlement", "pay_1", "590.00"), _txn("razorpay_settlement", "pay_2", "410.00")]
    right = [_txn("bank_statement", "bank_1", "1000.00")]

    fake_response = {
        "groups": [
            {
                "transaction_ids": ["razorpay_settlement:pay_1", "razorpay_settlement:pay_2", "bank_statement:bank_1"],
                "reasoning": "pay_1 and pay_2 sum to 1000.00, matching bank_1's combined credit.",
                "confidence": 0.85,
            }
        ]
    }
    matches, unmatched_left, unmatched_right = adjudicate(left, right, call_llm=_fake_llm(fake_response))

    assert len(matches) == 1
    assert matches[0].tier == "llm"
    assert matches[0].confidence == 0.85
    assert len(matches[0].transactions) == 3
    assert unmatched_left == []
    assert unmatched_right == []


def test_proposal_referencing_unknown_transaction_id_is_rejected():
    left = [_txn("razorpay_settlement", "pay_1", "590.00")]
    right = [_txn("bank_statement", "bank_1", "590.00")]

    fake_response = {
        "groups": [
            {
                "transaction_ids": ["razorpay_settlement:pay_1", "razorpay_settlement:pay_NEVER_GIVEN"],
                "reasoning": "hallucinated",
                "confidence": 0.9,
            }
        ]
    }
    matches, unmatched_left, unmatched_right = adjudicate(left, right, call_llm=_fake_llm(fake_response))

    assert matches == []
    assert unmatched_left == left
    assert unmatched_right == right


def test_proposal_that_does_not_actually_reconcile_is_rejected_despite_high_confidence():
    left = [_txn("razorpay_settlement", "pay_1", "500.00")]
    right = [_txn("bank_statement", "bank_1", "999.00")]  # nowhere close to 500.00

    fake_response = {
        "groups": [
            {
                "transaction_ids": ["razorpay_settlement:pay_1", "bank_statement:bank_1"],
                "reasoning": "these are definitely related",
                "confidence": 0.99,
            }
        ]
    }
    matches, unmatched_left, unmatched_right = adjudicate(left, right, call_llm=_fake_llm(fake_response))

    assert matches == []
    assert unmatched_left == left
    assert unmatched_right == right


def test_low_confidence_proposal_is_rejected_even_if_amounts_reconcile():
    left = [_txn("razorpay_settlement", "pay_1", "500.00")]
    right = [_txn("bank_statement", "bank_1", "500.00")]

    fake_response = {
        "groups": [
            {
                "transaction_ids": ["razorpay_settlement:pay_1", "bank_statement:bank_1"],
                "reasoning": "maybe related, not sure",
                "confidence": 0.3,
            }
        ]
    }
    matches, unmatched_left, unmatched_right = adjudicate(left, right, call_llm=_fake_llm(fake_response))

    assert matches == []
    assert unmatched_left == left


def test_unreachable_ollama_never_crashes_everything_stays_unmatched():
    # A real gap found while building the CLI: an unreachable Ollama
    # server (not running, wrong port, etc.) raises requests.RequestException
    # from call_ollama -- this must degrade the same way a malformed
    # response does, not propagate up and crash reconcile().
    left = [_txn("razorpay_settlement", "pay_1", "500.00")]
    right = [_txn("bank_statement", "bank_1", "500.00")]

    def unreachable_llm(prompt: str) -> str:
        raise requests.exceptions.ConnectionError("Connection refused")

    matches, unmatched_left, unmatched_right = adjudicate(left, right, call_llm=unreachable_llm)

    assert matches == []
    assert unmatched_left == left
    assert unmatched_right == right


def test_malformed_json_response_never_crashes_everything_stays_unmatched():
    left = [_txn("razorpay_settlement", "pay_1", "500.00")]
    right = [_txn("bank_statement", "bank_1", "500.00")]

    def broken_llm(prompt: str) -> str:
        return "this is not json at all"

    matches, unmatched_left, unmatched_right = adjudicate(left, right, call_llm=broken_llm)

    assert matches == []
    assert unmatched_left == left
    assert unmatched_right == right


def test_empty_groups_response_leaves_everything_unmatched():
    left = [_txn("razorpay_settlement", "pay_1", "500.00")]
    right = [_txn("bank_statement", "bank_1", "999.00")]

    matches, unmatched_left, unmatched_right = adjudicate(left, right, call_llm=_fake_llm({"groups": []}))

    assert matches == []
    assert unmatched_left == left
    assert unmatched_right == right


def test_no_transactions_at_all_returns_immediately_without_calling_llm():
    def never_call(prompt: str) -> str:
        raise AssertionError("should not be called when there's nothing to adjudicate")

    matches, unmatched_left, unmatched_right = adjudicate([], [], call_llm=never_call)
    assert matches == []


def test_overlapping_groups_do_not_double_claim_the_same_transaction():
    left = [_txn("razorpay_settlement", "pay_1", "500.00")]
    right = [
        _txn("bank_statement", "bank_1", "500.00"),
        _txn("bank_statement", "bank_2", "500.00"),
    ]

    fake_response = {
        "groups": [
            {
                "transaction_ids": ["razorpay_settlement:pay_1", "bank_statement:bank_1"],
                "reasoning": "first proposal",
                "confidence": 0.9,
            },
            {
                "transaction_ids": ["razorpay_settlement:pay_1", "bank_statement:bank_2"],
                "reasoning": "second proposal, same pay_1 again",
                "confidence": 0.9,
            },
        ]
    }
    matches, unmatched_left, unmatched_right = adjudicate(left, right, call_llm=_fake_llm(fake_response))

    assert len(matches) == 1  # only the first-accepted proposal wins
    assert unmatched_left == []
    assert len(unmatched_right) == 1  # the other bank row stays unmatched
