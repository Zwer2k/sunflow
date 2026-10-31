"""HTTP API of the backend (below /api/v1) and the app that serves it."""

from . import (
    analysis,
    control,
    device_test,
    devices,
    direct,
    integration,
    live,
    optimizers,
    plan,
    setup,
    sources,
    system,
    vehicles,
)
from .app import create_app
from .common import router

for _area in (system, integration, direct, devices, device_test, setup, sources, plan, vehicles, control, analysis, live, optimizers):
    router.include_router(_area.router)

__all__ = ["create_app", "router"]
