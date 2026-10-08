"""Template registry tests: specs drive header detection and noise lines."""

from hdfc_parser.extract import (
    NOISE_LINE_RE,
    Word,
    _find_page_header,
    cluster_rows,
)
from hdfc_parser.templates import REGISTRY


def _words_at(y: float, *label_x_pairs: tuple[str, float]) -> list[Word]:
    return [
        Word(x0=x, y0=y, x1=x + 40, y1=y + 8, text=label)
        for label, x in label_x_pairs
    ]


def _rows(words: list[Word]) -> list:
    return cluster_rows(sorted(words, key=lambda w: (w.y0, w.x0)))


class TestRegistry:
    def test_registry_has_known_templates(self):
        assert set(REGISTRY) == {"infinia", "legacy"}
        for spec in REGISTRY.values():
            assert spec.date_label and spec.amount_labels
            assert spec.anchors, f"{spec.name} needs doc-level anchors"
            assert spec.footer_re is not None

    def test_noise_labels_are_wired_into_noise_regex(self):
        # every registered template's noise labels must actually match
        for spec in REGISTRY.values():
            for label in spec.noise_labels:
                assert NOISE_LINE_RE.match(label), (
                    f"{spec.name}: noise label {label!r} not in NOISE_LINE_RE"
                )


class TestPageHeaderDetection:
    def test_infinia_header_detected(self):
        # infinia puts REWARDS/AMOUNT/PI on a separate line below the
        # DATE & TIME / TRANSACTION DESCRIPTION labels
        words = _words_at(
            76.0,
            ("DATE", 26.0), ("&", 60.0), ("TIME", 70.0),
            ("TRANSACTION", 136.0), ("DESCRIPTION", 200.0),
        ) + _words_at(
            82.0,
            ("REWARDS", 428.0), ("AMOUNT", 525.0),
        )
        spec, header = _find_page_header(_rows(words))
        assert spec is not None and spec.name == "infinia"
        assert header["amount_x"] == 525.0
        assert header["rewards_x"] == 428.0

    def test_legacy_header_detected(self):
        words = _words_at(
            100.0,
            ("Transaction", 50.0), ("Date", 110.0),
            ("Posting", 200.0), ("Date", 260.0),
            ("Description", 330.0), ("Amount", 450.0),
        )
        spec, header = _find_page_header(_rows(words))
        assert spec is not None and spec.name == "legacy"
        assert header["amount_x"] == 450.0
        assert header["rewards_x"] is None  # legacy has no rewards column

    def test_no_recognised_header_returns_none(self):
        words = _words_at(100.0, ("nothing", 50.0), ("recognisable", 120.0))
        spec, header = _find_page_header(_rows(words))
        assert spec is None and header is None
