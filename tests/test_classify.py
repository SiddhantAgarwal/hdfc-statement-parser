"""Classification tests.

The Clef-dependent tests are skipped unless Ollama is running with the
model pulled and a personal eval set exists. The rest — question
building, client payload shape, review-flagging logic, taxonomy loading —
run offline against fakes and the committed categories template.
"""

import json
from pathlib import Path

import httpx
import pytest

from hdfc_parser import classify
from hdfc_parser.config_loader import load_category_descriptions
from hdfc_parser.questions import (
    build_classification_questions,
    build_row_question,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
EVAL_PATH = PROJECT_ROOT / "data" / "eval.jsonl"
EVAL_ACCURACY_BAR = 0.85
# the committed template taxonomy — same one a fresh clone uses
TAXONOMY = load_category_descriptions()


class TestTaxonomy:
    def test_taxonomy_loads_ordered(self):
        names = list(TAXONOMY)
        assert names, "taxonomy should not be empty"
        assert names[-1] == "other", (
            "'other' must be the last option: Clef breaks ties by option "
            "order, so the catch-all must not precede specific categories"
        )
        assert all(isinstance(d, str) and d.strip() for d in TAXONOMY.values())

    def test_question_criteria_follow_taxonomy_order(self):
        qs = build_classification_questions(TAXONOMY)
        assert list(qs["category"]["criteria"]) == list(TAXONOMY)


class TestQuestions:
    def test_classification_questions_shape(self):
        qs = build_classification_questions(TAXONOMY)
        assert set(qs) == {"category", "is_recurring", "is_emi"}
        assert qs["category"]["type"] == "choice"
        assert "other" in qs["category"]["criteria"]
        # preferred-first ordering: "other" must be last on ties
        assert list(qs["category"]["criteria"])[-1] == "other"

    def test_row_question_shape(self):
        q = build_row_question()
        assert q["is_transaction"]["type"] == "noul"
        assert "criteria" in q["is_transaction"]


class FakeClefClient(classify.ClefClient):
    """Offline stand-in returning canned /v1/systemone answers."""

    def __init__(self, answers):
        # skip ClefClient.__init__ (no network)
        self.base_url = "http://fake"
        self.model = "fake"
        self.keep_alive = "0"
        self.answers = answers

    def decide(self, state, questions):
        return self.answers


class TestClassifyAll:
    def _txn(self):
        return {
            "txn_date": "03/08/26",
            "description": "SWIGGY BANGALORE",
            "amount": 432.50,
            "cr_dr": "DR",
        }

    def test_high_confidence_not_flagged(self):
        client = FakeClefClient(
            {
                "category": {"choice": "dining", "confidence": 0.95,
                             "probabilities": {"dining": 0.95}},
                "is_recurring": {"noul": 0.01},
                "is_emi": {"noul": 0.02},
            }
        )
        txn = classify.classify_all(client, [self._txn()], TAXONOMY, 0.6)
        assert txn[0]["category"] == "dining"
        assert txn[0]["review"] is False
        assert txn[0]["is_recurring"] is False
        assert txn[0]["is_emi"] is False

    def test_low_confidence_flagged_for_review(self):
        client = FakeClefClient(
            {
                "category": {"choice": "other", "confidence": 0.40,
                             "probabilities": {"other": 0.40}},
                "is_recurring": {"noul": 0.9},
                "is_emi": {"noul": 0.1},
            }
        )
        txn = classify.classify_all(client, [self._txn()], TAXONOMY, 0.6)
        assert txn[0]["review"] is True
        assert txn[0]["is_recurring"] is True

    def test_request_failure_flags_review(self):
        class DeadClient(FakeClefClient):
            def decide(self, state, questions):
                raise classify.httpx.ConnectError("down")

        client = DeadClient({})
        txn = classify.classify_all(client, [self._txn()], TAXONOMY, 0.6)
        assert txn[0]["category"] == "error"
        assert txn[0]["review"] is True


class TestPayload:
    def test_decide_payload_shape(self, monkeypatch):
        sent = {}

        def fake_post(self, url, json=None):
            sent["url"] = url
            sent["json"] = json

            class R:
                def raise_for_status(self):
                    pass

                def json(self):
                    return {"answers": {}}

            return R()

        monkeypatch.setattr(classify.httpx.Client, "post", fake_post)
        client = classify.ClefClient("http://localhost:11434/", "clef")
        client.decide("hello", build_row_question())
        client.close()
        assert sent["url"].endswith("/v1/systemone")
        assert sent["json"]["model"] == "clef"
        assert "state" in sent["json"]
        assert "questions" in sent["json"]
        assert sent["json"]["keep_alive"] == "30m"


def _ollama_available() -> bool:
    """True if a local Ollama answers on the configured base URL."""
    try:
        r = httpx.get("http://localhost:11434/api/tags", timeout=2.0)
        return r.status_code == 200
    except httpx.HTTPError:
        return False


def _eval_available() -> bool:
    """True if the user has provided a real eval set (data/eval.jsonl)."""
    return EVAL_PATH.exists()


def _load_eval() -> list[dict]:
    entries = []
    with open(EVAL_PATH) as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    assert entries, f"eval set is empty: {EVAL_PATH}"
    return entries


class TestMerchantOverrides:
    """Offline tests for the merchants.json deterministic override layer."""

    def test_load_overrides_skips_comment_keys(self):
        overrides = classify.load_merchant_overrides()
        assert overrides, "merchants.json should load at least one override"
        assert all(not needle.startswith("_") for needle, _ in overrides)
        assert all(
            cat in TAXONOMY for _, cat in overrides
        ), "every override category must be in the taxonomy"

    def test_override_matches_case_insensitive_substring(self):
        overrides = [("example cinemas", "entertainment")]
        assert (
            classify.merchant_override(
                "EXAMPLE CINEMAS PVT LTDBANGALORE", overrides
            )
            == "entertainment"
        )

    def test_no_match_returns_none(self):
        overrides = [("example cinemas", "entertainment")]
        assert classify.merchant_override("SWIGGY BANGALORE", overrides) is None

    def test_classify_all_pins_override_without_clef_call(self):
        client = FakeClefClient({})  # empty answers: Clef never answers
        txn = {
            "txn_date": "03/08/26",
            "description": "EXAMPLE FUEL BUNKMYSURUCITY",
            "amount": 2000.00,
            "cr_dr": "DR",
        }
        result = classify.classify_all(
            client,
            [txn],
            TAXONOMY,
            0.6,
            progress=False,
            overrides=[("example fuel bunk", "fuel")],
        )
        assert result[0]["category"] == "fuel"
        assert result[0]["category_source"] == "merchant_override"
        assert result[0]["review"] is False
        assert result[0]["category_confidence"] == 1.0


@pytest.mark.skipif(
    not _ollama_available() or not _eval_available(),
    reason="requires local Ollama and a real eval set at data/eval.jsonl "
           "(see data/eval.jsonl.example)",
)
class TestEval:
    def test_eval_set_shape(self):
        """Every entry validates against the known taxonomy."""
        for entry in _load_eval():
            assert entry["expected_category"] in TAXONOMY, (
                f"unknown expected_category {entry['expected_category']!r} "
                f"in {entry['description']!r}"
            )
            assert entry["description"].strip()

    def test_macro_accuracy_bar(self):
        """Run Clef over the eval set; macro-accuracy must clear the bar.

        Prints a per-category breakdown and every miss with probabilities
        on failure, so criteria tuning (questions.py) is directed, not
        guesswork.
        """
        entries = _load_eval()
        config = {
            "ollama_base_url": "http://localhost:11434",
            "model": "clef-flash",
            "request_timeout_s": 120.0,
            "keep_alive": "30m",
        }
        client = classify.ClefClient(
            base_url=config["ollama_base_url"],
            model=config["model"],
            timeout_s=config["request_timeout_s"],
            keep_alive=config["keep_alive"],
        )
        try:
            results = classify.classify_all(
                client,
                [
                    {
                        "txn_date": None,
                        "description": e["description"],
                        "amount": e.get("amount"),
                        "cr_dr": e.get("cr_dr"),
                    }
                    for e in entries
                ],
                TAXONOMY,
                confidence_threshold=0.6,
                progress=False,
            )
        finally:
            client.close()

        # macro accuracy: mean of per-expected-category accuracy
        by_cat: dict[str, list[bool]] = {}
        misses = []
        for entry, result in zip(entries, results, strict=True):
            expected = entry["expected_category"]
            got = result.get("category")
            ok = got == expected
            by_cat.setdefault(expected, []).append(ok)
            if not ok:
                probs = result.get("category_probabilities") or {}
                top3 = sorted(probs.items(), key=lambda kv: -kv[1])[:3]
                misses.append(
                    f"{entry['description']!r}: expected {expected}, "
                    f"got {got} | top: "
                    f"{', '.join(f'{c}={p:.2f}' for c, p in top3)}"
                )

        macro = sum(sum(v) / len(v) for v in by_cat.values()) / len(by_cat)
        print(f"\nmacro accuracy: {macro:.3f} over {len(entries)} entries")
        for cat in sorted(by_cat):
            hits, total = sum(by_cat[cat]), len(by_cat[cat])
            print(f"  {cat:<14} {hits}/{total}")
        if misses:
            print("misses:")
            for m in misses:
                print(f"  - {m}")
        assert macro >= EVAL_ACCURACY_BAR, (
            f"macro accuracy {macro:.3f} below bar {EVAL_ACCURACY_BAR}; "
            f"see printed misses to tune data/categories.json descriptions"
        )
