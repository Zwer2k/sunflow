"""Simulated Indevolt battery (local HTTP API) for trying Sunflow's profile in a dev Home Assistant.

Serves the endpoints the core `indevolt` integration uses on port 8080:
    POST /rpc/Indevolt.GetData?config={"t":[...]}
    POST /rpc/Indevolt.SetData?config={"f":16,"t":...,"v":[...]}
    GET  /rpc/Sys.GetConfig

The battery follows a simple model: PV by time of day, a varying house load, self-consumption
in energy mode 1 and the real-time commands (charge, discharge, stop) in mode 4. Every write
is printed, so what Sunflow sends can be checked.

Run with an interpreter that has aiohttp, e.g. Home Assistant's:
    python tools/sim/indevolt.py --host 127.0.0.5
then add the Indevolt integration in Home Assistant with that host.
"""

import argparse
import asyncio
from datetime import datetime
import json
import math

from aiohttp import web

# Generation 2 (SolidFlex 2000); values from the core integration's test fixture
SERIAL = "SolidFlex2000-SIM00001"
MODEL = "CMS-SF2000"
CAPACITY_WH = 1790.0
MAX_CHARGE_W = 2400.0
TICK_S = 5.0

# Written points and where the device reports them back
READ_BACK = {
    "47005": "7101",  # energy mode
    "1142": "6105",  # discharge limit (emergency SoC)
    "1147": "11011",  # max AC output power
    "1138": "11009",  # inverter input limit
    "1146": "11010",  # feed-in power limit
    "7265": "7171",  # light
    "7266": "680",  # bypass
}
SWITCH_POINTS = {"1143": "2618"}  # grid charging: writes 1/0, reads 1001/1000
REALTIME = "47015"
SELF_CONSUMPTION, REALTIME_MODE = 1, 4
STOP, CHARGE, DISCHARGE = 0, 1, 2


class Battery:
    """State of the simulated device."""

    def __init__(self, soc_pct: float) -> None:
        self.soc_wh = CAPACITY_WH * soc_pct / 100
        self.points: dict[str, object] = {
            "0": SERIAL,
            "606": "1002",
            "7101": SELF_CONSUMPTION,
            "142": CAPACITY_WH / 1000,
            "6105": 5,
            "6107": 1000,
            "6108": 0,
            "6109": 0,
            "2618": 1000,
            "11009": 800,
            "11010": 800,
            "11011": 800,
            "667": 0,
            "6006": 380.0,
            "6007": 338.0,
            "2107": 290.0,
            "2104": 1500.0,
            "2105": 0.0,
            "11034": 0,
            "1502": 0.0,
            "1505": 553673,
            "6004": 0.0,
            "6005": 0.0,
            "7120": 1000,
            "2600": 230,
            "2612": 50.0,
            "11016": 0,
            "9008": "MASTER-SIM00001",
        }
        self.realtime = (STOP, 0, 0)
        self.update(0)

    def write(self, point: str, values: list[int]) -> bool:
        if point == REALTIME:
            action, power, target = (values + [0, 0, 0])[:3]
            self.realtime = (action, power, target)
            self.points["7101"] = REALTIME_MODE
            self.points.update({"6107": {STOP: 1000, CHARGE: 1001, DISCHARGE: 1002}[action], "6108": target, "6109": power})
        elif point in READ_BACK:
            self.points[READ_BACK[point]] = values[0]
        elif point in SWITCH_POINTS:
            self.points[SWITCH_POINTS[point]] = 1001 if values[0] else 1000
        else:
            return False
        self.update(0)
        return True

    def update(self, seconds: float) -> None:
        """Advance the model; battery power + = discharge."""
        hour = datetime.now().hour + datetime.now().minute / 60
        pv = max(0.0, math.sin((hour - 6) / 14 * math.pi)) * 900
        load = 280 + 120 * math.sin(hour / 24 * 4 * math.pi)
        soc_pct = self.soc_wh / CAPACITY_WH * 100
        floor = float(self.points["6105"])
        max_out = float(self.points["11011"])
        if self.points["7101"] == REALTIME_MODE:
            action, power, target = self.realtime
            if action == CHARGE and soc_pct < target:
                battery = -min(power, MAX_CHARGE_W)
            elif action == DISCHARGE and soc_pct > max(target, floor):
                battery = min(power, max_out)
            else:
                battery = 0.0
        else:
            surplus = pv - load
            if surplus > 0:
                battery = -min(surplus, MAX_CHARGE_W) if soc_pct < 100 else 0.0
            else:
                battery = min(-surplus, max_out) if soc_pct > floor else 0.0
        if battery < 0:
            self.soc_wh = min(CAPACITY_WH, self.soc_wh - battery * seconds / 3600)
        else:
            self.soc_wh = max(0.0, self.soc_wh - battery * seconds / 3600)
        ac = pv + battery
        kwh = abs(battery) * seconds / 3_600_000
        if battery < 0:
            self.points["6006"] = round(float(self.points["6006"]) + kwh, 3)
            self.points["6004"] = round(float(self.points["6004"]) + kwh, 3)
        else:
            self.points["6007"] = round(float(self.points["6007"]) + kwh, 3)
            self.points["6005"] = round(float(self.points["6005"]) + kwh, 3)
        self.points.update(
            {
                "6000": round(battery),
                "6001": 1000 if abs(battery) < 1 else (1002 if battery > 0 else 1001),
                "6002": round(self.soc_wh / CAPACITY_WH * 100),
                "9000": round(self.soc_wh / CAPACITY_WH * 100),
                "1501": round(pv),
                "1664": round(pv / 2),
                "1665": round(pv / 2),
                "1666": 0,
                "1667": 0,
                "2108": round(max(0.0, ac)),
                "2101": round(max(0.0, -ac)),
            }
        )


def make_app(battery: Battery) -> web.Application:
    def config(request: web.Request) -> dict:
        return json.loads(request.query.get("config", "{}"))

    async def get_data(request: web.Request) -> web.Response:
        wanted = [str(t) for t in config(request).get("t", [])]
        return web.json_response({t: battery.points[t] for t in wanted if t in battery.points})

    async def set_data(request: web.Request) -> web.Response:
        data = config(request)
        ok = battery.write(str(data["t"]), [int(v) for v in data.get("v", [])])
        print(f"{datetime.now():%H:%M:%S} SetData t={data['t']} v={data.get('v')} -> {ok}", flush=True)
        return web.json_response({"result": ok})

    async def get_config(request: web.Request) -> web.Response:
        return web.json_response({"device": {"sn": SERIAL, "type": MODEL, "mac": "02:00:00:5e:00:01"}})

    app = web.Application()
    app.router.add_post("/rpc/Indevolt.GetData", get_data)
    app.router.add_post("/rpc/Indevolt.SetData", set_data)
    app.router.add_get("/rpc/Sys.GetConfig", get_config)
    return app


async def run(host: str, soc: float) -> None:
    battery = Battery(soc)
    runner = web.AppRunner(make_app(battery))
    await runner.setup()
    await web.TCPSite(runner, host, 8080).start()
    print(f"Indevolt simulator on http://{host}:8080 (SN {SERIAL})", flush=True)
    while True:
        await asyncio.sleep(TICK_S)
        battery.update(TICK_S)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.5")
    parser.add_argument("--soc", type=float, default=50.0)
    args = parser.parse_args()
    asyncio.run(run(args.host, args.soc))
