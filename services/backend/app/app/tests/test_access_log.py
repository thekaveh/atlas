from __future__ import annotations

import logging

import pytest

from access_log import _APIKeyRedactionFilter, configure_uvicorn_access_log_redaction


@pytest.fixture
def uvicorn_loggers():
    loggers = [logging.getLogger(name) for name in ("uvicorn.access", "uvicorn.error")]
    original_filters = {logger.name: list(logger.filters) for logger in loggers}
    try:
        yield {logger.name: logger for logger in loggers}
    finally:
        for logger in loggers:
            logger.filters[:] = original_filters[logger.name]


@pytest.mark.parametrize(
    ("logger_name", "message", "args", "expected", "secrets"),
    [
        (
            "uvicorn.error",
            '%s - "WebSocket %s" [accepted]',
            (
                ("127.0.0.1", 54321),
                "/key-socket/ws?%61pikey=first'secret&apikey=second-secret"
                "&next=/other?apikey=harmless&trace=accepted",
            ),
            "WebSocket /key-socket/ws?%61pikey=***&apikey=***"
            '&next=/other?apikey=harmless&trace=accepted" [accepted]',
            ("first'secret", "second-secret"),
        ),
        (
            "uvicorn.error",
            '%s - "WebSocket %s" %d',
            (
                ("127.0.0.1", 54322),
                "/key-socket/ws?trace=denied&apikey=credential-secret",
                401,
            ),
            'WebSocket /key-socket/ws?trace=denied&apikey=***" 401',
            ("credential-secret",),
        ),
        (
            "uvicorn.access",
            '%s - "%s %s HTTP/%s" %d',
            (
                "127.0.0.1:54323",
                "GET",
                "/key-socket/http?%61pikey=http'secret&apikey=http-second"
                "&next=/other?apikey=harmless&trace=http",
                "1.1",
                200,
            ),
            "GET /key-socket/http?%61pikey=***&apikey=***"
            '&next=/other?apikey=harmless&trace=http HTTP/1.1" 200',
            ("http'secret", "http-second"),
        ),
    ],
)
def test_uvicorn_request_logs_redact_only_apikey_query_values(
    uvicorn_loggers, logger_name, message, args, expected, secrets
) -> None:
    configure_uvicorn_access_log_redaction()
    record = logging.LogRecord(
        logger_name,
        logging.INFO,
        __file__,
        1,
        message,
        args,
        None,
    )

    for log_filter in uvicorn_loggers[logger_name].filters:
        assert log_filter.filter(record)
    rendered_once = record.getMessage()
    for log_filter in uvicorn_loggers[logger_name].filters:
        assert log_filter.filter(record)
    rendered_twice = record.getMessage()

    assert rendered_twice == rendered_once
    assert all(secret not in rendered_once for secret in secrets)
    assert expected in rendered_once


def test_uvicorn_redaction_filter_installation_is_idempotent(uvicorn_loggers) -> None:
    configure_uvicorn_access_log_redaction()
    configure_uvicorn_access_log_redaction()

    for logger in uvicorn_loggers.values():
        assert sum(
            isinstance(log_filter, _APIKeyRedactionFilter)
            for log_filter in logger.filters
        ) == 1
