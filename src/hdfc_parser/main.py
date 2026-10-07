#!/usr/bin/env python3
"""
Parse a redacted HDFC credit card statement PDF into structured,
Clef-classified transactions.

Usage:
    uv run hdfc-parser <redacted.pdf> [-o out.json] [--csv out.csv]
                                  [--summary out_summary.json]
                                  [--no-classify] [--quiet]

Pipeline (see PLAN.md) - each function below owns one stage; the stage
number lives in the comment banner above the function and in main(),
never in the function's name:
    Stage 0: safety_check        - refuse encrypted or leaky input
    Stage 1-2: extract           - word extraction + row reconstruction
    Stage 3: adjudicate          - Clef KEEP/DROP on ambiguous rows
    Stage 4: classify            - Clef category per transaction
    Stage 5: validate            - deterministic checks + reconciliation
    Stage 6: write_output        - JSON/CSV/summary files
"""

import argparse
import sys
from pathlib import Path

import pymupdf

from hdfc_parser import classify, output, validate
from hdfc_parser.config_loader import (
    load_category_descriptions,
    load_config,
    require_categories,
)
from hdfc_parser.extract import (
    ExtractedStatement,
    RawRow,
    check_input_is_safe,
    extract_statement_from_doc,
    parse_row_fields,
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Parse a redacted HDFC credit card statement PDF."
    )
    p.add_argument("pdf", help="redacted, unencrypted statement PDF")
    p.add_argument("-o", "--out", default=None,
                   help="output JSON path (default: <pdf>.parsed.json)")
    p.add_argument("--csv", default=None,
                   help="output CSV path (default: <pdf>.parsed.csv)")
    p.add_argument("--summary", default=None,
                   help="summary JSON path (default: <pdf>_summary.json)")
    p.add_argument("--no-classify", action="store_true",
                   help="extract and validate only; skip Clef (no Ollama)")
    p.add_argument("--quiet", action="store_true",
                   help="suppress per-transaction progress output")
    return p


def _make_client(config: dict) -> classify.ClefClient:
    """Build a Clef client from the loaded config."""
    return classify.ClefClient(
        base_url=config["ollama_base_url"],
        model=config["model"],
        timeout_s=config["request_timeout_s"],
        keep_alive=config["keep_alive"],
    )


def _txn_dict(row: RawRow, **extra: object) -> dict:
    """Convert a RawRow into the transaction dict used downstream."""
    txn = {
        "txn_date": row.txn_date,
        "txn_time": row.txn_time,
        "post_date": row.post_date,
        "description": row.description,
        "amount": row.amount,
        "cr_dr": row.cr_dr,
        "rewards": row.rewards,
    }
    txn.update(extra)
    return txn


# ── Stage 0: refuse encrypted or leaky input ─────────────────────────────


def safety_check(pdf_path: Path) -> None:
    """Exit if the PDF is encrypted or configured personal data survived
    redaction."""
    print("Stage 0: input safety check…")
    leaks = check_input_is_safe(str(pdf_path))
    if leaks:
        print("REFUSING to parse - personal data detected in input:")
        for leak in leaks:
            print(f"  - {leak}")
        print("Re-run hdfc-statement-redactor on this statement first.")
        sys.exit(1)
    print("  clean: no configured personal data found.\n")


# ── Stages 1-2: extraction + column-aware row reconstruction ─────────────


def extract(pdf_path: Path) -> tuple[ExtractedStatement, list[dict]]:
    """Extract the statement and build transaction dicts from its rows.

    Returns (ExtractedStatement, transactions); exits if no transaction
    candidates were found.
    """
    print("Stage 1-2: extracting rows…")
    doc = pymupdf.open(pdf_path)
    extracted = extract_statement_from_doc(doc)
    doc.close()

    if extracted.template:
        print(f"  template: {extracted.template}")
    else:
        print(
            "  WARNING: no statement template recognised "
            f"(anchors missing: {extracted.anchors_missing}); "
            "rows may be wrong. Continuing, but verify output."
        )

    transactions = []
    for row in extracted.rows:
        if row.source == "fallback":
            row = parse_row_fields(row)
        transactions.append(_txn_dict(row))
    print(
        f"  {len(transactions)} transaction candidates, "
        f"{len(extracted.rejected)} noise rows rejected, "
        f"{len(extracted.ambiguous)} ambiguous.\n"
    )

    if not transactions:
        sys.exit(
            "ERROR: no transaction candidates found - layout not "
            "recognised. See warnings above."
        )
    return extracted, transactions


# ── Stage 3: Clef adjudication of ambiguous rows ─────────────────────────


def adjudicate(
    extracted: ExtractedStatement,
    transactions: list[dict],
    config: dict,
    no_classify: bool,
) -> classify.ClefClient | None:
    """Ask Clef to KEEP/DROP each ambiguous row the deterministic filters
    couldn't decide. Returns the open client for Stage 4 to reuse, or None
    if no adjudication was needed."""
    if not extracted.ambiguous or no_classify:
        return None

    print(f"Stage 3: adjudicating {len(extracted.ambiguous)} "
          "ambiguous rows with Clef…")
    client = _make_client(config)
    try:
        for row in extracted.ambiguous:
            is_txn, prob = classify.adjudicate_row(client, row.text)
            print(f"    {'KEEP' if is_txn else 'DROP'} "
                  f"(p={prob:.2f}): {row.text[:60]!r}")
            if is_txn:
                transactions.append(
                    _txn_dict(parse_row_fields(row), adjudicated=True)
                )
    except Exception as exc:
        sys.exit(
            f"ERROR: could not reach Ollama at "
            f"{config['ollama_base_url']} ({exc}).\n"
            f"Start it (ollama serve) and pull the model "
            f"(ollama pull {config['model']}), or re-run with "
            f"--no-classify."
        )
    print()
    return client


# ── Stage 5: deterministic validation ────────────────────────────────────


def validate_statement(
    extracted: ExtractedStatement, transactions: list[dict]
) -> validate.ValidationReport:
    """Structural checks plus reconciliation against the printed totals."""
    print("Stage 5: validating…")
    period = validate.parse_period(extracted.full_text)
    totals = validate.parse_summary_totals(extracted.full_text)
    report = validate.ValidationReport()
    for sub in (
        validate.validate_transactions(transactions),
        validate.check_dates_in_period(transactions, period),
        validate.reconcile_totals(
            transactions,
            totals[0] if totals else None,
            totals[1] if totals else None,
        ),
    ):
        report.errors.extend(sub.errors)
        report.warnings.extend(sub.warnings)
    print()
    return report


# ── Stage 4: Clef classification ─────────────────────────────────────────


def classify_transactions(
    transactions: list[dict],
    config: dict,
    category_descriptions: dict[str, str],
    no_classify: bool,
    quiet: bool,
    client: classify.ClefClient | None,
) -> None:
    """Fill in category/recurring/EMI decisions in place; with
    --no-classify, mark everything for review instead."""
    if no_classify:
        print("--no-classify: skipping Clef (categories left empty).")
        for txn in transactions:
            txn["category"] = None
            txn["review"] = True
        return

    if client is None:
        client = _make_client(config)
    print(
        f"Stage 4: classifying with Clef "
        f"(model={config['model']}, {len(transactions)} txns)…"
    )
    try:
        classify.classify_all(
            client,
            transactions,
            category_descriptions,
            confidence_threshold=config["confidence_threshold"],
            progress=not quiet,
        )
    finally:
        client.close()
    print()


# ── Stage 6: JSON/CSV/summary output ─────────────────────────────────────


def write_output(
    transactions: list[dict],
    report: validate.ValidationReport,
    pdf_path: Path,
    args: argparse.Namespace,
) -> None:
    """Write <stem>.parsed.json/.parsed.csv/_summary.json beside the PDF
    (or wherever the CLI flags point) and print the console summary."""
    out_json = (
        Path(args.out) if args.out else pdf_path.with_suffix(".parsed.json")
    )
    out_csv = Path(args.csv) if args.csv else pdf_path.with_suffix(".parsed.csv")
    out_summary = (
        Path(args.summary)
        if args.summary
        else pdf_path.with_name(pdf_path.stem + "_summary.json")
    )

    summary = output.build_summary(transactions, report)
    output.write_json(transactions, out_json)
    output.write_csv(transactions, out_csv)
    output.write_summary(summary, out_summary)
    output.print_console_summary(summary, out_json, out_csv)


# ── orchestration ────────────────────────────────────────────────────────
# Each call below is one pipeline stage; the comment marks its stage
# number (the functions themselves don't encode it in their names).


def main() -> None:
    args = build_parser().parse_args()
    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        sys.exit(f"ERROR: {pdf_path} does not exist")

    config = load_config()
    category_descriptions = load_category_descriptions()
    require_categories(config, category_descriptions)  # validates selection

    safety_check(pdf_path)                                   # Stage 0
    extracted, transactions = extract(pdf_path)             # Stages 1-2
    client = adjudicate(extracted, transactions,             # Stage 3
                        config, args.no_classify)
    report = validate_statement(extracted, transactions)    # Stage 5
    classify_transactions(transactions, config,             # Stage 4
                         category_descriptions,
                         args.no_classify, args.quiet, client)
    write_output(transactions, report, pdf_path, args)       # Stage 6

    if not report.ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
