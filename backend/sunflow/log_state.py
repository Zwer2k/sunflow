"""Lasting conditions in the App's log: a warning when one starts, an info when it is over.

Planning runs every quarter hour and retries what failed; without this every run would
repeat the same warning, or nothing would be logged at all.
"""

import json
import logging
from pathlib import Path
from typing import Any


class Conditions:
    """Failures by key, each logged once until it works again."""

    def __init__(self, logger: logging.Logger) -> None:
        """Log to logger."""
        self._logger = logger
        self._active: dict[str, str] = {}

    def failed(self, key: str, message: str, *args: Any) -> None:
        """Something failed: logged as a warning unless it already is."""
        if key in self._active:
            return
        self._active[key] = message % args if args else message
        self._logger.warning(message, *args)

    def ok(self, key: str) -> None:
        """It works (again): logged as an info if it had failed."""
        if (message := self._active.pop(key, None)) is not None:
            self._logger.info("Works again: %s", message)


_STATE_FILES = Conditions(logging.getLogger("sunflow.state"))


def load_json(path: Path, default: Any) -> Any:
    """A state file of the App; default when it is missing (first start) or unreadable (cut off by a crash).

    An unreadable file is logged once: what it kept (e.g. the EEPROM writes of today) starts empty.
    """
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return default
    except ValueError as err:
        _STATE_FILES.failed(str(path), "%s is unreadable, starting from empty: %s", path.name, err)
        return default
    _STATE_FILES.ok(str(path))
    return data
