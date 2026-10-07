"""Unit tests for extract.py: regexes, clustering, row splitting, fields."""

import pymupdf
from conftest import make_statement_pdf

from hdfc_parser.extract import (
    AMOUNT_RE,
    DATE_RE,
    cluster_rows,
    extract_statement,
    parse_row_fields,
    split_rows,
)


class TestRegexes:
    def test_date_matches(self):
        assert DATE_RE.match("03/08/26")
        assert DATE_RE.match("03/08/2026")

    def test_date_rejects(self):
        assert not DATE_RE.match("3/8/26")
        assert not DATE_RE.match("030826")
        assert not DATE_RE.match("SWIGGY")

    def test_amount_matches(self):
        assert AMOUNT_RE.match("432.50")
        assert AMOUNT_RE.match("1,299.00")
        assert AMOUNT_RE.match("Rs.1,299.00")
        assert AMOUNT_RE.match("Rs 1,299.00")
        assert AMOUNT_RE.match("INR 1,299.00")

    def test_amount_rejects(self):
        assert not AMOUNT_RE.match("432")
        assert not AMOUNT_RE.match("SWIGGY")
        assert not AMOUNT_RE.match("CR")


class TestRowBuilding:
    def test_sample_statement_extracts_rows(self, sample_pdf):
        extracted = extract_statement(str(sample_pdf))
        # 5 dated rows; notice lines rejected; 0 ambiguous
        assert len(extracted.rows) == 5
        assert len(extracted.rejected) >= 1  # at least the Terms/notice lines
        assert len(extracted.ambiguous) == 0
        # notice text must not leak into the last transaction
        last = extracted.rows[-1]
        assert "computer generated" not in last.description
        assert last.description == "NETFLIX SUBSCRIPTION MUMBAI"
        # CR row detected via the CR word
        cr_rows = [r for r in extracted.rows if r.cr_dr == "CR"]
        assert len(cr_rows) == 1 and cr_rows[0].amount == 5000.00

    def test_anchors_detected(self, sample_pdf):
        extracted = extract_statement(str(sample_pdf))
        assert "Transaction Date" in extracted.anchors_found
        # anchors_missing covers BOTH templates; only the recognised
        # template's anchors matter
        assert "Transaction Date" not in extracted.anchors_missing
        assert "Amount" not in extracted.anchors_missing
        assert extracted.template == "legacy"

    def test_wrapped_description_joins_previous_row(self, tmp_path):
        pdf = make_statement_pdf(
            tmp_path / "wrapped.pdf",
            [
                {"txn_date": "03/08/26", "post_date": "04/08/26",
                 "description": "VERY LONG MERCHANT NAME PVT LTD",
                 "amount": 100.00,
                 "continuation": "BANGALORE KARNATAKA 560001"},
            ],
        )
        extracted = extract_statement(str(pdf))
        assert len(extracted.rows) == 1
        row = parse_row_fields(extracted.rows[0])
        assert "BANGALORE" in row.description
        assert row.amount == 100.00

    def test_missing_anchors_reported(self, tmp_path):
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((50, 50), "Nothing recognisable here")
        path = tmp_path / "bad.pdf"
        doc.save(str(path))
        doc.close()
        extracted = extract_statement(str(path))
        assert "Transaction Date" in extracted.anchors_missing


class TestParseRowFields:
    def _row_from_texts(self, texts):
        from hdfc_parser.extract import RawRow, Word

        row = RawRow(y=0.0)
        for i, t in enumerate(texts):
            row.words.append(
                Word(x0=float(i * 10), y0=0.0, x1=i * 10 + 9.0, y1=8.0, text=t)
            )
        return row

    def test_dr_row(self):
        row = parse_row_fields(
            self._row_from_texts(
                ["03/08/26", "04/08/26", "SWIGGY", "BANGALORE", "432.50"]
            )
        )
        assert row.txn_date == "03/08/26"
        assert row.post_date == "04/08/26"
        assert row.description == "SWIGGY BANGALORE"
        assert row.amount == 432.50
        assert row.cr_dr == "DR"

    def test_cr_row(self):
        row = parse_row_fields(
            self._row_from_texts(
                ["07/08/26", "07/08/26", "PAYMENT", "RECEIVED", "5,000.00", "CR"]
            )
        )
        assert row.amount == 5000.00
        assert row.cr_dr == "CR"

    def test_thousands_separator(self):
        row = parse_row_fields(
            self._row_from_texts(["03/08/26", "1,299.00"])
        )
        assert row.amount == 1299.00

    def test_cluster_rows_splits_on_y(self):
        from hdfc_parser.extract import Word

        words = [
            Word(x0=0, y0=10, x1=5, y1=15, text="a"),
            Word(x0=10, y0=10, x1=15, y1=15, text="b"),
            Word(x0=0, y0=30, x1=5, y1=35, text="c"),
        ]
        rows = cluster_rows(words)
        assert len(rows) == 2
        assert rows[0].text == "a b"
        assert rows[1].text == "c"

    def test_split_rows_on_empty_input(self):
        txn, rejected, ambiguous = split_rows([])
        assert (txn, rejected, ambiguous) == ([], [], [])
