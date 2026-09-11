"""
daq_device_sim — simulates a remote CM7 DAQ acquisition device publishing 8
H2 sensor channels over MQTT, matching the wire shape the webapp's
app/mqtt.py._handle_sensor_sample() consumes:

    DataAcquisition/Kitchen/{deviceId}/{sensorName}
        {"value": <float>, "unit": "mA", "ts_ms": <int>}

Two independent instances represent two physical DAQ boxes (e.g. one per
lab zone), each with its own deviceId and 8 channels H2_1..H2_8. Values are
reported as a 4-20 mA current-loop reading (matching the firmware's own
A0602 sensors), random-walked around a baseline "clean air" current with
occasional simulated noise, and can be individually forced to a "leak"
level by the interactive console.

A device only publishes while `powered` is True, mirroring the real
sensors/power command the kitchen PLC sends
(KitchenControl/{id}/sensors/power) — the interactive sim wires that
topic to DaqDeviceSim.set_powered() so the two DAQ boxes only come alive
when the simulated kitchen PLC turns them on for a leak-test run.
"""

from __future__ import annotations

import json
import random
import threading
import time

SENSOR_COUNT = 8
BASELINE_MA = 4.0          # clean-air live-zero of a 4-20 mA loop
FULLSCALE_MA = 20.0
PUBLISH_HZ = 2.0


class DaqDeviceSim:
    def __init__(self, mqtt_client, device_id: str, topic_prefix: str = "DataAcquisition/Kitchen"):
        self.client = mqtt_client
        self.device_id = device_id
        self.topic_prefix = topic_prefix
        self.powered = False
        self.online = True
        # per-sensor state: baseline mA + an optional forced override mA
        self._baseline_ma = [BASELINE_MA + random.uniform(-0.05, 0.05) for _ in range(SENSOR_COUNT)]
        self._forced_ma: dict[int, float] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # --- console controls ------------------------------------------------
    def set_powered(self, on: bool) -> None:
        with self._lock:
            self.powered = on

    def set_online(self, on: bool) -> None:
        """Simulate the DAQ box itself being unreachable (network cut, crash)."""
        with self._lock:
            self.online = on

    def force_leak(self, sensor_idx: int, ma: float) -> None:
        """Force one channel (0-7) to a specific mA reading, e.g. to simulate
        a leak spike. Pass None via clear_force() to release it back to the
        random walk."""
        with self._lock:
            self._forced_ma[sensor_idx] = ma

    def clear_force(self, sensor_idx: int | None = None) -> None:
        with self._lock:
            if sensor_idx is None:
                self._forced_ma.clear()
            else:
                self._forced_ma.pop(sensor_idx, None)

    def snapshot(self) -> list[float]:
        """Current mA per channel, for the console status display."""
        with self._lock:
            out = []
            for i in range(SENSOR_COUNT):
                out.append(self._forced_ma.get(i, self._baseline_ma[i]))
            return out

    # --- publishing loop ---------------------------------------------------
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        period = 1.0 / PUBLISH_HZ
        while not self._stop.is_set():
            self._tick()
            time.sleep(period)

    def _tick(self) -> None:
        with self._lock:
            powered = self.powered
            online = self.online
            if not powered or not online:
                return
            ts_ms = int(time.time() * 1000)
            readings = []
            for i in range(SENSOR_COUNT):
                if i in self._forced_ma:
                    ma = self._forced_ma[i]
                else:
                    # slow random walk, clamped to the 4-20 mA loop range
                    drift = random.uniform(-0.03, 0.03)
                    self._baseline_ma[i] = min(FULLSCALE_MA, max(BASELINE_MA, self._baseline_ma[i] + drift))
                    ma = self._baseline_ma[i] + random.uniform(-0.01, 0.01)
                readings.append(ma)

        for i, ma in enumerate(readings):
            sensor_name = f"H2_{i + 1}"
            topic = f"{self.topic_prefix}/{self.device_id}/{sensor_name}"
            payload = json.dumps({"value": round(ma, 4), "unit": "mA", "ts_ms": ts_ms})
            if self.client is not None:
                self.client.publish(topic, payload, qos=0, retain=False)
