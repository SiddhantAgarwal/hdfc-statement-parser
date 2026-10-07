# HDFC Statement Parser

Parse redacted, unencrypted HDFC credit card statement PDFs — the output of
[hdfc-statement-redactor](https://github.com/SiddhantAgarwal/hdfc-statement-redactor) — into structured
transaction data, with per-transaction category classification done
**locally** by [Clef](https://ollama.com/library/clef), Cloudflare's
decision model, via [Ollama](https://ollama.com).

## Repository layout

```
hdfc-statement-parser/
├── src/hdfc_parser/     # the parser package (installable, importable)
│   ├── main.py         # CLI entry point + pipeline orchestration
│   ├── extract.py      # stages 0-2: safety scan, word extraction, rows
│   ├── classify.py     # stages 3-4: merchant overrides + Clef client
│   ├── questions.py    # Clef question shapes (categories come from data/)
│   ├── config_loader.py
│   ├── validate.py     # stage 5: deterministic checks + reconciliation
│   └── output.py       # stage 6: JSON/CSV/summary writers
├── data/               # tunable data, no code
│   ├── config.json           # personal (gitignored)
│   ├── config.json.example
│   ├── categories.json       # the category taxonomy (gitignored)
│   ├── categories.json.example
│   ├── merchants.json        # known-merchant category overrides (gitignored)
│   ├── merchants.json.example
│   ├── eval.jsonl            # hand-labelled eval set (gitignored)
│   └── eval.jsonl.example
├── statements/         # statement PDFs in, parsed outputs out (gitignored)
└── tests/
```

## Architecture in one paragraph

Extraction is **deterministic** (PyMuPDF word boxes + regex) — Clef is a
*decision* model, so it never extracts fields. Classification is
two-layered: a small curated merchant table (`data/merchants.json`) pins
merchants whose category can't be inferred from the statement text
(truncated names, gateway prefixes, ambiguous brands), and Clef decides
everything else — category, recurring-charge and EMI flags, and
adjudication of rows the deterministic filters can't classify. Clef's
answers carry probabilities and a confidence score; anything below the
threshold lands in a human-review bucket instead of being silently
accepted. Every transaction records whether its category came from the
merchant table or the model (`category_source`).

## Requirements

- [uv](https://docs.astral.sh/uv/) (manages Python and dependencies)
- [Ollama](https://ollama.com) ≥ 0.35.1 for classification

  ```bash
  ollama pull clef          # 27B, ~18GB, needs ~20GB+ RAM
  # or, much faster per call:
  ollama pull clef-flash
  ```

## Setup

```bash
cd hdfc-statement-parser
cp data/config.json.example data/config.json           # then edit model/URL
cp data/categories.json.example data/categories.json   # your category taxonomy
cp data/merchants.json.example data/merchants.json     # your merchant overrides
cp data/eval.jsonl.example data/eval.jsonl             # your labelled eval set
uv sync     # installs the hdfc_parser package (editable) + the hdfc-parser command
```

Everything in `data/` without `.example` is gitignored — those files hold
your personal setup and your real transaction descriptions. The committed
`.example` files show the expected shape. The redactor's personal exact
strings are read from the sibling project, never committed here.

## Usage

```bash
uv run hdfc-parser statements/redacted-statement.pdf
```

Outputs are written beside the input:

- `<stem>.parsed.json` — every transaction with category, probabilities,
  confidence, recurring/EMI flags, review flag, and category provenance
- `<stem>.parsed.csv` — same, flat
- `<stem>_summary.json` — per-category totals, top merchants, recurring
  charges, validation report

Flags:

- `--no-classify` — extraction + validation only, no Ollama needed
  (everything lands in the review bucket)
- `--quiet` — no per-transaction progress lines
- `-o/--csv/--summary` — override output paths

## Classification tuning

All knobs are data files in `data/` — no code changes:

- **`data/categories.json`** (template in `data/categories.json.example`)
  — the category taxonomy: ordered `name -> description` pairs. The
  description is the model's *only* definition of the category, so
  include merchant examples from your statements. Key order matters:
  Clef breaks ties by option order, so keep specific categories first
  and `other` last. This is the right knob when misses are *textually
  inferable* (gateway prefixes hiding a known merchant, bill payments,
  home services).
- **`data/merchants.json`** (template in
  `data/merchants.json.example`) — add a merchant when *you* know better
  than the text (e.g. a truncated name that reads like the wrong
  industry). Matched descriptions skip Clef entirely.

`data/eval.jsonl` (template in `data/eval.jsonl.example`) is the
regression guard: hand-labelled real descriptions from your statements.
The eval test runs the classification path over it and asserts
macro-accuracy ≥ 0.85, printing per-category breakdowns and miss
probabilities on failure — so tuning edits are directed, not guesswork.
It auto-skips (with the eval set missing, or Ollama down), so a fresh
clone passes tests out of the box.

```bash
uv run pytest tests/test_classify.py::TestEval -s   # skips if Ollama is down
```

## Safety

- **Refuses encrypted PDFs** — run the redactor first.
- **Stage 0 leak scan** — re-checks the input against the redactor's
  needles (its `config.json` exact strings + the structured-data regexes).
  If any personal data survives, the parser exits non-zero and refuses to
  produce output. (Skipped with a warning if the redactor's config can't be
  found.)
- Everything runs locally; statements never leave the machine.

## Development

```bash
uv run ruff check .   # lint
uv run pytest          # tests (synthetic PDFs generated at test time;
                       # no personal data is ever written to disk)
```

Test PDFs are drawn by PyMuPDF into pytest's tmp dir at run time, so the
repo stays share-safe by construction.