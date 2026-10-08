"""Infinia-family statement layout (current generation)."""

from hdfc_parser.templates.base import TemplateSpec

INFINIA = TemplateSpec(
    name="infinia",
    date_label="DATE & TIME",
    amount_labels=("AMOUNT",),
    rewards_labels=("REWARDS",),
    anchors=("DATE & TIME", "TRANSACTION DESCRIPTION"),
    noise_labels=("DATE & TIME", "TRANSACTION DESCRIPTION", "REWARDS"),
    notes=(
        "Current-generation Infinia-class statements. Table columns: "
        "DATE & TIME / TRANSACTION DESCRIPTION / REWARDS / AMOUNT / PI. "
        "Amounts carry the rupee glyph rendered as 'C' with Indian digit "
        "grouping and are right-aligned; credits are prefixed '+'. No "
        "CR/DR column. Continuation pages can emit text column-major."
    ),
)
