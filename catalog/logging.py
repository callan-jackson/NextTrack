"""Structured JSON logging for production environments."""

import json
import logging
import traceback
from datetime import datetime, timezone


class JSONFormatter(logging.Formatter):
    """Format log records as single-line JSON objects (JSON Lines).

    Each log line includes:
    * timestamp  -- ISO-8601 UTC
    * level      -- e.g. INFO, WARNING, ERROR
    * module     -- Python module that emitted the log
    * message    -- The formatted log message
    * request_id -- From the RequestIDFilter (falls back to ``-``)
    * logger     -- Logger name

    On exceptions the ``exception`` key contains the full traceback string.
    """

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            'timestamp': datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            'level': record.levelname,
            'module': record.module,
            'message': record.getMessage(),
            'request_id': getattr(record, 'request_id', '-'),
            'logger': record.name,
        }

        if record.exc_info and record.exc_info[0] is not None:
            log_entry['exception'] = self.formatException(record.exc_info)

        return json.dumps(log_entry, default=str)
