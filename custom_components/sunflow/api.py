"""Client for the HTTP API of the Sunflow App."""

from typing import Any

import aiohttp

from .const import API_VERSION


class SunflowError(Exception):
    """The App could not be reached or answered with an error."""


class SunflowAuthError(SunflowError):
    """The App refused the token."""


class SunflowClient:
    """Requests to one Sunflow App."""

    def __init__(
        self, session: aiohttp.ClientSession, url: str, token: str | None
    ) -> None:
        """Initialize the client."""
        self._session = session
        self._base = f"{url.rstrip('/')}/api/{API_VERSION}"
        self._headers = {"Authorization": f"Bearer {token}"} if token else {}

    async def request(self, method: str, path: str, json: Any = None) -> Any:
        """Call the API and return the decoded answer."""
        try:
            async with self._session.request(
                method,
                f"{self._base}{path}",
                json=json,
                headers=self._headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                if response.status in (401, 403):
                    raise SunflowAuthError(f"{path}: access refused")
                response.raise_for_status()
                return await response.json()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise SunflowError(f"{path}: {err}") from err

    async def health(self) -> dict[str, Any]:
        """Version and connection state; works without token."""
        return await self.request("GET", "/health")

    async def forecasts(self) -> dict[str, Any]:
        """The learned PV forecasts (per plane, plant and total) by hour."""
        return await self.request("GET", "/forecast/export")

    async def string_energy(self) -> dict[str, Any]:
        """Energy and power per PV string."""
        return await self.request("GET", "/strings/energy")
