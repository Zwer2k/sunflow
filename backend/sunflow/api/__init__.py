"""HTTP API of the backend (below /api/v1) and the app that serves it."""

from . import (
    analysis,
    control,
    device_test,
    devices,
    direct,
    live,
    plan,
    setup,
    sources,
    system,
    vehicles,
)
from .app import create_app
from .common import router

for _area in (system, direct, devices, device_test, setup, sources, plan, vehicles, control, analysis, live):
    router.include_router(_area.router)

__all__ = ["create_app", "router"]
