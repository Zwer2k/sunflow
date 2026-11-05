"""Simulated Marstek Venus E (v1/v2 register map) over Modbus TCP, for Sunflow's profile in a dev Home Assistant.

Answers reads (function 3 and 4) and writes (6 and 16) like the battery behind an RS485 gateway,
as the marstek_modbus custom integration expects; registers it does not know read 0.
Without RS485 control the battery runs self-consumption (anti-feed); with it, the force mode
(standby, charge, discharge) at the set charge or discharge power. Every write is printed.

    python tools/sim/marstek.py --host 127.0.0.7 --port 5020
then add "Marstek Venus Modbus" in Home Assistant with that host and port, model Venus E v1/v2.
"""

import argparse
import asyncio
from datetime import datetime
import struct

from model import SimBattery, site_now

TICK_S = 5.0
RS485_ON, RS485_OFF = 21930, 21947
STANDBY, CHARGE, DISCHARGE = 0, 1, 2

# Venus E v1/v2 (e_v12.yaml of the integration)
SOC = 32104
TOTAL_ENERGY = 32105
BATTERY_POWER = 32102  # int32, + = charging (assumed)
AC_POWER = 32202  # int32, + = output to the house (assumed)
CHARGED = 33000  # uint32, 0.01 kWh
DISCHARGED = 33002
RS485 = 42000
FORCE_MODE = 42010
CHARGE_TO_SOC = 42011
CHARGE_POWER = 42020
DISCHARGE_POWER = 42021
WORK_MODE = 43000


class Venus:
    """Register map and battery state."""

    def __init__(self, soc: float) -> None:
        self.battery = SimBattery(5120, soc, 2500, 2500)
        self.registers: dict[int, int] = {
            RS485: RS485_OFF, FORCE_MODE: STANDBY, CHARGE_TO_SOC: 100, CHARGE_POWER: 0, DISCHARGE_POWER: 0, WORK_MODE: 1,
        }
        self.update(0)

    def _put32(self, address: int, value: int) -> None:
        high, low = struct.unpack(">HH", struct.pack(">i", value))
        self.registers[address], self.registers[address + 1] = high, low

    def write(self, address: int, values: list[int]) -> None:
        for offset, value in enumerate(values):
            self.registers[address + offset] = value
        self.update(0)

    def update(self, seconds: float) -> None:
        pv, load = site_now(peak_pv_w=0)
        b = self.battery
        if self.registers[RS485] == RS485_ON:
            mode = self.registers[FORCE_MODE]
            if mode == CHARGE:
                power = b.charge(self.registers[CHARGE_POWER], self.registers[CHARGE_TO_SOC])
            elif mode == DISCHARGE:
                power = b.discharge(self.registers[DISCHARGE_POWER], 12)
            else:
                power = 0.0
        else:
            power = b.self_consumption(pv, load, 12)
        b.step(power, seconds)
        self.registers[SOC] = round(b.soc_pct)
        self.registers[TOTAL_ENERGY] = round(b.capacity_wh)
        self._put32(BATTERY_POWER, round(-power))
        self._put32(AC_POWER, round(power))
        self._put32(CHARGED, round(b.charged_wh / 10))
        self._put32(DISCHARGED, round(b.discharged_wh / 10))


async def serve(venus: Venus, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            header = await reader.readexactly(7)
            transaction, protocol, length, unit = struct.unpack(">HHHB", header)
            pdu = await reader.readexactly(length - 1)
            function = pdu[0]
            if function in (3, 4):
                address, count = struct.unpack(">HH", pdu[1:5])
                values = [venus.registers.get(address + i, 0) & 0xFFFF for i in range(count)]
                body = struct.pack(">BB", function, 2 * count) + struct.pack(f">{count}H", *values)
            elif function == 6:
                address, value = struct.unpack(">HH", pdu[1:5])
                venus.write(address, [value])
                print(f"{datetime.now():%H:%M:%S} write {address} = {value}", flush=True)
                body = pdu[:5]
            elif function == 16:
                address, count = struct.unpack(">HH", pdu[1:5])
                values = list(struct.unpack(f">{count}H", pdu[6 : 6 + 2 * count]))
                venus.write(address, values)
                print(f"{datetime.now():%H:%M:%S} write {address} = {values}", flush=True)
                body = pdu[:5]
            else:
                body = struct.pack(">BB", function | 0x80, 1)
            writer.write(struct.pack(">HHHB", transaction, protocol, len(body) + 1, unit) + body)
            await writer.drain()
    except (asyncio.IncompleteReadError, ConnectionResetError):
        pass
    finally:
        writer.close()


async def run(host: str, port: int, soc: float) -> None:
    venus = Venus(soc)
    server = await asyncio.start_server(lambda r, w: serve(venus, r, w), host, port)
    print(f"Marstek Venus E simulator on {host}:{port}", flush=True)
    async with server:
        while True:
            await asyncio.sleep(TICK_S)
            venus.update(TICK_S)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.7")
    parser.add_argument("--port", type=int, default=5020)
    parser.add_argument("--soc", type=float, default=70.0)
    args = parser.parse_args()
    asyncio.run(run(args.host, args.port, args.soc))
