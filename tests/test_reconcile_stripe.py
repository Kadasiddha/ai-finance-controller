"""End-to-end proof that KNOWN_LEGS generalizes to a second gateway with
zero pipeline changes -- a small, self-contained fixture (deliberately
not reusing the Razorpay-flavored fixture in conftest.py, since Stripe's
realistic behavior is genuinely different, not just a renamed copy).

Two things proven here, both direct consequences of the real differences
found while grounding the Stripe parser against Stripe's own docs (see
app/parsers/stripe_settlement.py's module docstring):

1. order_ledger<->stripe_settlement matches exactly on order_id (tier 1)
   -- a real, reliable field for any gateway.
2. stripe_settlement<->bank_statement has no shared exact-match key,
   because Stripe's trace_id isn't expected to appear in a bank
   narration the way Razorpay's UTR does -- so this leg realistically
   falls through to tier 2 (fuzzy amount+date). This is the existing
   fallback path working as designed, not new code.

A third test documents a genuine limitation surfaced by this: fuzzy
matching is strictly one-to-one (see app/matching/fuzzy.py's docstring),
so a Stripe payout batching a charge and a refund together -- which,
without a shared bank-side reference, can't exact-match either -- can't
be reconstructed into one correct match at all. It correctly becomes
exceptions rather than a wrong match, but not the ideal single netted
match a human would recognize. Captured as a known gap, not hidden.
"""

from pathlib import Path

import pytest

from app.parsers.bank_statement import parse_bank_statement
from app.parsers.order_ledger import parse_order_ledger
from app.parsers.stripe_settlement import parse_stripe_settlement
from app.reconcile import reconcile


def _write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


@pytest.fixture
def happy_path_sources(tmp_path: Path) -> dict:
    ledger_csv = _write(
        tmp_path / "orders_export.csv",
        "Name,Financial Status,Paid at,Total\n"
        "order_9001,paid,2026-01-03 14:30:00 +0000,50.00\n",
    )
    settlement_csv = _write(
        tmp_path / "stripe_settlement.csv",
        "balance_transaction_id,reporting_category,created,currency,gross,fee,net,order_id,trace_id,automatic_payout_id\n"
        "txn_charge001,charge,2026-01-03 14:30:00,usd,50.00,1.75,48.25,order_9001,,po_batch01\n",
    )
    bank_csv = _write(
        tmp_path / "bank_statement.csv",
        "Date,Narration,Reference,Withdrawal,Deposit,Balance\n"
        "2026-01-03,STRIPE PAYOUT,,0,48.25,100048.25\n",
    )
    return {
        "order_ledger": parse_order_ledger(ledger_csv),
        "stripe_settlement": parse_stripe_settlement(settlement_csv),
        "bank_statement": parse_bank_statement(bank_csv),
    }


def test_ledger_and_settlement_match_exactly_on_order_id(happy_path_sources):
    result = reconcile(happy_path_sources)
    order_matches = [
        m
        for m in result.matches
        if any(t.order_id == "order_9001" for t in m.transactions)
        and {t.source for t in m.transactions} == {"order_ledger", "stripe_settlement"}
    ]
    assert len(order_matches) == 1
    assert order_matches[0].tier == "exact"


def test_settlement_and_bank_fall_through_to_fuzzy_not_exact(happy_path_sources):
    # Neither side has a usable settlement_utr (Stripe's trace_id wasn't
    # provided here, and even when it is, bank_statement's extractor
    # isn't taught to recognize it -- see the module docstring) -- so
    # this leg must be caught by tier 2, not tier 1.
    result = reconcile(happy_path_sources)
    bank_matches = [
        m
        for m in result.matches
        if {t.source for t in m.transactions} == {"stripe_settlement", "bank_statement"}
    ]
    assert len(bank_matches) == 1
    assert bank_matches[0].tier == "fuzzy"


def test_nothing_from_the_happy_path_becomes_an_exception(happy_path_sources):
    result = reconcile(happy_path_sources)
    assert result.exceptions == []


@pytest.fixture
def batched_refund_sources(tmp_path: Path) -> dict:
    # A charge and a refund batched into the same real Stripe payout
    # (same trace_id), netting to a single bank credit -- but since
    # neither stripe row's individual amount is close to the bank row's
    # combined net, and fuzzy matching is strictly 1:1, this is expected
    # to correctly become exceptions rather than a wrong or missing match.
    settlement_csv = _write(
        tmp_path / "stripe_settlement_batch.csv",
        "balance_transaction_id,reporting_category,created,currency,gross,fee,net,order_id,trace_id,automatic_payout_id\n"
        "txn_charge002,charge,2026-01-05 10:00:00,usd,50.00,1.75,48.25,order_9002,TRC999,po_batch02\n"
        "txn_refund002,refund,2026-01-05 10:05:00,usd,-20.00,0,-20.00,order_9003,TRC999,po_batch02\n",
    )
    bank_csv = _write(
        tmp_path / "bank_statement_batch.csv",
        "Date,Narration,Reference,Withdrawal,Deposit,Balance\n"
        "2026-01-05,STRIPE PAYOUT,,0,28.25,100028.25\n",
    )
    return {
        "stripe_settlement": parse_stripe_settlement(settlement_csv),
        "bank_statement": parse_bank_statement(bank_csv),
    }


def test_batched_charge_and_refund_cannot_be_netted_without_a_shared_reference(
    batched_refund_sources,
):
    # Documents a real, known gap (see this file's module docstring):
    # both stripe rows and the bank row end up as separate
    # NO_COUNTERPART_FOUND exceptions, not one correct netted match --
    # and, just as importantly, not a WRONG match either.
    result = reconcile(batched_refund_sources)
    assert result.matches == []
    assert len(result.exceptions) == 3
    assert all(e.code == "NO_COUNTERPART_FOUND" for e in result.exceptions)
