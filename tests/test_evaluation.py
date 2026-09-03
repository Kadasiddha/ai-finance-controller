"""Tests for the evaluation framework, run against the same fixture
dataset as tests/test_reconcile_end_to_end.py (see conftest.py for the
scenario breakdown).

Ground-truth row IDs are looked up from the actually-parsed transactions
(by their distinguishing fields) rather than hand-typed, since the bank
parser derives an ID from a truncated narration string that's error-prone
to compute by hand -- looking it up avoids a test that's fragile against
its own arithmetic rather than against real behavior.
"""

from decimal import Decimal

from app.evaluation import GroundTruthEntry, evaluate
from app.models import MatchResult, Transaction
from app.reconcile import ReconciliationResult, reconcile


def _find(transactions: list[Transaction], **criteria) -> Transaction:
    for t in transactions:
        if all(getattr(t, k) == v for k, v in criteria.items()):
            return t
    raise AssertionError(f"No transaction matching {criteria} in fixture data")


def _build_ground_truth(parsed_sources: dict) -> list[GroundTruthEntry]:
    ledger = parsed_sources["order_ledger"]
    settlement = parsed_sources["razorpay_settlement"]
    bank = parsed_sources["bank_statement"]

    l4471 = _find(ledger, order_id="#4471")
    l4472 = _find(ledger, order_id="#4472")
    l4473 = _find(ledger, order_id="#4473")
    l4474 = _find(ledger, order_id="#4474")
    l4475 = _find(ledger, order_id="#4475")
    l4476 = _find(ledger, order_id="#4476")

    pay_1 = _find(settlement, source_row_id="pay_1")
    pay_2 = _find(settlement, source_row_id="pay_2")
    pay_3 = _find(settlement, source_row_id="pay_3")
    pay_4 = _find(settlement, source_row_id="pay_4")
    pay_6 = _find(settlement, source_row_id="pay_6")

    bank_1 = _find(bank, settlement_utr="RZRP1000000001")
    bank_2 = _find(bank, settlement_utr="RZRP1000000002")
    bank_3 = _find(bank, amount=Decimal("1464.60"))  # truncated-UTR row
    bank_4 = _find(bank, amount=Decimal("3000.00"))  # wrong-amount row

    def ids(*txns) -> frozenset:
        return frozenset(t.source_row_id for t in txns)

    return [
        # Positive: clean 1:1 matches, both legs.
        GroundTruthEntry(ids(l4471, pay_1), "matched", "order 4471 <-> settlement"),
        GroundTruthEntry(ids(pay_1, bank_1), "matched", "settlement <-> bank, UTR 001"),
        # Positive: batched settlement.
        GroundTruthEntry(ids(l4472, pay_2), "matched", "order 4472 <-> settlement"),
        GroundTruthEntry(ids(l4473, pay_3), "matched", "order 4473 <-> settlement"),
        GroundTruthEntry(ids(pay_2, pay_3, bank_2), "matched", "batched settlement -> one bank credit"),
        # Positive: truncated UTR falls back to fuzzy match.
        GroundTruthEntry(ids(l4474, pay_4), "matched", "order 4474 <-> settlement"),
        GroundTruthEntry(ids(pay_4, bank_3), "matched", "settlement <-> bank via fuzzy (truncated UTR)"),
        # Negative: genuinely missing counterpart.
        GroundTruthEntry(ids(l4475), "exception", "order 4475 has no settlement at all"),
        # Positive half + negative half of the same settlement row.
        GroundTruthEntry(ids(l4476, pay_6), "matched", "order 4476 <-> settlement (this part IS real)"),
        GroundTruthEntry(ids(pay_6, bank_4), "exception", "settlement <-> bank: wrong amount, must NOT match"),
    ]


def test_evaluation_against_known_good_fixture(parsed_sources):
    result = reconcile(parsed_sources)
    ground_truth = _build_ground_truth(parsed_sources)

    report = evaluate(result, ground_truth)

    # This fixture is specifically designed so the engine gets everything
    # right -- precision and recall should both be perfect, and there
    # should be zero false matches, since the whole point of the tiered
    # design is to never force a wrong match.
    assert report.auto_match_precision == 1.0
    assert report.auto_match_recall == 1.0
    assert report.false_match_rate == 0.0
    assert report.false_matches == 0
    assert report.missed_matches == 0
    assert report.correct_matches == 8  # the 8 "matched" entries above


def test_unresolved_value_reflects_real_exceptions(parsed_sources):
    result = reconcile(parsed_sources)
    ground_truth = _build_ground_truth(parsed_sources)
    report = evaluate(result, ground_truth)

    # #4475 (₹700, no counterpart) + the pay_6/bank_4 mismatch (settlement
    # net ₹4882.00 + bank's claimed ₹3000.00) are the only real exceptions.
    expected_unresolved = Decimal("700.00") + Decimal("4882.00") + Decimal("3000.00")
    assert report.unresolved_value == expected_unresolved
    assert report.exception_coverage_count == 3  # 1 ledger row + 2-row mismatch group


def test_evaluator_catches_a_genuine_false_match(parsed_sources):
    # Sanity-check the evaluator itself with a synthetic, deliberately
    # wrong ReconciliationResult (not the real engine's output) -- proves
    # the evaluator actually flags a bad match rather than just happening
    # to report zeros because the current engine behaves correctly.
    settlement = parsed_sources["razorpay_settlement"]
    bank = parsed_sources["bank_statement"]
    pay_6 = _find(settlement, source_row_id="pay_6")
    bank_4 = _find(bank, amount=Decimal("3000.00"))

    fake_bad_result = ReconciliationResult(
        matches=[
            MatchResult(
                tier="exact",
                transactions=[pay_6, bank_4],
                reasoning="(synthetic, deliberately wrong, for this test only)",
            )
        ],
        exceptions=[],
    )
    ground_truth = [
        GroundTruthEntry(
            frozenset({pay_6.source_row_id, bank_4.source_row_id}),
            "exception",
            "this pair must never be matched",
        ),
    ]

    report = evaluate(fake_bad_result, ground_truth)

    assert report.false_matches == 1
    assert report.false_match_rate == 1.0
    assert report.auto_match_precision == 0.0
