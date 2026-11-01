"""The link to the Sunflow integration in Home Assistant: its access token and Supervisor discovery."""

import logging
from pathlib import Path
import secrets
import socket
from typing import Any

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


async def host_port(supervisor_token: str, port: int) -> int | None:
    """Host port the App's port is mapped to in its network settings; None when not mapped."""
    try:
        async with (
            aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session,
            session.get(
                "http://supervisor/addons/self/info", headers={"Authorization": f"Bearer {supervisor_token}"}
            ) as response,
        ):
            if response.status >= 400:
                return None
            network = (await response.json()).get("data", {}).get("network") or {}
    except (aiohttp.ClientError, TimeoutError, ValueError) as err:
        _LOGGER.warning("App info not available: %s", err)
        return None
    return network.get(f"{port}/tcp")


# The stable App and Sunflow Edge come from the same repository; only one may run
APP_SLUGS = ("sunflow", "sunflow_edge")


def peer_slug(own: str) -> str | None:
    """Slug of the other Sunflow App of the same repository, e.g. "2b15894e_sunflow" → "2b15894e_sunflow_edge"."""
    repository, _, name = own.partition("_")
    if name not in APP_SLUGS:
        return None
    other = APP_SLUGS[1] if name == APP_SLUGS[0] else APP_SLUGS[0]
    return f"{repository}_{other}"


async def _addon_info(supervisor_token: str, slug: str) -> dict[str, Any] | None:
    try:
        async with (
            aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session,
            session.get(
                f"http://supervisor/addons/{slug}/info", headers={"Authorization": f"Bearer {supervisor_token}"}
            ) as response,
        ):
            if response.status >= 400:
                return None
            return (await response.json()).get("data")
    except (aiohttp.ClientError, TimeoutError, ValueError) as err:
        _LOGGER.debug("Info of App %s not available: %s", slug, err)
        return None


async def running_peer(supervisor_token: str) -> str | None:
    """Name of the other Sunflow App when it is running; None if not (or not installed)."""
    own = await _addon_info(supervisor_token, "self")
    if not own or (slug := peer_slug(own.get("slug", ""))) is None:
        return None
    peer = await _addon_info(supervisor_token, slug)
    return peer.get("name", slug) if peer and peer.get("state") == "started" else None
