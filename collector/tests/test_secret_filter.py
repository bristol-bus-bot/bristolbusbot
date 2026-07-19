import logging

from collector.secret_filter import (
    QuerySecretFilter,
    install_query_secret_filter,
    redact_query_secrets,
)


def test_redacts_bods_key_from_request_error():
    secret = "this-must-never-reach-journald"
    error = (
        "HTTPSConnectionPool: /datafeed/?boundingBox=x&api_key="
        f"{secret}&other=1"
    )
    rendered = redact_query_secrets(error)
    assert secret not in rendered
    assert "api_key=[REDACTED]&other=1" in rendered


def test_logging_filter_redacts_formatted_arguments():
    secret = "also-secret"
    record = logging.LogRecord(
        "urllib3.connectionpool",
        logging.WARNING,
        __file__,
        1,
        "retrying %s",
        (f"https://example.invalid/?api_key={secret}",),
        None,
    )
    assert QuerySecretFilter().filter(record)
    assert secret not in record.getMessage()
    assert "[REDACTED]" in record.getMessage()


def test_filter_installation_is_idempotent():
    root = logging.getLogger()
    handler = logging.StreamHandler()
    root.addHandler(handler)
    try:
        install_query_secret_filter()
        install_query_secret_filter()
        assert sum(isinstance(item, QuerySecretFilter)
                   for item in handler.filters) == 1
    finally:
        root.removeHandler(handler)
