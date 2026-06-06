"""Structured NEXO product knowledge.

The catalog in this package is intentionally small, schema-checked and
source-linked. It complements the live system catalog; it is not a replacement
for live backend state.
"""

from .catalog import (
    answer_product_question,
    catalog_entries_for_system_catalog,
    explain_capability,
    find_capabilities,
    list_capabilities,
    load_product_catalog,
    surface_status,
    validate_catalog,
)

__all__ = [
    "answer_product_question",
    "catalog_entries_for_system_catalog",
    "explain_capability",
    "find_capabilities",
    "list_capabilities",
    "load_product_catalog",
    "surface_status",
    "validate_catalog",
]
