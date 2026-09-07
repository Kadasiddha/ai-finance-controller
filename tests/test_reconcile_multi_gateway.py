"""Regression test for a real bug found via end-to-end testing with all
3 gateways configured simultaneously (not just one gateway at a time,
which is all the per-gateway test files exercise).

With 3 settlement gateways, `order_ledger` sits on 3 parallel
`order_id`-keyed legs (one per gateway) and `bank_statement` sits on 3
parallel `settlement_utr`-keyed legs. Before the fix, a ledger row that
matched via its real gateway (say Razorpay) was ALSO reported as a
`NO_COUNTERPART_FOUND` exception for the other two gateways it was never
associated with in the first place -- the same transaction appeared
simultaneously as a genuine match AND a false exception, contradicting
the project's own core principle that a confirmed match is confirmed.

Caught by feeding all 3 gateways real, combined data through `reconcile()`
in one call and reading the actual output, not by isolated per-gateway
fixtures (each of which only ever configures one gateway at a time, so
this cross-leg false-exception path never had a chance to fire).
"""

from datetime import date
from decimal import Decimal

from app.models import Transaction
from app.reconcile import reconcile


def _txn(source, row_id, order_id=None, settlement_utr=None, amount="100.00", d=date(2026, 2, 1)):
    return Transaction(
        source=source,
        source_row_id=row_id,
        amount=Decimal(amount),
        date=d,
        order_id=order_id,
        settlement_utr=settlement_utr,
    )


def test_ledger_row_matched_via_one_gateway_is_not_also_a_false_exception():
    sources = {
        "order_ledger": [_txn("order_ledger", "#1", order_id="#1", amount="100.00")],
        "razorpay_settlement": [
            _txn("razorpay_settlement", "pay_1", order_id="#1", amount="95.00")
        ],
        "stripe_settlement": [],
        "payu_settlement": [],
    }
    result = reconcile(sources)

    matched_row_ids = {t.source_row_id for m in result.matches for t in m.transactions}
    exception_row_ids = {t.source_row_id for e in result.exceptions for t in e.transactions}

    assert "#1" in matched_row_ids
    # The whole point: a row that matched via Razorpay must not ALSO show
    # up as "missing from stripe_settlement/payu_settlement" -- it was
    # never a Stripe or PayU order to begin with.
    assert "#1" not in exception_row_ids


def test_ledger_row_missing_from_every_gateway_still_becomes_one_exception():
    sources = {
        "order_ledger": [_txn("order_ledger", "#2", order_id="#2", amount="100.00")],
        "razorpay_settlement": [],
        "stripe_settlement": [],
        "payu_settlement": [_txn("payu_settlement", "pay_other", order_id="#other", amount="50.00")],
    }
    result = reconcile(sources)

    order_2_exceptions = [
        e for e in result.exceptions for t in e.transactions if t.source_row_id == "#2"
    ]
    assert len(order_2_exceptions) == 1
    assert order_2_exceptions[0].code == "NO_COUNTERPART_FOUND"


def test_settlement_row_can_still_be_both_matched_and_an_exception_simultaneously():
    # Different keys (order_id vs settlement_utr) describe genuinely
    # different relationships -- this dual-status case must still work
    # after the fix, unlike the same-key multi-gateway case above.
    sources = {
        "order_ledger": [_txn("order_ledger", "#3", order_id="#3", amount="100.00")],
        "razorpay_settlement": [
            _txn("razorpay_settlement", "pay_3", order_id="#3", settlement_utr=None, amount="95.00")
        ],
        "bank_statement": [],
    }
    result = reconcile(sources)

    matched_row_ids = {t.source_row_id for m in result.matches for t in m.transactions}
    exception_row_ids = {t.source_row_id for e in result.exceptions for t in e.transactions}

    assert "pay_3" in matched_row_ids  # confirmed against its order
    assert "pay_3" in exception_row_ids  # AND still missing a bank credit


def test_rejected_group_does_not_also_get_a_redundant_no_counterpart_exception():
    # A bank row that shares a UTR with a razorpay row but fails
    # amounts_reconcile already gets a specific, diagnosed AMOUNT_MISMATCH
    # exception for that. It also, correctly, has no Stripe/PayU
    # counterpart -- but that's the same underlying fact, not a SECOND
    # real problem, so it must not also produce a generic
    # NO_COUNTERPART_FOUND exception for the same row under the same key.
    bank_row = _txn(
        "bank_statement", "bank_1", settlement_utr="UTR1", amount="100.00"
    )
    razorpay_row = _txn(
        "razorpay_settlement", "pay_4", settlement_utr="UTR1", amount="50.00"
    )
    sources = {
        "razorpay_settlement": [razorpay_row],
        "stripe_settlement": [],
        "payu_settlement": [],
        "bank_statement": [bank_row],
    }
    result = reconcile(sources)

    bank_row_exceptions = [
        e for e in result.exceptions for t in e.transactions if t.source_row_id == "bank_1"
    ]
    assert len(bank_row_exceptions) == 1
    assert bank_row_exceptions[0].code == "AMOUNT_MISMATCH"
