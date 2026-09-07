"""Tests for the PayU Settlement Detail Range API parser.

The JSON fixtures below use field names and value shapes taken directly
from PayU's documented Settlement Detail Range API response (see the
parser's module docstring for the source) -- constructed fixtures for
testing parser logic, not a stand-in for real business data.
"""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.parsers.payu_settlement import parse_payu_settlement


def _write_json(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _batch(**overrides) -> dict:
    batch = {
        "settlementId": "12127298202508260245",
        "settlementCompletedDate": "2025-08-26 02:51:22.000000",
        "settlementAmount": "2467.13",
        "utrNumber": "523871332950",
        "adjustmentAmount": "0.0",
        "refundAmount": "0.0",
        "chargebackAmount": "0.0",
        "refundReversalAmount": "0.0",
        "chargebackReversalAmount": "0.0",
        "numberOfTransactions": 1,
        "transaction": [
            {
                "payuId": "24868774786",
                "merchantTransactionId": "order_5001",
                "transactionAmount": "2480.0",
                "merchantNetAmount": "2467.13",
                "transactionDate": "2025-08-26 02:14:35.000000",
                "action": "capture",
                "transactionCurrency": "INR",
            }
        ],
    }
    batch.update(overrides)
    return batch


@pytest.fixture
def clean_settlement_json(tmp_path: Path) -> Path:
    return _write_json(tmp_path / "payu_settlement.json", {"result": {"data": [_batch()]}})


def test_parses_the_capture_transaction(clean_settlement_json: Path):
    transactions = parse_payu_settlement(clean_settlement_json)
    assert len(transactions) == 1
    txn = transactions[0]
    assert txn.source_row_id == "24868774786"
    assert txn.amount == Decimal("2467.13")
    assert str(txn.date) == "2025-08-26"


def test_order_id_and_utr_are_linked(clean_settlement_json: Path):
    transactions = parse_payu_settlement(clean_settlement_json)
    txn = transactions[0]
    assert txn.order_id == "order_5001"
    assert txn.settlement_utr == "523871332950"


def test_no_adjustment_row_when_batch_fully_attributes(clean_settlement_json: Path):
    # settlementAmount (2467.13) exactly equals the one transaction's
    # merchantNetAmount -- nothing unattributed, so no synthetic row.
    transactions = parse_payu_settlement(clean_settlement_json)
    assert len(transactions) == 1


def test_adjustment_row_appears_when_batch_total_does_not_fully_attribute(tmp_path: Path):
    # Real PayU example shape: settlementAmount = merchantNetAmount + adjustmentAmount.
    batch = _batch(settlementAmount="1479.82", adjustmentAmount="-987.31")
    path = _write_json(tmp_path / "payu_with_adjustment.json", {"result": {"data": [batch]}})

    transactions = parse_payu_settlement(path)
    assert len(transactions) == 2

    adjustment = next(t for t in transactions if t.order_id is None)
    assert adjustment.amount == Decimal("-987.31")
    assert adjustment.settlement_utr == "523871332950"
    assert "unattributed" in adjustment.description.lower()


def test_adjustment_row_amount_is_a_residual_not_hardcoded(tmp_path: Path):
    # A different, made-up-but-consistent pair of numbers -- proves the
    # adjustment is computed (settlementAmount - transactions total), not
    # just echoing adjustmentAmount verbatim.
    batch = _batch(settlementAmount="2400.00", adjustmentAmount="-999.00")
    path = _write_json(tmp_path / "payu_residual.json", {"result": {"data": [batch]}})

    transactions = parse_payu_settlement(path)
    adjustment = next(t for t in transactions if t.order_id is None)
    # 2400.00 - 2467.13 = -67.13, NOT -999.00 (the unused adjustmentAmount field).
    assert adjustment.amount == Decimal("-67.13")


def test_raw_preserves_original_transaction_fields(clean_settlement_json: Path):
    transactions = parse_payu_settlement(clean_settlement_json)
    assert transactions[0].raw["action"] == "capture"
    assert transactions[0].raw["transactionAmount"] == "2480.0"


def test_missing_result_data_structure_raises(tmp_path: Path):
    path = _write_json(tmp_path / "bad_top_level.json", {"status": 0})
    with pytest.raises(ValueError, match="result.data"):
        parse_payu_settlement(path)


def test_missing_batch_key_raises(tmp_path: Path):
    batch = _batch()
    del batch["utrNumber"]
    path = _write_json(tmp_path / "bad_batch.json", {"result": {"data": [batch]}})
    with pytest.raises(ValueError, match="missing expected keys"):
        parse_payu_settlement(path)


def test_missing_transaction_key_raises(tmp_path: Path):
    batch = _batch()
    del batch["transaction"][0]["merchantNetAmount"]
    path = _write_json(tmp_path / "bad_txn.json", {"result": {"data": [batch]}})
    with pytest.raises(ValueError, match="missing expected keys"):
        parse_payu_settlement(path)
