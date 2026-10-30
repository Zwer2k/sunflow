"""The link to the Sunflow integration in Home Assistant: its access token and Supervisor discovery."""

import logging
from pathlib import Path
import secrets
import socket

import aiohttp

_LOGGER = logging.getLogger(__name__)

DISCOVERY_SERVICE = "sunflow_ems"


def integration_token(data_dir: Path) -> str:
    """Token the integration authenticates with; created once and kept in the data directory."""
    path = data_dir / "integration_token"
    try:
        if token := path.read_text().strip():
            return token
    except FileNotFoundError:
        pass
    token = secrets.token_urlsafe(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token)
    path.chmod(0o600)
    return token


def app_host() -> str:
    """Hostname of this App on the Supervisor network (e.g. "local-sunflow")."""
    return socket.gethostname()


async def announce(supervisor_token: str, port: int, token: str) -> None:
    """Tell the Supervisor where the integration finds the App; Home Assistant then offers to set it up."""
    payload = {"service": DISCOVERY_SERVICE, "config": {"host": app_host(), "port": port, "token": token}}
    try:
        async with (
            aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session,
            session.post(
                "http://supervisor/discovery", json=payload, headers={"Authorization": f"Bearer {supervisor_token}"}
            ) as response,
        ):
            if response.status >= 400:
                _LOGGER.warning("Supervisor discovery refused (%s): %s", response.status, await response.text())
    except (aiohttp.ClientError, TimeoutError) as err:
        _LOGGER.warning("Supervisor discovery failed: %s", err)
