"""End-to-end proof that KNOWN_LEGS generalizes to a third gateway with
zero pipeline changes -- mirrors tests/test_reconcile_stripe.py's shape,
using a small, self-contained fixture (not conftest.py's Razorpay-flavored
one).

Three things proven here:

1. order_ledger<->payu_settlement matches exactly on order_id (tier 1).
2. payu_settlement<->bank_statement falls through to tier 2 (fuzzy) --
   same observed behavior as Stripe, but for a different real reason (see
   app/parsers/payu_settlement.py's module docstring: PayU's UTR isn't
   prefixed the way Razorpay's is, and there's no confirmed narration
   pattern to recognize it by).
3. The genuinely new piece of logic in this task -- the synthetic
   adjustment row's arithmetic -- tested directly at the matching layer,
   not through the full parser-to-parser pipeline. That distinction
   matters: given the SAME real UTR-recognition gap already documented
   for Stripe (bank_statement.py's extractor doesn't recognize PayU's
   bare-numeric UTR in a narration either), a PayU batch with an
   adjustment does NOT currently reconcile end-to-end against its bank
   credit -- verified directly below, it degrades to three separate
   NO_COUNTERPART_FOUND exceptions, the same honest failure mode as
   Stripe's batched-refund test. What IS proven here is that the
   adjustment row's arithmetic is correct (`amounts_reconcile` accepts a
   batch that includes it) -- ready for the day the UTR-extractor gap is
   closed, not a false claim that it already works end-to-end today.
"""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.matching.exact import amounts_reconcile
from app.models import Transaction
from app.parsers.bank_statement import parse_bank_statement
from app.parsers.order_ledger import parse_order_ledger
from app.parsers.payu_settlement import parse_payu_settlement
from app.reconcile import reconcile


def _write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def _write_json(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.fixture
def happy_path_sources(tmp_path: Path) -> dict:
    ledger_csv = _write(
        tmp_path / "orders_export.csv",
        "Name,Financial Status,Paid at,Total\n"
        "order_5001,paid,2025-08-26 02:14:35 +0000,2480.00\n",
    )
    settlement_json = _write_json(
        tmp_path / "payu_settlement.json",
        {
            "result": {
                "data": [
                    {
                        "settlementId": "12127298202508260245",
                        "settlementCompletedDate": "2025-08-26 02:51:22.000000",
                        "settlementAmount": "2467.13",
                        "utrNumber": "523871332950",
                        "adjustmentAmount": "0.0",
                        "numberOfTransactions": 1,
                        "transaction": [
                            {
                                "payuId": "24868774786",
                                "merchantTransactionId": "order_5001",
                                "transactionAmount": "2480.0",
                                "merchantNetAmount": "2467.13",
                                "transactionDate": "2025-08-26 02:14:35.000000",
                                "action": "capture",
                            }
                        ],
                    }
                ]
            }
        },
    )
    bank_csv = _write(
        tmp_path / "bank_statement.csv",
        "Date,Narration,Reference,Withdrawal,Deposit,Balance\n"
        "2025-08-26,PAYU PAYOUT,,0,2467.13,102467.13\n",
    )
    return {
        "order_ledger": parse_order_ledger(ledger_csv),
        "payu_settlement": parse_payu_settlement(settlement_json),
        "bank_statement": parse_bank_statement(bank_csv),
    }


def test_ledger_and_settlement_match_exactly_on_order_id(happy_path_sources):
    result = reconcile(happy_path_sources)
    order_matches = [
        m
        for m in result.matches
        if any(t.order_id == "order_5001" for t in m.transactions)
        and {t.source for t in m.transactions} == {"order_ledger", "payu_settlement"}
    ]
    assert len(order_matches) == 1
    assert order_matches[0].tier == "exact"


def test_settlement_and_bank_fall_through_to_fuzzy_not_exact(happy_path_sources):
    result = reconcile(happy_path_sources)
    bank_matches = [
        m
        for m in result.matches
        if {t.source for t in m.transactions} == {"payu_settlement", "bank_statement"}
    ]
    assert len(bank_matches) == 1
    assert bank_matches[0].tier == "fuzzy"


def test_nothing_from_the_happy_path_becomes_an_exception(happy_path_sources):
    result = reconcile(happy_path_sources)
    assert result.exceptions == []


@pytest.fixture
def batch_with_adjustment_sources(tmp_path: Path) -> dict:
    # A capture netting 2467.13, but the batch's real settlementAmount is
    # 1479.82 -- a -987.31 unattributed adjustment (refund/chargeback)
    # that the parser must surface as its own row for the batch total to
    # actually balance against the bank credit.
    settlement_json = _write_json(
        tmp_path / "payu_with_adjustment.json",
        {
            "result": {
                "data": [
                    {
                        "settlementId": "batch002",
                        "settlementCompletedDate": "2025-08-27 02:51:22.000000",
                        "settlementAmount": "1479.82",
                        "utrNumber": "111222333444",
                        "adjustmentAmount": "-987.31",
                        "numberOfTransactions": 1,
                        "transaction": [
                            {
                                "payuId": "txn_batch002",
                                "merchantTransactionId": "order_5002",
                                "transactionAmount": "2480.0",
                                "merchantNetAmount": "2467.13",
                                "transactionDate": "2025-08-27 02:14:35.000000",
                                "action": "capture",
                            }
                        ],
                    }
                ]
            }
        },
    )
    bank_csv = _write(
        tmp_path / "bank_statement_adjustment.csv",
        "Date,Narration,Reference,Withdrawal,Deposit,Balance\n"
        "2025-08-27,PAYU PAYOUT,,0,1479.82,103946.95\n",
    )
    return {
        "payu_settlement": parse_payu_settlement(settlement_json),
        "bank_statement": parse_bank_statement(bank_csv),
    }


def test_batch_with_unattributed_adjustment_becomes_exceptions_not_a_wrong_match(
    batch_with_adjustment_sources,
):
    # Honest current behavior: bank_statement's extractor doesn't recognize
    # PayU's UTR in "PAYU PAYOUT" (same gap as Stripe), so this leg never
    # reaches an exact-match group, and fuzzy matching can't combine the
    # two payu rows (capture + adjustment) into one 1:1 match against the
    # bank row either -- all three correctly become NO_COUNTERPART_FOUND
    # exceptions. Not a wrong match, but not the ideal netted answer
    # either -- same limitation already documented for Stripe.
    result = reconcile(batch_with_adjustment_sources)
    assert result.matches == []
    assert len(result.exceptions) == 3
    assert all(e.code == "NO_COUNTERPART_FOUND" for e in result.exceptions)


def test_adjustment_row_arithmetic_makes_the_batch_balance_at_the_matching_layer():
    # Proves the design itself is sound, independent of the real
    # UTR-recognition gap above: IF a payu batch's capture + synthetic
    # adjustment rows were grouped against their bank credit (e.g. once
    # the extractor gap above is closed, or via any other real linkage),
    # amounts_reconcile would correctly accept the group -- the adjustment
    # row's residual arithmetic genuinely cancels out, not just on paper.
    capture = Transaction(
        source="payu_settlement",
        source_row_id="txn_batch002",
        amount=Decimal("2467.13"),
        date=date(2025, 8, 27),
        settlement_utr="111222333444",
    )
    adjustment = Transaction(
        source="payu_settlement",
        source_row_id="batch002:adjustment",
        amount=Decimal("-987.31"),
        date=date(2025, 8, 27),
        settlement_utr="111222333444",
    )
    bank_credit = Transaction(
        source="bank_statement",
        source_row_id="bank_row_1",
        amount=Decimal("1479.82"),
        date=date(2025, 8, 27),
        settlement_utr="111222333444",
    )
    assert amounts_reconcile([capture, adjustment, bank_credit])
    # And without the adjustment row, the same group would NOT balance --
    # confirming the adjustment row is what makes the difference, not
    # amounts_reconcile's tolerance alone.
    assert not amounts_reconcile([capture, bank_credit])
