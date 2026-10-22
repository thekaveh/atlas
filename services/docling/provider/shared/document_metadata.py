"""Metadata derived from the stored Docling document model."""


def count_formulas(document):
    """Count each stored formula item once, independently of references."""
    from docling_core.types.doc import FormulaItem

    return sum(isinstance(item, FormulaItem) for item in document.texts)
