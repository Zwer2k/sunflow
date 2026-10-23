"""Runtime settings for App and standalone mode."""

from dataclasses import dataclass
import os
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[1]


@dataclass(frozen=True, kw_only=True)
class Settings:
    """Backend settings."""

    ha_url: str
    ha_token: str
    port: int
    data_dir: Path
    profiles_dir: Path
    schema_dir: Path
    frontend_dir: Path

    @property
    def ha_websocket_url(self) -> str:
        """Return the websocket URL of Home Assistant."""
        base = self.ha_url.rstrip("/")
        if base.startswith("http"):
            base = "ws" + base.removeprefix("http")
        # The Supervisor proxy serves the websocket at /core/websocket
        if base == "ws://supervisor/core":
            return f"{base}/websocket"
        return f"{base}/api/websocket"


def load_settings() -> Settings:
    """Load settings, preferring the Supervisor environment when present."""
    # Variables already set in the environment take precedence over .env
    load_dotenv(BACKEND_DIR / ".env")

    if supervisor_token := os.environ.get("SUPERVISOR_TOKEN"):
        ha_url = "http://supervisor/core"
        ha_token = supervisor_token
    else:
        ha_url = os.environ.get("SUNFLOW_HA_URL", "http://localhost:8123")
        ha_token = os.environ["SUNFLOW_HA_TOKEN"]

    repo_root = BACKEND_DIR.parent
    return Settings(
        ha_url=ha_url,
        ha_token=ha_token,
        port=int(os.environ.get("SUNFLOW_PORT", "8099")),
        data_dir=Path(os.environ.get("SUNFLOW_DATA_DIR", repo_root / "data")),
        profiles_dir=Path(
            os.environ.get("SUNFLOW_PROFILES_DIR", repo_root / "profiles")
        ),
        schema_dir=Path(os.environ.get("SUNFLOW_SCHEMA_DIR", repo_root / "schema")),
        frontend_dir=Path(
            os.environ.get("SUNFLOW_FRONTEND_DIR", repo_root / "frontend")
        ),
    )
