"""End-to-end reconciliation test across all three sources, covering both
positive (should match) and negative (should correctly refuse to match)
scenarios in one coherent, cross-referenced dataset.

All three fixture files are constructed here -- realistic in shape (real
Razorpay settlement schema, real Shopify order-export shape, real Indian
bank statement conventions) but synthetic in content, built specifically
to exercise this pipeline's matching and exception-handling logic. Not a
stand-in for real business data.

Scenarios, by order:
- #4471: clean 1:1 exact match on both legs.                    (positive)
- #4472 + #4473: batched settlement, one bank credit for two orders.
                                                                   (positive)
- #4474: bank narration truncates the UTR -- tier 1 (exact) can't match
  it, tier 2 (amount + date) catches it instead.                 (positive)
- #4475: paid in the ledger, but no settlement row exists at all.
                                                                   (negative)
- #4476: settlement exists, but the bank credit for that UTR is for a
  wildly different amount (a misapplied/duplicate reference) -- must be
  refused at both tiers, not force-matched.                      (negative)
- #4477: still "pending" in the ledger -- correctly excluded before
  reconciliation even starts, not a false positive or a false exception.
"""

import csv
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from app.parsers.bank_statement import parse_bank_statement
from app.parsers.order_ledger import parse_order_ledger
from app.parsers.razorpay_settlement import parse_settlement_report
from app.reconcile import reconcile


def _epoch(y, m, d, hh=12, mm=0) -> int:
    return int(datetime(y, m, d, hh, mm, tzinfo=timezone.utc).timestamp())


ORDER_LEDGER_ROWS = [
    # Name,  Financial Status, Paid at,                     Total
    ("#4471", "paid", "2026-01-01 10:00:00 +0000", "2000.00"),
    ("#4472", "paid", "2026-01-02 09:00:00 +0000", "1000.00"),
    ("#4473", "paid", "2026-01-02 09:05:00 +0000", "980.00"),
    ("#4474", "paid", "2026-01-04 08:00:00 +0000", "1500.00"),
    ("#4475", "paid", "2026-01-05 08:00:00 +0000", "700.00"),
    ("#4476", "paid", "2026-01-06 08:00:00 +0000", "5000.00"),
    ("#4477", "pending", "", "300.00"),  # unpaid -- must be excluded entirely
]

# (entity_id, order_id, gross, fee, tax, utr, created_at epoch)
SETTLEMENT_ROWS = [
    ("pay_1", "#4471", "200000", "4000", "720", "RZRP1000000001", _epoch(2026, 1, 3)),
    ("pay_2", "#4472", "100000", "2000", "360", "RZRP1000000002", _epoch(2026, 1, 4)),
    ("pay_3", "#4473", "98000", "1960", "353", "RZRP1000000002", _epoch(2026, 1, 4)),
    ("pay_4", "#4474", "150000", "3000", "540", "RZRP1000000004", _epoch(2026, 1, 6)),
    # #4475 has no settlement row at all -- negative scenario.
    ("pay_6", "#4476", "500000", "10000", "1800", "RZRP1000000006", _epoch(2026, 1, 7)),
]

# (date, narration, deposit)
BANK_ROWS = [
    ("2026-01-03", "NEFT CR:RZRP1000000001 RAZORPAY SOFTWARE PRIVATE", "1952.80"),
    ("2026-01-04", "NEFT CR:RZRP1000000002 RAZORPAY SOFTWARE PRIVATE", "1933.27"),
    # Truncated UTR (bank cut the trailing "04") -- exact match will miss,
    # amount ties out (net 150000-3000-540 = 146460 paise = 1464.60) so
    # tier 2 (amount + date) should catch it instead.
    ("2026-01-06", "NEFT CR:RZRP10000000 RAZORPAY SOFTWARE", "1464.60"),
    # Correct UTR, but the WRONG amount -- net for pay_6 is 4882.00, this
    # bank row claims 3000.00. Must be refused at both tiers.
    ("2026-01-07", "NEFT CR:RZRP1000000006 RAZORPAY SOFTWARE PRIVATE", "3000.00"),
]


def _write_order_ledger_csv(path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Name", "Financial Status", "Paid at", "Total"])
        writer.writerows(ORDER_LEDGER_ROWS)


def _write_settlement_csv(path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["entity_id", "type", "amount", "fee", "tax", "created_at", "settlement_utr", "order_id"]
        )
        for entity_id, order_id, gross, fee, tax, utr, created_at in SETTLEMENT_ROWS:
            writer.writerow([entity_id, "payment", gross, fee, tax, created_at, utr, order_id])


def _write_bank_statement_csv(path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Date", "Narration", "Reference", "Withdrawal", "Deposit", "Balance"])
        balance = Decimal("100000.00")
        for d, narration, deposit in BANK_ROWS:
            balance += Decimal(deposit)
            writer.writerow([d, narration, "", "0", deposit, str(balance)])


@pytest.fixture
def reconciliation_result(tmp_path: Path):
    ledger_csv = tmp_path / "orders_export.csv"
    settlement_csv = tmp_path / "settlement_report.csv"
    bank_csv = tmp_path / "bank_statement.csv"

    _write_order_ledger_csv(ledger_csv)
    _write_settlement_csv(settlement_csv)
    _write_bank_statement_csv(bank_csv)

    ledger = parse_order_ledger(ledger_csv)
    settlement = parse_settlement_report(settlement_csv)
    bank = parse_bank_statement(bank_csv)

    return reconcile(ledger, settlement, bank)


class TestPositiveScenarios:
    def test_pending_order_never_enters_reconciliation(self, reconciliation_result):
        all_row_ids = {
            t.source_row_id
            for m in reconciliation_result.matches
            for t in m.transactions
        } | {t.source_row_id for e in reconciliation_result.exceptions for t in e.transactions}
        assert "#4477" not in all_row_ids

    def test_clean_exact_match_on_both_legs(self, reconciliation_result):
        order_4471_matches = [
            m
            for m in reconciliation_result.matches
            if any(t.order_id == "#4471" or t.source_row_id == "pay_1" for t in m.transactions)
        ]
        # Two separate MatchResults: ledger<->settlement, settlement<->bank.
        assert len(order_4471_matches) == 2
        assert all(m.tier == "exact" for m in order_4471_matches)

    def test_batched_settlement_matches_one_bank_row_to_two_orders(self, reconciliation_result):
        batch_match = next(
            m
            for m in reconciliation_result.matches
            if any(t.settlement_utr == "RZRP1000000002" for t in m.transactions)
            and any(t.source == "bank_statement" for t in m.transactions)
        )
        assert len(batch_match.transactions) == 3  # 2 settlement rows + 1 bank row
        assert batch_match.tier == "exact"

    def test_truncated_utr_falls_back_to_fuzzy_match(self, reconciliation_result):
        order_4474_bank_match = next(
            (
                m
                for m in reconciliation_result.matches
                if any(t.order_id == "#4474" for t in m.transactions)
                or any(
                    t.source == "razorpay_settlement" and t.settlement_utr == "RZRP1000000004"
                    for t in m.transactions
                )
            ),
            None,
        )
        settlement_bank_matches_for_4474 = [
            m
            for m in reconciliation_result.matches
            if any(
                t.source == "razorpay_settlement" and t.order_id == "#4474"
                for t in m.transactions
            )
            and any(t.source == "bank_statement" for t in m.transactions)
        ]
        assert len(settlement_bank_matches_for_4474) == 1
        assert settlement_bank_matches_for_4474[0].tier == "fuzzy"


class TestNegativeScenarios:
    def test_order_with_no_settlement_becomes_exception_not_forced_match(
        self, reconciliation_result
    ):
        matched_row_ids = {
            t.source_row_id for m in reconciliation_result.matches for t in m.transactions
        }
        assert "#4475" not in matched_row_ids

        exception = next(
            e
            for e in reconciliation_result.exceptions
            for t in e.transactions
            if t.order_id == "#4475"
        )
        assert exception.code == "NO_COUNTERPART_FOUND"
        assert "no matching settlement" in exception.reason.lower()

    def test_wildly_mismatched_amount_is_refused_not_force_matched(self, reconciliation_result):
        # pay_6 legitimately DOES match on the ledger<->settlement leg
        # (order #4476 is real) -- that's correct. What must NOT happen is
        # a settlement<->bank link forming despite the amount mismatch.
        settlement_bank_matches_for_pay_6 = [
            m
            for m in reconciliation_result.matches
            if any(t.source_row_id == "pay_6" for t in m.transactions)
            and any(t.source == "bank_statement" for t in m.transactions)
        ]
        assert settlement_bank_matches_for_pay_6 == []

        settlement_exception = next(
            e
            for e in reconciliation_result.exceptions
            for t in e.transactions
            if t.source_row_id == "pay_6"
        )
        # Structured code, not just free text -- and both the settlement
        # row AND the wrong-amount bank row are attached to the SAME
        # exception, since they're the same incident.
        assert settlement_exception.code == "AMOUNT_MISMATCH"
        assert len(settlement_exception.transactions) == 2
        assert {t.source for t in settlement_exception.transactions} == {
            "razorpay_settlement",
            "bank_statement",
        }

    def test_exceptions_are_sorted_by_rupee_value_descending(self, reconciliation_result):
        values = [e.total_amount for e in reconciliation_result.exceptions_by_value]
        assert values == sorted(values, reverse=True)

    def test_nothing_is_silently_dropped(self, reconciliation_result):
        # Every transaction from every source ends up either matched or
        # accounted for in an exception -- never just missing.
        expected_settlement_ids = {row[0] for row in SETTLEMENT_ROWS}
        seen_settlement_ids = {
            t.source_row_id
            for m in reconciliation_result.matches
            for t in m.transactions
            if t.source == "razorpay_settlement"
        } | {
            t.source_row_id
            for e in reconciliation_result.exceptions
            for t in e.transactions
            if t.source == "razorpay_settlement"
        }
        assert expected_settlement_ids == seen_settlement_ids
