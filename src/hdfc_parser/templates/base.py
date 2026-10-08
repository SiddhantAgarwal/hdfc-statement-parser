"""TemplateSpec: the declarative contract between the engine and one HDFC
statement family.

The extraction engine (hdfc_parser.extract) is generic: it matches each
PDF page against the registered specs and parses with whichever spec's
header it finds. Everything family-specific lives in a spec - adding a
new HDFC card family means adding a spec module to this package and
registering it in __init__.py, not touching the engine.
"""

import re
from dataclasses import dataclass

# Shared table-end markers: the first row below the header matching this
# ends the transaction table (both current families use these).
FOOTER_RE = re.compile(r"^(?:TRANSACTIONS TOTAL|Page \d+ of\b|Total Amount Due)")


@dataclass(frozen=True)
class TemplateSpec:
    """Declarative description of one HDFC statement table layout.

    - date_label: the leftmost column's label; its presence in a row
      anchors page-header detection.
    - amount_labels / rewards_labels: column labels used to derive the
      x-boundaries of the amount (and optional rewards) columns from the
      header row or a nearby label row.
    - anchors: document-level detection (all must appear in the full
      text); used only when no page header was recognised.
    - noise_labels: header labels that mark a line as noise when they
      start it (rejects repeated headers and label-like notice text).
    - footer_re: the first row below the header matching this ends the
      table.
    """

    name: str
    date_label: str
    amount_labels: tuple[str, ...]
    rewards_labels: tuple[str, ...] = ()
    anchors: tuple[str, ...] = ()
    noise_labels: tuple[str, ...] = ()
    footer_re: re.Pattern[str] = FOOTER_RE
    notes: str = ""
