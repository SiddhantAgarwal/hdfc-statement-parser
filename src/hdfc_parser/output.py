"""Stage 6: write transactions.json, transactions.csv and summary.json."""

import csv
import json
from collections import Counter
from pathlib import Path

from hdfc_parser.validate import ValidationReport

CSV_FIELDS = [
    "txn_date",
    "txn_time",
    "post_date",
    "description",
    "amount",
    "cr_dr",
    "rewards",
    "category",
    "category_confidence",
    "is_recurring",
    "is_emi",
    "review",
    "error",
]


def write_json(transactions: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(transactions, f, indent=2, ensure_ascii=False)


def write_csv(transactions: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for txn in transactions:
            writer.writerow(
                {k: txn.get(k, "") for k in CSV_FIELDS}
            )


def build_summary(transactions: list[dict], report: ValidationReport) -> dict:
    """Aggregate per-category totals, top merchants, recurring charges."""
    by_category: dict[str, float] = {}
    merchants: Counter = Counter()
    recurring = []
    for txn in transactions:
        cat = txn.get("category") or "unclassified"
        amount = txn.get("amount") or 0.0
        by_category[cat] = round(by_category.get(cat, 0.0) + amount, 2)
        merchants[txn.get("description", "")] += 1
        if txn.get("is_recurring"):
            recurring.append(
                {
                    "description": txn.get("description"),
                    "amount": amount,
                    "txn_date": txn.get("txn_date"),
                }
            )

    debits = round(sum(t["amount"] for t in transactions if t.get("cr_dr") == "DR"), 2)
    credits = round(sum(t["amount"] for t in transactions if t.get("cr_dr") == "CR"), 2)
    review = [t.get("description") for t in transactions if t.get("review")]

    return {
        "total_transactions": len(transactions),
        "total_debits": debits,
        "total_credits": credits,
        "by_category": by_category,
        "top_merchants": merchants.most_common(10),
        "recurring_charges": recurring,
        "needs_review": review,
        "validation": {
            "ok": report.ok,
            "errors": report.errors,
            "warnings": report.warnings,
        },
    }


def write_summary(summary: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)


def print_console_summary(summary: dict, out_json: Path, out_csv: Path) -> None:
    print(f"\nParsed {summary['total_transactions']} transactions "
          f"(debits {summary['total_debits']:.2f}, "
          f"credits {summary['total_credits']:.2f})")
    print("\nBy category:")
    for cat, total in sorted(
        summary["by_category"].items(), key=lambda kv: -kv[1]
    ):
        print(f"  {cat:<16} {total:>10.2f}")
    if summary["needs_review"]:
        print(f"\n{len(summary['needs_review'])} transaction(s) need review:")
        for desc in summary["needs_review"]:
            print(f"  - {desc!r}")
    v = summary["validation"]
    if not v["ok"]:
        print("\nVALIDATION ERRORS:")
        for err in v["errors"]:
            print(f"  ! {err}")
    for warn in v["warnings"]:
        print(f"  ~ {warn}")
    print(f"\nWrote {out_json}, {out_csv}, and summary beside them.")
