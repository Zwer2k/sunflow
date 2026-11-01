"""Only one Sunflow App (stable or Edge) may run at a time."""

from typing import Any

import pytest
from sunflow import integration_link
from sunflow.integration_link import peer_slug, running_peer


@pytest.mark.parametrize(
    ("own", "peer"),
    [
        ("2b15894e_sunflow", "2b15894e_sunflow_edge"),
        ("2b15894e_sunflow_edge", "2b15894e_sunflow"),
        ("local_sunflow", "local_sunflow_edge"),
        ("2b15894e_other", None),
        ("", None),
    ],
)
def test_peer_slug(own: str, peer: str | None) -> None:
    """The other App of the same repository."""
    assert peer_slug(own) == peer


@pytest.mark.parametrize(
    ("peer_info", "expected"),
    [
        pytest.param({"name": "Sunflow Edge", "state": "started"}, "Sunflow Edge", id="running"),
        pytest.param({"name": "Sunflow Edge", "state": "stopped"}, None, id="stopped"),
        pytest.param(None, None, id="not-installed"),
    ],
)
async def test_running_peer(monkeypatch: pytest.MonkeyPatch, peer_info: dict[str, Any] | None, expected: str | None) -> None:
    """The other App counts only while it is started."""
    infos = {"self": {"slug": "2b15894e_sunflow"}, "2b15894e_sunflow_edge": peer_info}

    async def info(token: str, slug: str) -> dict[str, Any] | None:
        return infos[slug]

    monkeypatch.setattr(integration_link, "_addon_info", info)
    assert await running_peer("token") == expected
