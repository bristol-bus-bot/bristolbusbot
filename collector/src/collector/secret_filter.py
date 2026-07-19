"""Prevent query-string credentials from ever reaching process logs.

BODS requires its API key in the request URL. Requests/urllib3 may include
that URL in retry and connection-error records before our own exception
handler runs, so redaction belongs on the root logging handlers.
"""
from __future__ import annotations

import logging
import re


_SECRET_QUERY_VALUE = re.compile(
    r"(?i)([?&](?:api[_-]?key|access[_-]?token|token)=)[^&\s]+"
)


def redact_query_secrets(value: object) -> str:
    return _SECRET_QUERY_VALUE.sub(r"\1[REDACTED]", str(value))


class QuerySecretFilter(logging.Filter):
    """Rewrite a formatted record before any handler emits it."""

    def filter(self, record: logging.LogRecord) -> bool:
        rendered = record.getMessage()
        redacted = redact_query_secrets(rendered)
        if redacted != rendered:
            record.msg = redacted
            record.args = ()
        return True


def install_query_secret_filter() -> None:
    root = logging.getLogger()
    for handler in root.handlers:
        if not any(isinstance(item, QuerySecretFilter) for item in handler.filters):
            handler.addFilter(QuerySecretFilter())
