"""Tests for the candidates of the learning sources."""

from sunflow.config.models import DeviceConfig, SunflowConfig
from sunflow.devices.discovery import Snapshot
from sunflow.planning.learning_sources import learning_candidates


def _sensor(entity_id: str, name: str, device_class: str | None, unit: str, state_class: str | None = None) -> dict:
    attributes = {"friendly_name": name, "device_class": device_class, "unit_of_measurement": unit}
    if state_class:
        attributes["state_class"] = state_class
    return {"entity_id": entity_id, "state": "5.6", "attributes": attributes}


def test_candidates_by_device_class_outdoor_first() -> None:
    """Sensors grouped by kind; names pointing outside come first, the inverter's own ones are left out."""
    states = [
        _sensor("sensor.wohnzimmer_temperature", "Wohnzimmer Temperatur", "temperature", "°C", "measurement"),
        _sensor("sensor.garten_klima_temperature", "Garten Klima Temperatur", "temperature", "°C", "measurement"),
        _sensor("sensor.sofar_module_temperature", "Sofar Module Temperature", "temperature", "°C", "measurement"),
        _sensor("sensor.sofar_pack_temperature", "Sofar Battery Pack Temperature", "temperature", "°C", "measurement"),
        _sensor("sensor.station_helligkeit", "Wetterstation Helligkeit", "illuminance", "lx"),
        _sensor("sensor.garten_klima_humidity", "Garten Klima Feuchte", "humidity", "%", "measurement"),
        # Without device class: by unit
        _sensor("sensor.station_pv_temperatur_sud", "Wetterstation PV-Temperatur Süd", None, "°C"),
        _sensor("sensor.station_regen_stunde", "Wetterstation Regenmenge Stunde", None, "mm"),
        _sensor("sensor.station_akku", "Wetterstation Akku", None, "%"),
        # An optimizer below the inverter: its temperature is the module's
        _sensor("sensor.opt_temperatur", "JV Temperatur", "temperature", "°C", "measurement"),
    ]
    snapshot = Snapshot(
        entries=[],
        devices=[
            {"id": "inverter"},
            {"id": "pack", "via_device_id": "inverter"},
            {"id": "opt", "via_device_id": "inverter", "identifiers": [["huawei_solar", "JV1"]]},
        ],
        entities=[
            {"entity_id": "sensor.sofar_module_temperature", "device_id": "inverter", "platform": "solax_modbus"},
            {"entity_id": "sensor.sofar_pack_temperature", "device_id": "pack", "platform": "solax_modbus"},
            {"entity_id": "sensor.opt_temperatur", "unique_id": "JV1_temperature", "device_id": "opt", "platform": "huawei_solar"},
            {"entity_id": "sensor.opt_output_power", "unique_id": "JV1_output_power", "device_id": "opt", "platform": "huawei_solar"},
            {"entity_id": "sensor.opt_input_voltage", "unique_id": "JV1_input_voltage", "device_id": "opt", "platform": "huawei_solar"},
        ],
        states={s["entity_id"]: s for s in states},
        energy_prefs=None,
    )
    config = SunflowConfig(
        devices=[DeviceConfig(id="sofar", name="Sofar", profile="solax_modbus/sofar", profile_version=1, ha_device_id="inverter")]
    )

    candidates = learning_candidates(config, snapshot)

    assert [c["entity_id"] for c in candidates["temperature"]] == [
        "sensor.garten_klima_temperature",
        "sensor.station_pv_temperatur_sud",
        "sensor.opt_temperatur",
        "sensor.wohnzimmer_temperature",
    ]
    assert [c["entity_id"] for c in candidates["irradiance"]] == ["sensor.station_helligkeit"]
    assert candidates["irradiance"][0]["state_class"] is None
    assert [c["entity_id"] for c in candidates["humidity"]] == ["sensor.garten_klima_humidity"]
    assert [c["entity_id"] for c in candidates["precipitation"]] == ["sensor.station_regen_stunde"]
