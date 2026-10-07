"""Stages 0-2: validate input, extract words, build transaction rows.

Two statement layouts are recognised:

- "infinia": HDFC's current template - a table headed
  "DATE & TIME / TRANSACTION DESCRIPTION / REWARDS / AMOUNT / PI"
  (Infinia-class cards). Amounts carry the rupee glyph rendered as "C"
  with Indian digit grouping (C 1,35,372.00); credits are prefixed "+"
  (e.g. "+ C 37.96"); there is no CR/DR column and no posting date.
- "legacy": the "Transaction Date / Posting Date / Description / Amount"
  layout this parser was originally sketched against.

Both are parsed column-aware: the header labels' x-positions define column
regions, and rows are banded by the y of each row's date word. This
reconstructs rows correctly even when the PDF emits text column-major
(all dates in one block, all descriptions in another), and folds wrapped
multi-line descriptions into their transaction.

Pages with no recognised header fall back to reading-order row clustering.
Extraction is fully deterministic - Clef never extracts fields, it only
makes decisions on rows we've already built.
"""

import re
from dataclasses import dataclass, field

import pymupdf

from hdfc_parser.config_loader import (
    REDACTOR_REGEX_PATTERNS,
    load_redaction_needles,
)

# a date, optionally followed by a time in the same word ("20/08/2026 11:04")
DATE_WORD_RE = re.compile(r"^(\d{2}/\d{2}/\d{2,4})(?:\s+(\d{1,2}:\d{2}))?$")
DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{2,4}$")  # validate.py imports this
TIME_RE = re.compile(r"^\d{1,2}:\d{2}$")
# amount inside a cell: optional sign, optional C/Rs/INR prefix, digits
# (Indian grouping like 1,35,372.00 included)
AMOUNT_CELL_RE = re.compile(r"([+-])?\s*(?:C|Rs\.?|INR|\u20b9)?\s*([\d,]+\.\d{2})")
REWARDS_CELL_RE = re.compile(r"\+?\s*(\d+)")
# bare right-aligned amount number ("649.00", "1,35,372.00")
AMOUNT_NUM_RE = re.compile(r"^[\d,]+\.\d{2}$")
# rewards cell words: "+", "100", "3730"
REWARD_WORD_RE = re.compile(r"^\+?\d+$")
CR_WORD_RE = re.compile(r"^CR$")
# standalone amount token (legacy fallback path)
AMOUNT_RE = re.compile(r"^[+-]?\s?(?:C|Rs\.?|INR|\u20b9)?\s?[\d,]+\.\d{2}$")
CR_RE = re.compile(r"\bCR\b")
DR_RE = re.compile(r"\bDR\b")

TEMPLATES = {
    "infinia": ["DATE & TIME", "TRANSACTION DESCRIPTION"],
    "legacy": ["Transaction Date", "Amount"],
}
AMOUNT_LABELS = ("AMOUNT", "Amount")
REWARDS_LABELS = ("REWARDS", "Rewards")
# rows at or below the first of these end the transaction table
FOOTER_ROW_RE = re.compile(
    r"^(?:TRANSACTIONS TOTAL|Page \d+ of\b|Total Amount Due)"
)
LAST_ROW_MAX_HEIGHT = 24.0  # room for one wrapped description line
Y_TOL = 4.0
LABEL_SEARCH_BAND = 60.0  # how far below the DATE label the AMOUNT label may sit

# redaction leftovers / table junk that never belongs in a description
DESC_JUNK_RE = re.compile(r"^[\[\]:,;.\s]*$|^\[?CKYC\b|^ID$")
# HDFC prefixes standalone "EMI" to descriptions of transactions that are
# merely ELIGIBLE for EMI conversion - not actual instalments (those are
# the OFFUS EMI,PRIN/INT rows). Strip the tag so it doesn't mislead the
# classifier or pollute merchant names.
EMI_TAG = "EMI"

# Lines that are never transactions (legacy fallback path only).
NOISE_LINE_RE = re.compile(
    r"^(?:"
    r"Transaction Date|Posting Date|Description|Amount|Dr/Cr|CR/DR"
    r"|DATE & TIME|TRANSACTION DESCRIPTION|REWARDS"
    r"|Statement of [Aa]ccount"
    r"|Previous Balance|Opening Balance|Closing Balance|Total\b"
    r"|Reward Points?|Page \d+"
    r"|This is a (?:computer generated|system generated)"
    r"|.*Terms and Conditions"
    r")"
)


@dataclass
class Word:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str


@dataclass
class RawRow:
    """A reconstructed candidate row, before classification/validation."""

    y: float
    words: list[Word] = field(default_factory=list)
    txn_date: str | None = None
    txn_time: str | None = None
    post_date: str | None = None
    amount: float | None = None
    cr_dr: str | None = None  # "CR", "DR", or None
    rewards: int | None = None
    description_parts: list[str] = field(default_factory=list)
    source: str = "fallback"  # "column" or "fallback"

    @property
    def description(self) -> str:
        return " ".join(self.description_parts).strip()

    @property
    def text(self) -> str:
        return " ".join(w.text for w in sorted(self.words, key=lambda w: w.x0)).strip()


@dataclass
class ExtractedStatement:
    pages: int
    rows: list[RawRow]
    rejected: list[RawRow]  # noise, kept for the loud report
    ambiguous: list[RawRow]  # undateable rows for Clef adjudication
    template: str | None
    anchors_found: list[str]
    anchors_missing: list[str]
    full_text: str  # for period/total parsing downstream


# ── Stage 0: input safety ────────────────────────────────────────────────


def check_input_is_safe(path: str) -> list[str]:
    """Refuse encrypted PDFs; scan for redaction leaks.

    Returns a list of leak descriptions; empty means safe to proceed.
    """
    doc = pymupdf.open(path)
    try:
        if doc.needs_pass:
            return [
                "PDF is password-protected - run hdfc-statement-redactor "
                "on it first; the parser only accepts redacted, "
                "unencrypted output."
            ]
    finally:
        doc.close()

    leaks: list[str] = []
    exact_strings, scan_available = load_redaction_needles()

    doc = pymupdf.open(path)
    try:
        compiled = [re.compile(p) for p in REDACTOR_REGEX_PATTERNS]
        for i, page in enumerate(doc, start=1):
            text = page.get_text()
            if scan_available:
                for needle in exact_strings:
                    if needle.lower() in text.lower():
                        leaks.append(
                            f"page {i}: exact string still present "
                            f"(truncated for safety): {needle[:12]!r}..."
                        )
            for pat in compiled:
                for m in pat.findall(text):
                    leaks.append(f"page {i}: pattern matched: {m!r}")
    finally:
        doc.close()
    return leaks


# ── Stages 1-2: extraction ──────────────────────────────────────────────


def extract_statement(path: str) -> ExtractedStatement:
    """Open a file and extract."""
    doc = pymupdf.open(path)
    try:
        return extract_statement_from_doc(doc)
    finally:
        doc.close()


def extract_statement_from_doc(doc: "pymupdf.Document") -> ExtractedStatement:
    """Anchor detection, word extraction, column-aware row reconstruction."""
    txn_rows: list[RawRow] = []
    rejected: list[RawRow] = []
    ambiguous: list[RawRow] = []
    full_text = ""

    for page in doc:
        full_text += page.get_text()
        words = _words_sorted(page)
        rows = cluster_rows(words)
        header = _find_header(rows)
        if header is not None:
            page_rows, page_noise = _parse_column_page(words, rows, header)
            txn_rows += page_rows
            rejected += page_noise
        else:
            page_txn, page_noise, page_ambig = _parse_fallback_page(rows)
            txn_rows += page_txn
            rejected += page_noise
            ambiguous += page_ambig

    all_anchors = sorted({a for anchors in TEMPLATES.values() for a in anchors})
    anchors_found = [a for a in all_anchors if a in full_text]
    anchors_missing = [a for a in all_anchors if a not in full_text]
    template = next(
        (
            name
            for name, anchors in TEMPLATES.items()
            if all(a in full_text for a in anchors)
        ),
        None,
    )
    return ExtractedStatement(
        pages=doc.page_count,
        rows=txn_rows,
        rejected=rejected,
        ambiguous=ambiguous,
        template=template,
        anchors_found=anchors_found,
        anchors_missing=anchors_missing,
        full_text=full_text,
    )


def _words_sorted(page: "pymupdf.Page") -> list[Word]:
    """Words with bboxes, sorted by (y, x) to defeat column-major emission.

    The statement's text layer embeds table-delimiter pipes inside words
    (e.g. "23/08/2026|"); strip them so content regexes can anchor cleanly.
    """
    words = []
    for x0, y0, x1, y1, text, *_rest in page.get_text("words"):
        cleaned = text.strip("|").strip()
        if cleaned:
            words.append(Word(x0=x0, y0=y0, x1=x1, y1=y1, text=cleaned))
    words.sort(key=lambda w: (w.y0, w.x0))
    return words


def cluster_rows(words: list[Word], y_tolerance: float = Y_TOL) -> list[RawRow]:
    """Group words into rows by y-coordinate (words must be y-sorted)."""
    rows: list[RawRow] = []
    current: RawRow | None = None
    for w in words:
        if current is None or w.y0 - current.y > y_tolerance:
            if current is not None:
                rows.append(current)
            current = RawRow(y=w.y0)
        current.words.append(w)
    if current is not None:
        rows.append(current)
    return rows


def _fold_lines(words: list[Word]) -> list[str]:
    """Join words into description parts, respecting wrapped lines.

    A wrapped description spans two visual lines with different y's;
    sorting everything by x interleaves them. Group words whose y is
    within Y_TOL into lines, order lines top-to-bottom and words within
    each line left-to-right. Whole lines that match NOISE_LINE_RE (notice
    text printed after the table) are dropped so they don't pollute the
    last transaction's description.
    """
    lines: list[list[Word]] = []
    for w in sorted(words, key=lambda w: (w.y0, w.x0)):
        if lines and w.y0 - lines[-1][0].y0 <= Y_TOL:
            lines[-1].append(w)
        else:
            lines.append([w])
    parts: list[str] = []
    for line in lines:
        joined = " ".join(w.text for w in sorted(line, key=lambda w: w.x0))
        if NOISE_LINE_RE.match(joined):
            continue
        parts.extend(w.text for w in sorted(line, key=lambda w: w.x0))
    return parts


# ── column-aware parsing (infinia + legacy headers) ──────────────────────


def _find_header(rows: list[RawRow]) -> dict | None:
    """Locate the table header and its column x-boundaries.

    Returns {"y": header row y, "amount_x": amount column start,
    "rewards_x": rewards column start or None}, or None if the page has no
    recognised header. The AMOUNT label may sit in the DATE label's row or
    in a row up to LABEL_SEARCH_BAND below it (the Infinia template puts
    "REWARDS AMOUNT PI" on a separate line from "DATE & TIME").
    """
    for row in rows:
        if not ("DATE & TIME" in row.text or "Transaction Date" in row.text):
            continue
        candidates = [row] + [r for r in rows if 0 < r.y - row.y <= LABEL_SEARCH_BAND]
        for cand in candidates:
            amount = [w for w in cand.words if w.text in AMOUNT_LABELS]
            if not amount:
                continue
            rewards = [w for w in cand.words if w.text in REWARDS_LABELS]
            return {
                "y": row.y,
                "amount_x": min(w.x0 for w in amount),
                "rewards_x": min((w.x0 for w in rewards), default=None),
            }
    return None


def _find_footer_y(rows: list[RawRow], header_y: float) -> float:
    """Y of the first footer marker below the header, else +inf."""
    for row in rows:
        if row.y > header_y + 8 and FOOTER_ROW_RE.match(row.text):
            return row.y - 6
    return float("inf")


def _parse_column_page(
    words: list[Word], rows: list[RawRow], header: dict
) -> tuple[list[RawRow], list[RawRow]]:
    """Reconstruct transactions from one header'd page.

    Body words (between header and footer) are banded by the y of each
    date word; each band collects its date/time, description, rewards and
    amount by column x-boundaries. Band edges are midpoints between
    consecutive dates, so wrapped description lines fold into the row
    above them.
    """
    footer_y = _find_footer_y(rows, header["y"])
    body_top = header["y"] + 8
    body = [w for w in words if body_top < w.y0 < footer_y]

    amount_x = header["amount_x"]
    rewards_x = header["rewards_x"]

    date_words = [w for w in body if w.x0 < amount_x and DATE_WORD_RE.match(w.text)]
    if not date_words:
        return [], []

    # cluster date words' y into row anchors
    ys: list[float] = []
    for w in sorted(date_words, key=lambda w: w.y0):
        if not ys or w.y0 - ys[-1] > Y_TOL:
            ys.append(w.y0)

    last_end = min(footer_y, ys[-1] + LAST_ROW_MAX_HEIGHT)

    txn_rows: list[RawRow] = []
    for i, y in enumerate(ys):
        lo = body_top if i == 0 else (ys[i - 1] + y) / 2
        hi = (y + ys[i + 1]) / 2 if i + 1 < len(ys) else last_end
        if hi <= lo:
            continue
        band = [w for w in body if lo <= w.y0 < hi]
        txn_rows.append(_build_row(band, amount_x, rewards_x, y))

    noise: list[RawRow] = []
    above = [w for w in body if w.y0 < ys[0]]
    if above:
        noise.append(RawRow(y=body_top, words=above, source="column"))
    leftover = [w for w in body if w.y0 >= last_end]
    if leftover:
        noise.append(RawRow(y=last_end, words=leftover, source="column"))
    return txn_rows, noise


def _strip_emi_tag(parts: list[str]) -> list[str]:
    """Drop a leading eligibility tag ("EMI") from description parts.

    HDFC marks convert-to-EMI-eligible transactions with a standalone
    "EMI" prefix in the description; it says nothing about the purchase
    itself, so it shouldn't survive into the parsed description (or reach
    the Clef is_emi question as a false signal).
    """
    if parts and parts[0] == EMI_TAG:
        return parts[1:]
    return parts


def _build_row(
    band: list[Word], amount_x: float, rewards_x: float | None, y: float
) -> RawRow:
    """Assemble one RawRow from the words of a single y-band."""
    row = RawRow(y=y, words=band, source="column")

    date_words = [w for w in band if w.x0 < amount_x and DATE_WORD_RE.match(w.text)]
    date_words.sort(key=lambda w: w.x0)
    reserved_ids = {id(w) for w in date_words}
    for i, w in enumerate(date_words):
        m = DATE_WORD_RE.match(w.text)
        if i == 0:
            row.txn_date = m.group(1)
            row.txn_time = m.group(2)
        elif i == 1:
            row.post_date = m.group(1)

    # times emitted as separate words ("20/08/2026" + "11:04")
    time_words = [
        w
        for w in band
        if TIME_RE.match(w.text) and id(w) not in reserved_ids
    ]
    if time_words and row.txn_time is None:
        time_words.sort(key=lambda w: w.x0)
        row.txn_time = time_words[0].text
    reserved_ids.update(id(w) for w in time_words)

    desc_boundary = rewards_x if rewards_x is not None else amount_x - 5
    desc_words = [
        w
        for w in band
        if id(w) not in reserved_ids
        and w.x0 < desc_boundary
        and not (rewards_x is not None and w.x0 >= rewards_x)
        and not DESC_JUNK_RE.match(w.text)
    ]
    row.description_parts = _strip_emi_tag(_fold_lines(desc_words))

    # Amount: the rightmost bare decimal number in the band. Amounts are
    # right-aligned to the column's right edge, so wide ones (e.g.
    # 1,35,372.00) start left of the AMOUNT label's x-position - a fixed
    # x cut would drop them. The sign/C prefix sits within ~30pt to its
    # left; rewards integers sit further left still.
    amount_nums = [w for w in band if AMOUNT_NUM_RE.match(w.text)]
    if amount_nums:
        num = max(amount_nums, key=lambda w: w.x0)
        row.amount = round(float(num.text.replace(",", "")), 2)
        # sign/C prefix sits just LEFT of the number ("+ C 1,35,372.00");
        # legacy layout puts a CR marker RIGHT of it ("5,000.00 CR")
        prefix = [
            p
            for p in band
            if p is not num
            and p.x1 <= num.x0 + 2
            and num.x0 - p.x0 <= 30
        ]
        is_credit = "+" in (p.text for p in prefix) or any(
            CR_WORD_RE.match(p.text) for p in prefix
        )
        if not is_credit:
            is_credit = any(
                CR_WORD_RE.match(p.text)
                for p in band
                if p is not num and p.x0 >= num.x1
            )
        row.cr_dr = "CR" if is_credit else "DR"
        if rewards_x is not None:
            rewards_texts = [
                w.text
                for w in band
                if rewards_x <= w.x0 < num.x0 - 30
                and REWARD_WORD_RE.match(w.text)
            ]
            if rewards_texts:
                rm = REWARDS_CELL_RE.search(" ".join(rewards_texts))
                if rm:
                    row.rewards = int(rm.group(1))
    return row


# ── legacy fallback (pages without a recognised header) ──────────────────


def _parse_fallback_page(
    rows: list[RawRow],
) -> tuple[list[RawRow], list[RawRow], list[RawRow]]:
    """Reading-order clustering for header-less pages.

    Pages without a single date-shaped word are skipped entirely (pure
    notice pages would otherwise flood the Clef adjudication stage).
    """
    if not any(DATE_WORD_RE.match(w.text) for row in rows for w in row.words):
        return [], [], []
    txn_rows, rejected, ambiguous = split_rows(rows)
    for row in txn_rows:
        parse_row_fields(row)
    return txn_rows, rejected, ambiguous


def split_rows(
    rows: list[RawRow],
) -> tuple[list[RawRow], list[RawRow], list[RawRow]]:
    """Sort clustered rows into transactions, noise, and ambiguous.

    - starts with a date -> transaction candidate
    - matches NOISE_LINE_RE -> rejected
    - no date, not noise, previous transaction exists -> continuation
      (folded into that transaction's description)
    - otherwise -> ambiguous (Stage 3 sends these to Clef)
    """
    txn_rows: list[RawRow] = []
    rejected: list[RawRow] = []
    ambiguous: list[RawRow] = []
    for row in rows:
        text = row.text
        if not text or NOISE_LINE_RE.match(text):
            rejected.append(row)
            continue
        tokens = sorted(row.words, key=lambda w: w.x0)
        if tokens and DATE_RE.match(tokens[0].text):
            row.txn_date = tokens[0].text
            txn_rows.append(row)
        elif txn_rows:
            txn_rows[-1].words.extend(row.words)
        else:
            ambiguous.append(row)
    return txn_rows, rejected, ambiguous


def parse_row_fields(row: RawRow) -> RawRow:
    """Extract date/amount/CR-DR/description from a fallback candidate row.

    Conventions (legacy layout):
    - first token: transaction date; second date token: posting date
    - amount: rightmost amount-shaped token
    - CR marker near the amount; absence of CR implies DR
    """
    tokens = sorted(row.words, key=lambda w: w.x0)
    texts = [w.text for w in tokens]

    idx = 0
    if texts and DATE_RE.match(texts[0]):
        row.txn_date = texts[0]
        idx = 1
    if len(texts) > idx and DATE_RE.match(texts[idx]):
        row.post_date = texts[idx]
        idx += 1
    if len(texts) > idx and TIME_RE.match(texts[idx]):
        row.txn_time = texts[idx]
        idx += 1

    row.cr_dr = "DR"
    amount_idx = None
    for i in range(len(texts) - 1, idx - 1, -1):
        if AMOUNT_RE.match(texts[i]):
            amount_idx = i
            break

    if amount_idx is not None:
        m = AMOUNT_CELL_RE.search(texts[amount_idx])
        if m:
            row.amount = round(float(m.group(2).replace(",", "")), 2)
        tail = " ".join(texts[amount_idx : amount_idx + 2])
        head = " ".join(texts[idx:amount_idx])
        if CR_RE.search(tail) or CR_RE.search(head):
            row.cr_dr = "CR"
        elif DR_RE.search(tail):
            row.cr_dr = "DR"
        row.description_parts = [
            t for t in texts[idx:amount_idx] if not DESC_JUNK_RE.match(t)
        ]
    else:
        row.description_parts = [
            t for t in texts[idx:] if not DESC_JUNK_RE.match(t)
        ]
    return row
