"""Simulated Homevolt battery (local HTTP API) for trying Sunflow's profile in a dev Home Assistant.

Serves what the core `homevolt` integration reads and writes:
    GET  /ems.json, /schedule.json
    POST /params.json (k=settings_local, v=true|false: local mode, not stored)
    POST /console.json (cmd=sched_set <mode> [-s setpoint] ...)

Without local mode the battery runs self-consumption; with it the manual schedule applies:
0 idle, 1 inverter charge and 2 inverter discharge at the setpoint, 7 solar charge only.
Every write is printed.

    python tools/sim/homevolt.py --host 127.0.0.6 --port 8081
then add the Homevolt integration in Home Assistant with host "127.0.0.6:8081".
"""

import argparse
import asyncio
from datetime import datetime
import shlex
from typing import Any

from aiohttp import web
from model import SimBattery, site_now

ECU_ID = 40580137858664
TICK_S = 5.0
IDLE, INVERTER_CHARGE, INVERTER_DISCHARGE, SOLAR_CHARGE, SELF_CONSUMPTION = 0, 1, 2, 7, 8


class Homevolt:
    """State of the simulated device."""

    def __init__(self, soc: float) -> None:
        self.battery = SimBattery(13300, soc, 6000, 6000)
        self.local_mode = False
        self.mode = SELF_CONSUMPTION
        self.params: dict[str, int] = {}
        self.grid_w = 0.0

    def console(self, command: str) -> str:
        parts = shlex.split(command)
        if len(parts) < 2 or parts[0] != "sched_set":
            return "unknown command"
        flags = {"-s": "setpoint", "-c": "max_charge", "-d": "max_discharge", "-l": "import_limit", "-x": "export_limit"}
        params: dict[str, int] = {}
        for flag, value in zip(parts[2::2], parts[3::2], strict=False):
            if flag in flags:
                params[flags[flag]] = int(float(value))
        self.mode, self.params = int(parts[1]), params
        self.update(0)
        return "ok"

    def update(self, seconds: float) -> None:
        pv, load = site_now(peak_pv_w=3000)
        battery = self.battery
        mode = self.mode if self.local_mode else SELF_CONSUMPTION
        setpoint = float(self.params.get("setpoint", 0))
        if mode == INVERTER_CHARGE:
            power = battery.charge(setpoint)
        elif mode == INVERTER_DISCHARGE:
            power = battery.discharge(setpoint)
        elif mode == SOLAR_CHARGE:
            power = battery.charge(max(pv - load, 0.0))
        elif mode == IDLE:
            power = 0.0
        else:
            power = battery.self_consumption(pv, load)
        battery.step(power, seconds)
        self.grid_w = load - pv - power

    def ems(self) -> dict[str, Any]:
        b = self.battery
        phase = {"l1": 2300, "l2": 2310, "l3": 2290, "l1_l2": 4000, "l2_l3": 4010, "l3_l1": 3990}
        return {
            "ems": [
                {
                    "ecu_id": ECU_ID,
                    "ems_voltage": phase,
                    "ems_current": {"l1": 0, "l2": 0, "l3": 0},
                    "ems_data": {"sys_temp": 180, "power": round(b.power_w), "frequency": 50.0, "soc_avg": round(b.soc_pct * 100)},
                    "ems_aggregate": {"imported_kwh": round(b.charged_wh / 1000, 2), "exported_kwh": round(b.discharged_wh / 1000, 2)},
                    "ems_prediction": {
                        "avail_ch_pwr": b.max_charge_w,
                        "avail_di_pwr": b.max_discharge_w,
                        "avail_ch_energy": round(b.capacity_wh - b.soc_wh),
                        "avail_di_energy": round(b.soc_wh),
                    },
                    "bms_data": [{"soc": round(b.soc_pct * 100), "tmin": 180, "tmax": 210, "cycle_count": 21}],
                }
            ],
            "sensors": [
                {
                    "available": True,
                    "sensor_type": "grid",
                    "function": "",
                    "euid": "a46dd4fffea2361e",
                    "phase": [{"voltage": 230, "amp": round(self.grid_w / 690, 1), "power": round(self.grid_w / 3)}] * 3,
                    "energy_imported": 0,
                    "energy_exported": 0,
                }
            ],
        }

    def schedule(self) -> dict[str, Any]:
        if not self.local_mode:
            return {"local_mode": False, "schedule_id": "Default", "schedule": [{"type": SELF_CONSUMPTION, "params": {}}]}
        return {"local_mode": True, "schedule_id": "Manual Schedule", "schedule": [{"type": self.mode, "params": self.params}]}


def make_app(device: Homevolt) -> web.Application:
    async def ems(request: web.Request) -> web.Response:
        return web.json_response(device.ems())

    async def schedule(request: web.Request) -> web.Response:
        return web.json_response(device.schedule())

    async def params(request: web.Request) -> web.Response:
        form = await request.post()
        if form.get("k") == "settings_local":
            device.local_mode = form.get("v") == "true"
            if not device.local_mode:
                device.mode, device.params = SELF_CONSUMPTION, {}
        print(f"{datetime.now():%H:%M:%S} params {dict(form)}", flush=True)
        return web.Response(text="ok")

    async def console(request: web.Request) -> web.Response:
        form = await request.post()
        answer = device.console(str(form.get("cmd", "")))
        print(f"{datetime.now():%H:%M:%S} console {form.get('cmd')!r} -> {answer}", flush=True)
        return web.Response(text=answer)

    app = web.Application()
    app.router.add_get("/ems.json", ems)
    app.router.add_get("/schedule.json", schedule)
    app.router.add_post("/params.json", params)
    app.router.add_post("/console.json", console)
    return app


async def run(host: str, port: int, soc: float) -> None:
    device = Homevolt(soc)
    runner = web.AppRunner(make_app(device))
    await runner.setup()
    await web.TCPSite(runner, host, port).start()
    print(f"Homevolt simulator on http://{host}:{port} (ECU {ECU_ID})", flush=True)
    while True:
        await asyncio.sleep(TICK_S)
        device.update(TICK_S)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.6")
    parser.add_argument("--port", type=int, default=8081)
    parser.add_argument("--soc", type=float, default=60.0)
    args = parser.parse_args()
    asyncio.run(run(args.host, args.port, args.soc))
