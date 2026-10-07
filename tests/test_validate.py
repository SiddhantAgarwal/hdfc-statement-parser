"""Unit tests for validate.py — all deterministic, no Ollama needed."""

from hdfc_parser.validate import (
    check_dates_in_period,
    parse_period,
    reconcile_totals,
    validate_transactions,
)


class TestPeriod:
    def test_parses_period(self):
        # parse_period normalises to ISO for cross-format comparison
        assert parse_period("Statement Period: 01/08/26 - 31/08/26") == (
            "2026-08-01",
            "2026-08-31",
        )

    def test_parses_named_period(self):
        # Infinia format: "Billing Period 21 Aug, 2026 - 20 Sep, 2026"
        assert parse_period("Billing Period 21 Aug, 2026 - 20 Sep, 2026") == (
            "2026-08-21",
            "2026-09-20",
        )

    def test_no_period(self):
        assert parse_period("no dates here") is None


class TestValidateTransactions:
    def test_clean_rows(self):
        txns = [
            {"txn_date": "03/08/26", "description": "SWIGGY", "amount": 432.50},
        ]
        report = validate_transactions(txns)
        assert report.ok
        assert report.warnings == []

    def test_missing_amount_is_error(self):
        txns = [{"txn_date": "03/08/26", "description": "X", "amount": None}]
        report = validate_transactions(txns)
        assert not report.ok
        assert any("missing amount" in e for e in report.errors)

    def test_bad_date_is_error(self):
        txns = [{"txn_date": "garbage", "description": "X", "amount": 1.00}]
        report = validate_transactions(txns)
        assert not report.ok

    def test_duplicate_is_warning(self):
        txns = [
            {"txn_date": "03/08/26", "description": "SWIGGY", "amount": 432.50},
            {"txn_date": "03/08/26", "description": "SWIGGY", "amount": 432.50},
        ]
        report = validate_transactions(txns)
        assert report.ok  # duplicates warn, don't fail
        assert any("duplicate" in w for w in report.warnings)


class TestReconcile:
    def test_matching_total(self):
        txns = [
            {"amount": 432.50, "cr_dr": "DR"},
            {"amount": 100.00, "cr_dr": "DR"},
        ]
        report = reconcile_totals(txns, expected_debits=532.50)
        assert report.ok

    def test_mismatched_total_is_error(self):
        txns = [{"amount": 432.50, "cr_dr": "DR"}]
        report = reconcile_totals(txns, expected_debits=999.99)
        assert not report.ok
        assert any("debit total mismatch" in e for e in report.errors)

    def test_no_totals_warns(self):
        report = reconcile_totals([{"amount": 1.0, "cr_dr": "DR"}], None, None)
        assert report.ok
        assert any("skipped reconciliation" in w for w in report.warnings)


class TestDatesInPeriod:
    def test_inside(self):
        txns = [{"txn_date": "15/08/26", "amount": 1.0}]
        report = check_dates_in_period(txns, ("01/08/26", "31/08/26"))
        assert report.ok

    def test_outside_is_warning(self):
        # out-of-period dates are warnings by design (legit carry-over
        # rows like previous-cycle GST)
        txns = [{"txn_date": "15/07/26", "amount": 1.0}]
        report = check_dates_in_period(txns, ("2026-08-01", "2026-08-31"))
        assert report.ok  # no errors
        assert any("outside statement period" in w for w in report.warnings)

    def test_two_digit_vs_four_digit_years_compare(self):
        txns = [{"txn_date": "15/08/2026", "amount": 1.0}]
        report = check_dates_in_period(txns, ("01/08/26", "31/08/26"))
        assert report.ok
