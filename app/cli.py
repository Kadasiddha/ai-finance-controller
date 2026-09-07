"""Minimal command-line entry point -- run a reconciliation without
writing Python.

Each known source is its own explicit flag (`--razorpay-settlement`,
`--stripe-settlement`, ...) rather than one generic `--file` flag with
auto-detected gateway type -- guessing a file's gateway from its shape
would be exactly the kind of invented behavior this project's own
principles reject (see app/reconcile.py's KNOWN_LEGS docstring). The
flags map 1:1 to `Source`.

Usage:
    python -m app.cli --order-ledger orders.csv \\
        --razorpay-settlement settlement.csv \\
        --bank-statement bank.csv \\
        --report reconciliation_report.csv --summary
"""

import argparse
import sys
from datetime import date
from pathlib import Path

from app.analytics import summarize
from app.filters import filter_by_date_range
from app.matching.adjudicate import adjudicate
from app.models import Source, Transaction
from app.parsers.bank_statement import parse_bank_statement
from app.parsers.order_ledger import parse_order_ledger
from app.parsers.payu_settlement import parse_payu_settlement
from app.parsers.razorpay_settlement import parse_settlement_report
from app.parsers.stripe_settlement import parse_stripe_settlement
from app.reconcile import reconcile
from app.report import write_csv_report

_SOURCE_LOADERS: dict[Source, tuple[str, str]] = {
    "order_ledger": ("--order-ledger", "Order ledger CSV"),
    "razorpay_settlement": ("--razorpay-settlement", "Razorpay settlement report CSV"),
    "stripe_settlement": ("--stripe-settlement", "Stripe itemized payout reconciliation CSV"),
    "payu_settlement": ("--payu-settlement", "PayU Settlement Detail Range API response (JSON)"),
    "bank_statement": ("--bank-statement", "Bank statement CSV"),
}

_LOADER_FUNCS = {
    "order_ledger": parse_order_ledger,
    "razorpay_settlement": parse_settlement_report,
    "stripe_settlement": parse_stripe_settlement,
    "payu_settlement": parse_payu_settlement,
    "bank_statement": parse_bank_statement,
}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli",
        description=(
            "Reconcile an order ledger against one or more gateway settlement "
            "reports and a bank statement. Pass any 2 or more sources; only "
            "the comparisons that make sense for what you select actually run."
        ),
    )
    for source_name, (flag, help_text) in _SOURCE_LOADERS.items():
        parser.add_argument(flag, dest=source_name, type=Path, metavar="PATH", help=help_text)

    parser.add_argument("--start-date", metavar="YYYY-MM-DD", help="Only reconcile transactions on/after this date")
    parser.add_argument("--end-date", metavar="YYYY-MM-DD", help="Only reconcile transactions on/before this date")
    parser.add_argument("--report", type=Path, metavar="PATH", help="Write the CSV reconciliation report to this path")
    parser.add_argument("--summary", action="store_true", help="Print the analytics summary")
    parser.add_argument("--verbose", action="store_true", help="Print every match and exception, not just counts")
    parser.add_argument(
        "--llm",
        action="store_true",
        help=(
            "Opt into tier 3 (LLM adjudication), off by default. Requires a "
            "locally running Ollama server with qwen2.5:7b-instruct pulled -- "
            "if it isn't reachable, tier 3 contributes nothing rather than "
            "failing the run."
        ),
    )
    return parser


def _load_sources(args: argparse.Namespace) -> dict[Source, list[Transaction]]:
    sources: dict[Source, list[Transaction]] = {}
    for source_name, loader in _LOADER_FUNCS.items():
        path = getattr(args, source_name)
        if path is not None:
            sources[source_name] = loader(path)
    return sources


def _parse_date(value: str | None, flag: str) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise SystemExit(f"error: {flag} must be YYYY-MM-DD, got {value!r}")


def _print_verbose(result) -> None:
    for m in result.matches:
        ids = ", ".join(f"{t.source}:{t.source_row_id}" for t in m.transactions)
        print(f"  MATCH [{m.tier}] {ids}")
        print(f"    {m.reasoning}")
    for e in result.exceptions_by_value:
        ids = ", ".join(f"{t.source}:{t.source_row_id}" for t in e.transactions)
        print(f"  EXCEPTION [{e.code}] value={e.total_amount} {ids}")
        print(f"    {e.reason}")


def _print_summary(result) -> None:
    summary = summarize(result)
    rate = summary.match_rate_by_transaction_count
    print()
    print("Analytics summary")
    print(f"  matched_transaction_count: {summary.matched_transaction_count}")
    print(f"  exception_transaction_count: {summary.exception_transaction_count}")
    print(f"  match_rate_by_transaction_count: {rate:.3f}" if rate is not None else "  match_rate_by_transaction_count: N/A")
    print(f"  match_count_by_tier: {dict(summary.match_count_by_tier)}")
    print(f"  exception_count_by_code: {dict(summary.exception_count_by_code)}")
    print(f"  total_exception_value: {summary.total_exception_value}")


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    try:
        sources = _load_sources(args)
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    start = _parse_date(args.start_date, "--start-date")
    end = _parse_date(args.end_date, "--end-date")
    if start is not None or end is not None:
        sources = filter_by_date_range(sources, start=start, end=end)

    try:
        result = reconcile(sources, adjudicate_fn=adjudicate if args.llm else None)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"{len(result.matches)} matches, {len(result.exceptions)} exceptions")

    if args.verbose:
        _print_verbose(result)

    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        write_csv_report(result, args.report)
        print(f"report written to {args.report}")

    if args.summary:
        _print_summary(result)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
