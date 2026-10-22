"""Source intent shared by endpoint previews; this does not assert live health."""


def source_has_endpoint(source: str) -> bool:
    """Whether a selected source permits an endpoint to be advertised."""
    return source.strip().lower() not in {'none', 'disabled'}
