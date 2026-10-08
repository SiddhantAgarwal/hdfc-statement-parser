"""Legacy HDFC statement layout (older statements)."""

from hdfc_parser.templates.base import TemplateSpec

LEGACY = TemplateSpec(
    name="legacy",
    date_label="Transaction Date",
    amount_labels=("Amount",),
    rewards_labels=(),
    anchors=("Transaction Date", "Amount"),
    noise_labels=("Transaction Date", "Posting Date", "Description", "Amount"),
    notes=(
        "Older HDFC statements. Table columns: Transaction Date / Posting "
        "Date / Description / Amount, with a CR token marking credits; "
        "no rewards column."
    ),
)
