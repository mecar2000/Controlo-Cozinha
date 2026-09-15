"""
One-off script: place the 8 Kitchen-DAQ-1 + 8 Kitchen-DAQ-2 sensors
(problems.txt Area E2), positioned for buoyant H2 physics rather than an
even grid:

  DAQ-1 (pins 0-7 / H2-1..H2-8): clustered near the leak (LEAK_TUBE, under
  the water heater at the back-right) and up toward the ceiling/hood, where
  a buoyant leak actually collects — dense coverage inside
  CONFIDENCE_RANGE_M (2.2m, see lib/interpolation.ts) of both the source
  and each other.

  DAQ-2 (pins 0-7 / H2-1..H2-8): spread far from the leak and low, as a
  "should stay clean" baseline layer — a non-zero DAQ-2 reading is a real
  signal the room has filled past containment, not near-source noise.

Coordinates are against the room frame in lib/roomGeometry.ts (x: 0-2.859
along the back wall, y: 0-2.341 back-to-front, z: 0-2.56 floor-to-ceiling).

NOT wired into app boot. Run by hand once DataAcquisition has actually
registered "KITCHEN-DAQ-1" and "KITCHEN-DAQ-2" (GET /devices must list
them) — sensor_config rows are only meaningful pointers into DAQ's own pin
map (see routes/sensors.py::_derive_daq_sensor_name); this script uses that
same lookup, so a sensor whose device/pin DAQ doesn't yet report is skipped
with a warning rather than silently saved nameless:

    cd webapp && python scripts/seed_kitchen_daq_sensors.py

Idempotent: upsert_sensor keyed on sensor_key, safe to re-run.
"""

import app.daq as daq
from app.db.sensor_config import upsert_sensor
from app.routes.sensors import _derive_daq_sensor_name

# (sensor_key, label, x, y, z, device_id, pin)
LAYOUT = [
    # --- Kitchen-DAQ-1: leak-side, upper room ---
    ("daq1-1", "DAQ1 above leak",        2.70, 0.20, 2.40, "KITCHEN-DAQ-1", 0),
    ("daq1-2", "DAQ1 ceiling mid",       2.40, 0.30, 2.45, "KITCHEN-DAQ-1", 1),
    ("daq1-3", "DAQ1 hood throat",       2.00, 0.25, 2.45, "KITCHEN-DAQ-1", 2),
    ("daq1-4", "DAQ1 hood far edge",     1.60, 0.30, 2.40, "KITCHEN-DAQ-1", 3),
    ("daq1-5", "DAQ1 back-right mid",    2.60, 0.60, 2.10, "KITCHEN-DAQ-1", 4),
    ("daq1-6", "DAQ1 hood mouth level",  2.20, 0.15, 1.90, "KITCHEN-DAQ-1", 5),
    ("daq1-7", "DAQ1 back-right corner", 2.75, 1.00, 2.30, "KITCHEN-DAQ-1", 6),
    ("daq1-8", "DAQ1 ceiling right-ctr", 1.90, 0.60, 2.20, "KITCHEN-DAQ-1", 7),
    # --- Kitchen-DAQ-2: far side, low room ---
    ("daq2-1", "DAQ2 front-left corner", 0.30, 1.80, 0.30, "KITCHEN-DAQ-2", 0),
    ("daq2-2", "DAQ2 back-left floor",   0.30, 0.50, 0.30, "KITCHEN-DAQ-2", 1),
    ("daq2-3", "DAQ2 front-centre low",  1.40, 2.00, 0.40, "KITCHEN-DAQ-2", 2),
    ("daq2-4", "DAQ2 front-right low",   2.50, 1.90, 0.30, "KITCHEN-DAQ-2", 3),
    ("daq2-5", "DAQ2 left wall mid",     0.20, 1.20, 0.90, "KITCHEN-DAQ-2", 4),
    ("daq2-6", "DAQ2 centre-back low",   1.20, 0.60, 0.30, "KITCHEN-DAQ-2", 5),
    ("daq2-7", "DAQ2 front-left mid",    0.60, 2.10, 1.30, "KITCHEN-DAQ-2", 6),
    ("daq2-8", "DAQ2 right-centre low",  2.00, 1.70, 0.50, "KITCHEN-DAQ-2", 7),
]


def main() -> None:
    # Same lookup the sensor editor route uses (routes/sensors.py) — a
    # sensor whose device/pin DataAcquisition doesn't yet report gets no
    # name there, so it gets skipped here instead of saved nameless.
    placed, skipped = 0, 0
    for sensor_key, label, x, y, z, device_id, pin in LAYOUT:
        daq_sensor_name = _derive_daq_sensor_name(device_id, pin)
        if daq_sensor_name is None:
            print(f"SKIP {sensor_key}: {device_id} pin {daq.pin_label(pin)} not "
                  f"reported by DataAcquisition yet (GET /devices) — commission it there first")
            skipped += 1
            continue
        upsert_sensor(
            sensor_key, label=label, x=x, y=y, z=z, enabled=True,
            daq_device_id=device_id, daq_sensor_name=daq_sensor_name, daq_pin=pin,
        )
        placed += 1
        print(f"OK   {sensor_key} -> {device_id} pin {pin} ({daq_sensor_name}) @ ({x}, {y}, {z})")

    print(f"\n{placed} placed, {skipped} skipped")


if __name__ == "__main__":
    main()
