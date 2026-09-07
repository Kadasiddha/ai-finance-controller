"""Parser for PayU's Settlement Detail Range API response.

Schema is real, not guessed: taken from PayU's documented Settlement
Detail Range API (docs.payu.in/reference/settlement-detail-range-api),
confirmed against a full real example response. PayU's dashboard also
supports a CSV/XLSX export of settlement records
(docs.payu.in/docs/export-the-settlement-records), but that page doesn't
enumerate the exported file's actual column names -- building against it
would mean guessing column names, not grounding against them. This
parser is built against the JSON API response instead, which is fully
confirmed with real field names and a real example.

This is the first non-CSV source in the project -- a real structural
difference from Razorpay/Stripe, not a stylistic one. A PayU settlement
"batch" is a nested JSON object:

    {"settlementId": ..., "settlementAmount": ..., "utrNumber": ...,
     "adjustmentAmount": ..., "refundAmount": ..., "chargebackAmount": ...,
     "transaction": [{"payuId": ..., "merchantTransactionId": ...,
                       "merchantNetAmount": ..., "transactionDate": ...,
                       "action": "capture", ...}, ...]}

Two real findings from the confirmed sample, both consequential:

- Refunds/chargebacks are BATCH-LEVEL AGGREGATES, not per-transaction
  rows. Confirmed by the math in PayU's own example:
  settlementAmount (1479.82) == merchantNetAmount (2467.13)
  + adjustmentAmount (-987.31), exactly. The `transaction` array only
  ever contained the one real "capture" row in the confirmed sample --
  there's no per-transaction refund record with its own
  merchantTransactionId to link a refund back to a specific order. If
  this parser only emitted `transaction[]` rows, a batch's total would
  silently be short by its refunds/chargebacks/adjustments, and
  amounts_reconcile would wrongly reject an otherwise-correct batch as
  AMOUNT_MISMATCH. To keep the batch total honest, one extra synthetic
  "unattributed adjustment" Transaction is emitted per batch (only when
  non-zero), computed as a residual --
  settlementAmount - sum(merchantNetAmount for its transactions) --
  rather than decomposing it into refundAmount/chargebackAmount/...
  individually, whose sign conventions aren't confirmed by the one real
  sample (all zero there). This row carries order_id=None: it's real
  money, just not traceable to a specific order given what PayU's API
  actually exposes.

- `utrNumber` is a bare numeric string ("523871332950"), not prefixed
  like Razorpay's real `RZRP...` convention, and there's no confirmed
  example of how a PayU UTR appears embedded in a real bank narration.
  bank_statement.py's extract_settlement_utr is deliberately left
  untouched (same call already made for Stripe's trace_id, for a
  different underlying reason -- see that parser's docstring) --
  guessing at a bare-numeric narration pattern risks false-positive
  matches. PayU's settlement<->bank leg realistically falls through to
  tier 2 (fuzzy) here as a result.
"""

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from app.models import Transaction

_REQUIRED_BATCH_KEYS = {"settlementId", "settlementAmount", "utrNumber", "transaction"}
_REQUIRED_TXN_KEYS = {"payuId", "merchantTransactionId", "merchantNetAmount", "transactionDate", "action"}


def _parse_timestamp(value: str) -> datetime:
    # PayU's confirmed real format: "2025-08-26 02:14:35.000000"
    return datetime.strptime(value.strip(), "%Y-%m-%d %H:%M:%S.%f")


def parse_payu_settlement(path: str | Path) -> list[Transaction]:
    """Parse a saved PayU Settlement Detail Range API response (JSON) into
    normalized Transactions. Every transaction row is included regardless
    of `action` -- the matching tiers decide what to do with it, same
    philosophy as the CSV-based parsers.
    """
    path = Path(path)

    with path.open(encoding="utf-8") as f:
        payload = json.load(f)

    try:
        batches = payload["result"]["data"]
    except (KeyError, TypeError) as exc:
        raise ValueError(
            f"PayU settlement response is missing the expected result.data structure. "
            f"Found top-level keys: {sorted(payload) if isinstance(payload, dict) else type(payload)}"
        ) from exc

    transactions: list[Transaction] = []

    for batch in batches:
        missing_batch_keys = _REQUIRED_BATCH_KEYS - set(batch)
        if missing_batch_keys:
            raise ValueError(
                f"PayU settlement batch is missing expected keys: {sorted(missing_batch_keys)}. "
                f"Found: {sorted(batch)}"
            )

        utr = batch["utrNumber"] or None
        transactions_net_total = Decimal("0")

        for txn in batch["transaction"]:
            missing_txn_keys = _REQUIRED_TXN_KEYS - set(txn)
            if missing_txn_keys:
                raise ValueError(
                    f"PayU settlement transaction is missing expected keys: {sorted(missing_txn_keys)}. "
                    f"Found: {sorted(txn)}"
                )

            net_amount = Decimal(txn["merchantNetAmount"])
            transactions_net_total += net_amount

            transactions.append(
                Transaction(
                    source="payu_settlement",
                    source_row_id=txn["payuId"],
                    amount=net_amount,
                    date=_parse_timestamp(txn["transactionDate"]).date(),
                    order_id=txn["merchantTransactionId"] or None,
                    settlement_utr=utr,
                    description=txn["action"],
                    raw=dict(txn),
                )
            )

        batch_total = Decimal(batch["settlementAmount"])
        unattributed_adjustment = batch_total - transactions_net_total
        if unattributed_adjustment != 0:
            transactions.append(
                Transaction(
                    source="payu_settlement",
                    source_row_id=f"{batch['settlementId']}:adjustment",
                    amount=unattributed_adjustment,
                    date=_parse_timestamp(batch["settlementCompletedDate"]).date(),
                    order_id=None,
                    settlement_utr=utr,
                    description="Unattributed batch-level adjustment (refund/chargeback/other, not traceable to one order)",
                    raw=dict(batch, transaction="<omitted, see individual rows>"),
                )
            )

    return transactions
