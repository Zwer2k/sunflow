"""The App's recent log messages in memory, so they can be read without access to the host's log."""

from collections import deque
from datetime import UTC, datetime
import logging
import traceback
from typing import Any

# Enough for days of warnings; info messages push them out sooner
MAX_RECORDS = 3000
MAX_TRACEBACK_CHARS = 4000


class LogBuffer(logging.Handler):
    """Keeps the last log records from INFO up; lost on restart."""

    def __init__(self) -> None:
        """Start empty."""
        super().__init__(logging.INFO)
        self._records: deque[dict[str, Any]] = deque(maxlen=MAX_RECORDS)

    def emit(self, record: logging.LogRecord) -> None:
        """Store one record with its traceback, if any."""
        try:
            entry = {
                "time": datetime.fromtimestamp(record.created, UTC).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            }
        except Exception:  # noqa: BLE001 - a broken format string must not break logging
            self.handleError(record)
            return
        if record.exc_info and record.exc_info[1] is not None:
            entry["traceback"] = "".join(traceback.format_exception(*record.exc_info))[-MAX_TRACEBACK_CHARS:]
        self._records.append(entry)

    def entries(self, level: int, since: datetime, limit: int) -> list[dict[str, Any]]:
        """The newest records at or above a level since a moment, oldest first."""
        cutoff = since.isoformat()
        selected = [
            r for r in self._records if logging.getLevelName(r["level"]) >= level and r["time"] >= cutoff
        ]
        return selected[-limit:]


LOG_BUFFER = LogBuffer()
