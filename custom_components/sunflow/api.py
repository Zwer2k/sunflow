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

    async def state(self) -> dict[str, Any]:
        """Plan, control, live values and consumers."""
        return await self.request("GET", "/integration/state")

    async def set_control(
        self,
        enabled: bool | None = None,
        device_id: str | None = None,
        device_enabled: bool | None = None,
    ) -> None:
        """Main switch or one device's switch; switching on is confirmed by the user's action."""
        body = {
            k: v
            for k, v in {
                "enabled": enabled,
                "device_id": device_id,
                "device_enabled": device_enabled,
            }.items()
            if v is not None
        }
        await self.request("PUT", "/control/takeover?confirm=true", body)

    async def set_strategy(
        self, strategy: str | None, until: str | None = None
    ) -> None:
        """Strategy of the home battery until a time (None: until changed), None back to the default."""
        if strategy is None:
            await self.request("DELETE", "/control/strategy")
        else:
            await self.request(
                "PUT", "/control/strategy", {"strategy": strategy, "until": until}
            )

    async def replan(self) -> None:
        """Plan again now."""
        await self.request("POST", "/integration/replan")

    async def set_consumer(
        self, device_id: str, override: dict[str, Any] | None
    ) -> None:
        """Change a consumer's planning for a while (None: back to its settings)."""
        if override is None:
            await self.request("DELETE", f"/control/devices/{device_id}")
        else:
            await self.request("PUT", f"/control/devices/{device_id}", override)

    async def add_load(self, load: dict[str, Any]) -> dict[str, Any]:
        """Place a one-time run; returns it with its start."""
        return await self.request("POST", "/integration/loads", load)

    async def remove_load(self, load_id: str) -> None:
        """Forget a one-time run."""
        await self.request("DELETE", f"/integration/loads/{load_id}")
