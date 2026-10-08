"""HDFC statement template registry.

One TemplateSpec per card family; the engine (hdfc_parser.extract)
iterates REGISTRY to detect and parse pages. Add a family by dropping a
spec module here and adding it to REGISTRY.
"""

from hdfc_parser.templates.base import TemplateSpec
from hdfc_parser.templates.infinia import INFINIA
from hdfc_parser.templates.legacy import LEGACY

# Detection order: the first spec whose header matches a page wins.
REGISTRY: dict[str, TemplateSpec] = {
    spec.name: spec for spec in (INFINIA, LEGACY)
}

__all__ = ["INFINIA", "LEGACY", "REGISTRY", "TemplateSpec"]
