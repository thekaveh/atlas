"""Access-log safeguards for credentials allowed on WebSocket query strings."""

from __future__ import annotations

import logging
from urllib.parse import unquote_plus


def _redact_apikey_query_values(path: str) -> str:
    path_only, separator, query = path.partition("?")
    if not separator:
        return path

    redacted_components: list[str] = []
    for component in query.split("&"):
        raw_name, value_separator, _raw_value = component.partition("=")
        if value_separator and unquote_plus(raw_name) == "apikey":
            component = f"{raw_name}=***"
        redacted_components.append(component)
    return f"{path_only}?{'&'.join(redacted_components)}"


def _redact_uvicorn_path_arg(record: logging.LogRecord) -> None:
    if not isinstance(record.args, tuple):
        return
    if record.name == "uvicorn.access":
        path_index = 2
    elif record.name == "uvicorn.error" and '"WebSocket %s"' in str(record.msg):
        path_index = 1
    else:
        return
    if len(record.args) <= path_index or not isinstance(record.args[path_index], str):
        return

    args = list(record.args)
    args[path_index] = _redact_apikey_query_values(args[path_index])
    record.args = tuple(args)


class _APIKeyRedactionFilter(logging.Filter):
    """Redact only ``apikey`` query values from Uvicorn log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        _redact_uvicorn_path_arg(record)
        return True


def configure_uvicorn_access_log_redaction() -> None:
    """Install one credential-redaction filter on each Uvicorn request logger."""
    for logger_name in ("uvicorn.access", "uvicorn.error"):
        logger = logging.getLogger(logger_name)
        if not any(
            isinstance(log_filter, _APIKeyRedactionFilter)
            for log_filter in logger.filters
        ):
            logger.addFilter(_APIKeyRedactionFilter())
