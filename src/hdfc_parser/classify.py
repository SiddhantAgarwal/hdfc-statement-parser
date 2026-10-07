"""Stage 3-4: Clef-backed row adjudication and transaction classification.

Decision models aren't in the ollama Python library yet, so this talks to
Ollama's /v1/systemone endpoint directly with httpx. One request per
transaction keeps states small and focused; keep_alive holds the model in
memory across the batch.

Known-merchant overrides (merchants.json) run BEFORE Clef: some merchants'
categories simply aren't inferable from the statement text (truncated
names, payment-gateway prefixes, ambiguous brands), so a small curated
alias table pins them deterministically and Clef handles everything else.
"""

import json
from pathlib import Path

import httpx

from hdfc_parser.config_loader import DATA_DIR
from hdfc_parser.questions import (
    build_classification_questions,
    build_row_question,
)

SYSTEMONE_PATH = "/v1/systemone"
MERCHANTS_PATH = DATA_DIR / "merchants.json"


def load_merchant_overrides(path: Path = MERCHANTS_PATH) -> list[tuple[str, str]]:
    """Load (needle, category) override pairs from merchants.json.

    Keys are matched case-insensitively as substrings of the description.
    Entries whose key starts with "_" are metadata comments, not overrides.
    """
    if not path.exists():
        return []
    with open(path) as f:
        data = json.load(f)
    return [
        (key.lower(), category)
        for key, category in data.items()
        if not key.startswith("_")
    ]


def merchant_override(
    description: str, overrides: list[tuple[str, str]]
) -> str | None:
    """Return the pinned category if the description matches a known merchant."""
    lowered = description.lower()
    for needle, category in overrides:
        if needle in lowered:
            return category
    return None


class ClefClient:
    """Minimal client for Ollama's decision-model endpoint."""

    def __init__(
        self,
        base_url: str,
        model: str,
        timeout_s: float = 120.0,
        keep_alive: str = "30m",
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.keep_alive = keep_alive
        self._client = httpx.Client(timeout=httpx.Timeout(timeout_s))

    def decide(self, state: dict | str, questions: dict) -> dict:
        """Ask Clef a batch of questions about a state; returns the answers."""
        payload = {
            "model": self.model,
            "state": state,
            "questions": questions,
            "keep_alive": self.keep_alive,
        }
        resp = self._client.post(self.base_url + SYSTEMONE_PATH, json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data.get("answers", {})

    def close(self) -> None:
        self._client.close()


def adjudicate_row(client: ClefClient, row_text: str) -> tuple[bool, float]:
    """Stage 3: decide whether an undecidable row is a real transaction.

    Returns (is_transaction, probability).
    """
    questions = build_row_question()
    answers = client.decide(state={"row": row_text}, questions=questions)
    ans = answers.get("is_transaction", {})
    return bool(ans.get("noul", 0.0) >= 0.5), float(ans.get("noul", 0.0))


def classify_transaction(
    client: ClefClient,
    category_descriptions: dict[str, str],
    txn: dict,
) -> dict:
    """Stage 4: category + recurring + EMI decisions for one transaction.

    category_descriptions is the ordered taxonomy from data/categories.json
    (name -> description; key order = Clef's option order). txn is a dict
    with keys date, description, amount, cr_dr. Returns a dict with
    category, category_confidence, category_probabilities,
    is_recurring, is_emi — each carrying Clef's own confidence values.
    """
    questions = build_classification_questions(category_descriptions)
    state = {
        "date": txn.get("date"),
        "description": txn.get("description"),
        "amount": txn.get("amount"),
        "dr_cr": txn.get("cr_dr"),
    }
    answers = client.decide(state=state, questions=questions)

    cat = answers.get("category", {})
    recurring = answers.get("is_recurring", {})
    emi = answers.get("is_emi", {})

    return {
        "category": cat.get("choice"),
        "category_confidence": cat.get("confidence"),
        "category_probabilities": cat.get("probabilities"),
        "is_recurring": float(recurring.get("noul", 0.0)) >= 0.5,
        "is_recurring_probability": recurring.get("noul"),
        "is_emi": float(emi.get("noul", 0.0)) >= 0.5,
        "is_emi_probability": emi.get("noul"),
    }


def classify_all(
    client: ClefClient,
    transactions: list[dict],
    category_descriptions: dict[str, str],
    confidence_threshold: float,
    progress: bool = True,
    overrides: list[tuple[str, str]] | None = None,
) -> list[dict]:
    """Classify every transaction; low-confidence answers get review flags.

    category_descriptions is the ordered taxonomy from
    data/categories.json. Known-merchant overrides (from merchants.json)
    short-circuit the Clef call: the pinned category is applied with full
    confidence and the transaction is not flagged for review. Clef
    classifies everything else.
    """
    overrides = overrides if overrides is not None else load_merchant_overrides()
    for i, txn in enumerate(transactions, start=1):
        if progress:
            print(f"  classifying {i}/{len(transactions)}: "
                  f"{txn['description'][:40]!r}")
        pinned = merchant_override(txn.get("description", ""), overrides)
        if pinned is not None:
            txn["category"] = pinned
            txn["category_confidence"] = 1.0
            txn["category_probabilities"] = {pinned: 1.0}
            txn["category_source"] = "merchant_override"
            txn["is_recurring"] = False
            txn["is_recurring_probability"] = None
            txn["is_emi"] = False
            txn["is_emi_probability"] = None
            txn["review"] = False
            continue
        try:
            result = classify_transaction(client, category_descriptions, txn)
        except httpx.HTTPError as exc:
            txn["category"] = "error"
            txn["category_confidence"] = 0.0
            txn["review"] = True
            txn["error"] = f"clef request failed: {exc}"
            continue
        txn.update(result)
        txn["category_source"] = "clef"
        txn["review"] = (
            txn.get("category") is None
            or (txn.get("category_confidence") or 0.0) < confidence_threshold
        )
    return transactions
