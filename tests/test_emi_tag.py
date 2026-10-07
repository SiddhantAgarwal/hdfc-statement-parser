"""Tests for the EMI eligibility-tag stripping in _build_row/_strip_emi_tag.

HDFC prefixes a standalone "EMI" to descriptions of transactions that are
merely ELIGIBLE for EMI conversion - not actual instalments (those are the
OFFUS EMI,PRIN/INT rows). The tag must be removed from parsed descriptions.
"""

from hdfc_parser.extract import Word, _build_row, _strip_emi_tag


def _band(texts: list[str], amount_text: str = "C 439.00") -> list[Word]:
    """Build a y-band of words mimicking an Infinia transaction row."""
    band = []
    x = 26.0
    for t in texts:
        band.append(Word(x0=x, y0=90.0, x1=x + 40, y1=100.0, text=t))
        x += 45.0
    # amount words right-aligned at ~530
    ax = 533.0
    for t in amount_text.split():
        band.append(Word(x0=ax, y0=90.0, x1=ax + 12, y1=100.0, text=t))
        ax += 14.0
    return band


class TestStripEmiTag:
    def test_leading_emi_stripped(self):
        assert _strip_emi_tag(["EMI", "MAKEMYTRIP", "NEW DELHI"]) == [
            "MAKEMYTRIP",
            "NEW DELHI",
        ]

    def test_no_emi_prefix_untouched(self):
        assert _strip_emi_tag(["SWIGGY", "BANGALORE"]) == [
            "SWIGGY",
            "BANGALORE",
        ]

    def test_only_emi_leaves_empty(self):
        assert _strip_emi_tag(["EMI"]) == []

    def test_embedded_emi_kept(self):
        # "EMI" mid-description (not a tag) stays
        assert _strip_emi_tag(["OFFUS", "EMI,PRIN", "NB:03"]) == [
            "OFFUS",
            "EMI,PRIN",
            "NB:03",
        ]

    def test_build_row_strips_emi_tag(self):
        band = _band(["14/09/2026", "16:05", "EMI", "UNIQLO", "INDIA"])
        row = _build_row(band, amount_x=524.5, rewards_x=428.0, y=90.0)
        assert row.description == "UNIQLO INDIA"
        assert row.amount == 439.00

    def test_build_row_keeps_offus_emi_rows(self):
        # actual instalments keep their full description
        band = _band(["20/09/2026", "00:00", "OFFUS", "EMI,PRIN", "NB:03"])
        row = _build_row(band, amount_x=524.5, rewards_x=428.0, y=90.0)
        assert row.description == "OFFUS EMI,PRIN NB:03"
