"""The key of the MCP server: created on request, shown once, stored only as a hash."""

from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import secrets

KEY_FILE = "mcp_key.json"
# 0.68.0 kept the key itself; a key shown in the settings any time is gone with the hash
LEGACY_FILE = "mcp_token"


def _digest(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


class McpKey:
    """At most one key; without one the MCP server is closed."""

    def __init__(self, data_dir: Path) -> None:
        """Load the hash of the current key, if one was created."""
        self._path = data_dir / KEY_FILE
        (data_dir / LEGACY_FILE).unlink(missing_ok=True)
        try:
            self._stored: dict[str, str] | None = json.loads(self._path.read_text())
        except FileNotFoundError, ValueError:
            self._stored = None

    @property
    def created(self) -> str | None:
        """When the current key was created (ISO 8601); None without a key."""
        return self._stored["created"] if self._stored else None

    def matches(self, authorization: str) -> bool:
        """Whether an Authorization header carries the current key."""
        if self._stored is None or not authorization.startswith("Bearer "):
            return False
        return secrets.compare_digest(_digest(authorization.removeprefix("Bearer ")), self._stored["sha256"])

    def create(self) -> str:
        """A new key, replacing the old one; only the caller ever sees it."""
        key = secrets.token_urlsafe(32)
        stored = {"sha256": _digest(key), "created": datetime.now(UTC).isoformat()}
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(stored))
        self._path.chmod(0o600)
        self._stored = stored
        return key

    def revoke(self) -> None:
        """Close the MCP server until a new key is created."""
        self._path.unlink(missing_ok=True)
        self._stored = None
