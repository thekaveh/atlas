"""Access-log safeguards for credentials allowed on WebSocket query strings."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any


_APIKEY_QUERY_VALUE = re.compile(r"([?&]apikey=)[^&\s\"']*")


def _redact_apikey_query_values(value: Any) -> Any:
    if isinstance(value, str):
        return _APIKEY_QUERY_VALUE.sub(r"\1***", value)
    if isinstance(value, tuple):
        return tuple(_redact_apikey_query_values(item) for item in value)
    if isinstance(value, Mapping):
        return {
            key: _redact_apikey_query_values(item) for key, item in value.items()
        }
    return value


class _APIKeyRedactionFilter(logging.Filter):
    """Redact only ``apikey`` query values from Uvicorn log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _redact_apikey_query_values(record.msg)
        record.args = _redact_apikey_query_values(record.args)
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
