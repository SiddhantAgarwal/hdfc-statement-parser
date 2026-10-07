"""Clef question definitions for classification and row adjudication.

Clef is a decision model: questions are declared as data and sent to
Ollama's /v1/systemone endpoint in one batch per transaction. The category
taxonomy (names, order, descriptions) is config-driven — loaded from
data/categories.json by config_loader — so this module only shapes the
questions, it does not own the categories.

Option order matters on ties (Clef follows option order): the caller
supplies the ordered criteria; keep the preferred/most likely category
first and "other" last.
"""


def build_classification_questions(
    category_descriptions: dict[str, str],
) -> dict:
    """Build the question set asked for every transaction.

    category_descriptions is an ordered {name: description} mapping; its
    key order becomes the choice option order. Three questions are judged
    by Clef in a single forward pass: category (choice), recurring charge
    (noul), EMI (noul).
    """
    return {
        "category": {
            "type": "choice",
            "instructions": (
                "Which spending category does this credit card transaction "
                "belong to? Judge from the merchant description and amount; "
                "DR means money spent, CR means a payment or refund received."
            ),
            "criteria": dict(category_descriptions),
        },
        "is_recurring": {
            "type": "noul",
            "instructions": (
                "Is this a recurring subscription or membership charge "
                "billed periodically by the same merchant (for example "
                "Netflix, Spotify, a gym, a hosting plan)?"
            ),
        },
        "is_emi": {
            "type": "noul",
            "instructions": (
                "Is this an EMI (equated monthly instalment) charge, "
                "including merchant EMI conversions and no-cost EMI "
                "bookings? EMI descriptions usually mention EMI, "
                "instalment, flexi-pay, or carry a long reference number."
            ),
        },
    }


def build_row_question() -> dict:
    """Stage 3 adjudication for rows the deterministic anchors cannot decide."""
    return {
        "is_transaction": {
            "type": "noul",
            "instructions": (
                "Is this text a single genuine credit card transaction "
                "entry (a dated row with a merchant description and an "
                "amount), rather than a header, footer, notice, or "
                "summary line?"
            ),
            "criteria": {
                "true": "The text is one credit card transaction entry.",
                "false": (
                    "The text is a header, footer, notice, summary, or "
                    "some other non-transaction line."
                ),
            },
        }
    }
