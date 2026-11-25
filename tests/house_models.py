"""A learned house model for tests of the planning and the API, without fitting one."""

from datetime import UTC, datetime, timedelta
from typing import Any

from sunflow.heat_pump.house import NAMES, Fit, HouseModel, Params

NOW = datetime(2026, 11, 24, 12, tzinfo=UTC)


def house_model(
    u: float = 0.5, kf: float = 3.0, cs: float = 3.0, cb: float = 25.0, g: float = 1.25, *,
    room_hours: int = 200, test_rms_k: float | None = 0.05, stay_rms_k: float | None = 0.3,
    at_bound: tuple[str, ...] = (), kw: float | None = 0.8, **changes: Any,
) -> HouseModel:
    """τ house 50 h, 25 kWh/K, a little internal gain; a fast surface (τ 1 h); usable by default."""
    values: dict[str, Any] = {
        "fit": Fit(
            params=Params(u=u, kf=kf, cs=cs, cb=cb, g=g), offset=2.0, spread=dict.fromkeys(NAMES, 0.0),
            at_bound=list(at_bound), rms_k=0.05, room_hours=room_hours,
        ),
        "kw": kw, "test_rms_k": test_rms_k, "stay_rms_k": stay_rms_k, "computed_at": NOW,
        "surface_c": 23.0, "room_c": 21.0, "outside_c": 2.0, "data_end": NOW, "heating_hours": 100.0,
        "outside_min_c": -3.0, "outside_max_c": 8.0, "data_start": NOW - timedelta(days=10),
        "share": 0.8, "offset_k": 1.5,
    }
    return HouseModel(**(values | changes))
