"""Load parser configuration from config.json (gitignored, personal).

Config files live in data/ (gitignored) with committed .example templates:
config.json (runtime settings), categories.json (the Clef category
taxonomy — names, order, and descriptions), merchants.json (category
overrides), eval.jsonl (labelled eval set).
"""

import json
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent.parent  # repo root
DATA_DIR = PROJECT_DIR / "data"
CONFIG_PATH = DATA_DIR / "config.json"
CATEGORIES_PATH = DATA_DIR / "categories.json"
CATEGORIES_EXAMPLE_PATH = DATA_DIR / "categories.json.example"

DEFAULTS = {
    "ollama_base_url": "http://localhost:11434",
    "model": "clef",
    "keep_alive": "30m",
    "request_timeout_s": 120.0,
    "confidence_threshold": 0.6,
    "categories": None,  # categories.json order used when absent
}

# Redactor needles that must NOT survive in a parser input. The exact-string
# list lives only in the redactor's config.json; the regex patterns are safe
# to copy (they contain no personal data).
REDACTOR_REGEX_PATTERNS = [
    r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b",  # full card number
    r"\b\d{4}[\s-]?XXX[\s-]?\d{4}\b",  # 4311-XXXX-XXXX-1234 style
    r"\bXX{2,}\d{2,4}\b|\b\d{2,4}XX{2,}\b",  # XX1234 / 1234XX
    r"[\w.+-]+@[\w-]+\.[\w.]+",  # email addresses
    r"\+91[\s-]?\d[\d\s-]{8,}\d",  # +91 phone numbers
    r"\b[A-Z]{5}\d{4}[A-Z]\b",  # PAN number
]


def _load_json(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)


def load_config() -> dict:
    """Merge config.json over defaults. Prints guidance if missing."""
    if CONFIG_PATH.exists():
        user_config = _load_json(CONFIG_PATH)
    else:
        user_config = {}
        print(
            f"NOTE: {CONFIG_PATH.name} not found — using defaults "
            f"(and skipping the exact-string half of the redaction safety "
            f"scan, since that needs your personal config).\n"
            f"Copy config.json.example to config.json to configure.\n"
        )

    config = dict(DEFAULTS)
    config.update(user_config)
    return config


def load_redaction_needles() -> tuple[list[str], bool]:
    """Return (exact_strings, scan_available).

    scan_available is False when the redactor's config.json can't be found;
    in that case the caller skips the exact-string half of the safety scan.
    """
    redactor_config = (
        PROJECT_DIR.parent / "hdfc-statement-redactor" / "config.json"
    )
    if not redactor_config.exists():
        return [], False
    data = _load_json(redactor_config)
    strings = data.get("exact_strings", [])
    if not strings:
        print(
            "WARNING: redactor config.json has no exact_strings — the "
            "safety scan will check regex patterns only.\n"
        )
    return strings, True


def load_category_descriptions() -> dict[str, str]:
    """Load the category taxonomy from data/categories.json.

    Returns an ordered {name: description} mapping. The file's key order
    is the option order Clef sees (ties resolve by option order, so
    specific categories first, "other" last). Falls back to the committed
    categories.json.example when the personal file is missing.
    """
    if CATEGORIES_PATH.exists():
        data = _load_json(CATEGORIES_PATH)
    elif CATEGORIES_EXAMPLE_PATH.exists():
        print(
            f"NOTE: {CATEGORIES_PATH.name} not found — using the committed "
            f"template {CATEGORIES_EXAMPLE_PATH.name}. Copy it to "
            f"{CATEGORIES_PATH.name} to tune descriptions for your "
            f"merchants.\n"
        )
        data = _load_json(CATEGORIES_EXAMPLE_PATH)
    else:
        sys.exit(
            f"ERROR: neither {CATEGORIES_PATH} nor "
            f"{CATEGORIES_EXAMPLE_PATH} exists — the category taxonomy "
            f"is required."
        )
    return {
        name: desc
        for name, desc in data.items()
        if not name.startswith("_")
    }


def require_categories(
    config: dict, descriptions: dict[str, str]
) -> list[str]:
    """Resolve the ordered category list against the loaded taxonomy.

    - config["categories"] (if set) selects/reorders the list; every name
      must exist in the taxonomy
    - otherwise the categories.json key order is used as-is
    - "other" is always appended last if missing
    """
    categories = list(config.get("categories") or descriptions.keys())
    unknown = [c for c in categories if c not in descriptions]
    if unknown:
        sys.exit(
            f"ERROR: unknown categories in config.json: {unknown}. "
            f"Known categories: {sorted(descriptions)}"
        )
    if "other" not in categories:
        categories.append("other")
    return categories
