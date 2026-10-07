"""Stage 5: deterministic validation - no LLM, pure arithmetic.

Reconciles parsed rows against statement totals; anything that doesn't add
up is reported loudly, never silently dropped.
"""

import re
from dataclasses import dataclass, field

from hdfc_parser.extract import DATE_RE

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_DASH = r"(?:[-\u2013]|to\b)"
# "Statement Period: 01/08/26 - 31/08/26"
NUM_PERIOD_RE = re.compile(
    rf"(\d{{2}}/\d{{2}}/\d{{2,4}})\s*{_DASH}\s*(\d{{2}}/\d{{2}}/\d{{2,4}})"
)
# "Billing Period 21 Aug, 2026 - 20 Sep, 2026" (Infinia template)
NAME_PERIOD_RE = re.compile(
    rf"(\d{{1,2}})\s+([A-Za-z]{{3}})[a-z]*,?\s+(\d{{4}})\s*{_DASH}\s*"
    rf"(\d{{1,2}})\s+([A-Za-z]{{3}})[a-z]*,?\s+(\d{{4}})"
)
# summary band: PREVIOUS STATEMENT DUES / PAYMENTS/CREDITS / PURCHASES/DEBIT
# / FINANCE CHARGES, values printed in that order as C 1,35,371.99 etc.
SUMMARY_TOTALS_RE = re.compile(
    r"PREVIOUS STATEMENT DUES[\s\S]*?C\s?([\d,]+\.\d{2})[\s\S]*?C\s?([\d,]+\.\d{2})"
    r"[\s\S]*?C\s?([\d,]+\.\d{2})[\s\S]*?C\s?([\d,]+\.\d{2})"
)


@dataclass
class ValidationReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def add_error(self, msg: str) -> None:
        self.errors.append(msg)

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)


def _numeric_to_iso(d: str) -> str:
    """Normalise DD/MM/YY or DD/MM/YYYY to ISO for comparison."""
    day, month, year = d.split("/")
    if len(year) == 2:
        year = "20" + year
    return f"{year}-{month.rjust(2, '0')}-{day.rjust(2, '0')}"


def parse_period(text: str) -> tuple[str, str] | None:
    """Extract (start_iso, end_iso) from either period format."""
    m = NUM_PERIOD_RE.search(text)
    if m:
        return _numeric_to_iso(m.group(1)), _numeric_to_iso(m.group(2))
    m = NAME_PERIOD_RE.search(text)
    if m:
        d1, mon1, y1, d2, mon2, y2 = m.groups()
        m1, m2 = MONTHS.get(mon1.lower()), MONTHS.get(mon2.lower())
        if m1 and m2:
            return (
                f"{y1}-{m1:02d}-{int(d1):02d}",
                f"{y2}-{m2:02d}-{int(d2):02d}",
            )
    return None


def parse_summary_totals(text: str) -> tuple[float, float] | None:
    """Parse (debits, credits) from the statement summary band.

    The Infinia header prints four values after "PREVIOUS STATEMENT
    DUES": previous dues, payments/credits, purchases/debit, finance
    charges. Returns (purchases_debit, payments_credits) or None.
    """
    m = SUMMARY_TOTALS_RE.search(text)
    if not m:
        return None
    _prev, credits, debits, _finance = (
        float(g.replace(",", "")) for g in m.groups()
    )
    return debits, credits


def validate_transactions(transactions: list[dict]) -> ValidationReport:
    """Sanity-check parsed transactions: amounts, dates, duplicates."""
    report = ValidationReport()

    for i, txn in enumerate(transactions, start=1):
        if txn.get("amount") is None:
            report.add_error(f"txn {i}: missing amount - {txn.get('description')!r}")
        if txn.get("amount") is not None and txn["amount"] <= 0:
            report.add_warning(f"txn {i}: non-positive amount {txn['amount']}")
        if not txn.get("txn_date") or not DATE_RE.match(txn.get("txn_date", "")):
            report.add_error(f"txn {i}: missing/invalid txn_date")

    seen: dict[tuple, int] = {}
    for i, txn in enumerate(transactions, start=1):
        key = (txn.get("txn_date"), txn.get("description"), txn.get("amount"))
        if key in seen:
            report.add_warning(
                f"possible duplicate: txn {i} matches txn {seen[key]} "
                f"({txn.get('txn_date')}, {txn.get('description')!r}, "
                f"{txn.get('amount')})"
            )
        else:
            seen[key] = i

    return report


def reconcile_totals(
    transactions: list[dict],
    expected_debits: float | None,
    expected_credits: float | None = None,
    tolerance: float = 0.01,
) -> ValidationReport:
    """Reconcile debit/credit sums against the statement's printed totals."""
    report = ValidationReport()
    debits = round(sum(t["amount"] for t in transactions if t.get("cr_dr") == "DR"), 2)
    credits = round(sum(t["amount"] for t in transactions if t.get("cr_dr") == "CR"), 2)

    if expected_debits is not None:
        diff = round(abs(debits - expected_debits), 2)
        if diff > tolerance:
            report.add_error(
                f"debit total mismatch: parsed {debits:.2f} vs statement "
                f"{expected_debits:.2f} (diff {diff:.2f})"
            )
    if expected_credits is not None:
        diff = round(abs(credits - expected_credits), 2)
        if diff > tolerance:
            report.add_error(
                f"credit total mismatch: parsed {credits:.2f} vs statement "
                f"{expected_credits:.2f} (diff {diff:.2f})"
            )
    if expected_debits is None and expected_credits is None:
        report.add_warning("no statement totals found - skipped reconciliation")
    return report


def check_dates_in_period(
    transactions: list[dict], period: tuple[str, str] | None
) -> ValidationReport:
    """Check transaction dates against the billing period.

    Out-of-period dates are warnings, not errors: statements legitimately
    carry rows dated before the period opens (e.g. GST from the previous
    cycle is billed in the subsequent statement).
    """
    report = ValidationReport()
    if period is None:
        report.add_warning("statement period not found - skipped date check")
        return report

    start, end = period
    for i, txn in enumerate(transactions, start=1):
        d = txn.get("txn_date")
        if d and not (start <= _numeric_to_iso(d) <= end):
            report.add_warning(
                f"txn {i}: date {d} outside statement period "
                f"{start} to {end}"
            )
    return report
