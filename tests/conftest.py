"""Shared test fixtures: HDFC-like statement PDFs generated at test time.

No personal data is ever written to disk — everything is synthetic.
"""

import pymupdf
import pytest


def make_statement_pdf(path, transactions, *, with_period=True, extra_lines=()):
    """Draw an HDFC-like statement into a new PDF at `path`.

    transactions: list of dicts with txn_date, post_date, description,
    amount, cr (bool). Rows are drawn with fixed column x-offsets, wrapped
    descriptions get a second line.
    """
    doc = pymupdf.open()
    page = doc.new_page(width=842, height=595)  # A4 landscape-ish

    y = 60
    if with_period:
        page.insert_text((50, y), "Statement Period: 01/08/26 - 31/08/26")
        y += 20
    page.insert_text((50, y), "Statement of Account")
    y += 25

    # header row — column x-offsets for the table below
    cols = {"txn": 50, "post": 130, "desc": 250, "amount": 600, "cr": 700}
    page.insert_text((cols["txn"], y), "Transaction Date")
    page.insert_text((cols["post"], y), "Posting Date")
    page.insert_text((cols["desc"], y), "Description")
    page.insert_text((cols["amount"], y), "Amount")
    y += 20

    for txn in transactions:
        page.insert_text((cols["txn"], y), txn["txn_date"])
        page.insert_text((cols["post"], y), txn["post_date"])
        page.insert_text((cols["desc"], y), txn["description"])
        amount = f"{txn['amount']:,.2f}"
        page.insert_text((cols["amount"], y), amount)
        if txn.get("cr"):
            page.insert_text((cols["cr"], y), "CR")
        y += 15
        if txn.get("continuation"):
            page.insert_text((cols["desc"], y), txn["continuation"])
            y += 15

    for line in extra_lines:
        page.insert_text((50, y), line)
        y += 15

    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture
def sample_pdf(tmp_path):
    """A clean synthetic statement with a mix of DR and CR rows."""
    return make_statement_pdf(
        tmp_path / "sample.pdf",
        [
            {"txn_date": "03/08/26", "post_date": "04/08/26",
             "description": "SWIGGY BANGALORE", "amount": 432.50},
            {"txn_date": "05/08/26", "post_date": "05/08/26",
             "description": "AMAZON INDIA", "amount": 1299.00},
            {"txn_date": "07/08/26", "post_date": "07/08/26",
             "description": "PAYMENT RECEIVED - THANK YOU",
             "amount": 5000.00, "cr": True},
            {"txn_date": "12/08/26", "post_date": "12/08/26",
             "description": "IOCL PETROL BUNK", "amount": 2000.00},
            {"txn_date": "15/08/26", "post_date": "15/08/26",
             "description": "NETFLIX SUBSCRIPTION MUMBAI",
             "amount": 649.00},
        ],
        extra_lines=[
            "This is a computer generated statement.",
            "Terms and Conditions apply.",
        ],
    )
