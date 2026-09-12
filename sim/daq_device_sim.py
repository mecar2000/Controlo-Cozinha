"""
daq_device_sim — simulates a remote CM7 DAQ acquisition device publishing 8
H2 sensor channels over MQTT, matching the wire shape the webapp's
app/mqtt.py._handle_sensor_sample() consumes:

    DataAcquisition/Kitchen/{deviceId}/{sensorName}
        {"pin": <int>, "type": "current", "raw_ma": <float>, "ts": <int>}

Two independent instances represent two physical DAQ boxes (e.g. one per
lab zone), each with its own deviceId and 8 channels H2-1..H2-8. Values are
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
        # sensor_idx -> (start_ma, end_ma, start_time_s, duration_s). Real
        # wall-clock time (time.monotonic()), not simulated — scenarios keep
        # ramps <=30s, so this stays fast enough to run in CI/interactively.
        self._ramps: dict[int, tuple[float, float, float, float]] = {}
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
        random walk. Cancels any ramp() in progress on this channel — the two
        are mutually exclusive overrides of the same reading, most-recent
        call wins."""
        with self._lock:
            self._forced_ma[sensor_idx] = ma
            self._ramps.pop(sensor_idx, None)

    def ramp(self, sensor_idx: int, from_ma: float, to_ma: float, duration_s: float) -> None:
        """Move one channel (0-7) linearly from from_ma to to_ma over
        duration_s of REAL wall-clock time — a slow leak, not an instant
        spike. Holds at to_ma once duration_s has elapsed (does not
        auto-release back to the random walk; call clear_force() for that).
        Cancels any force_leak()/ramp() already in progress on this channel.
        """
        with self._lock:
            self._forced_ma.pop(sensor_idx, None)
            self._ramps[sensor_idx] = (from_ma, to_ma, time.monotonic(), max(1e-6, duration_s))

    def clear_force(self, sensor_idx: int | None = None) -> None:
        with self._lock:
            if sensor_idx is None:
                self._forced_ma.clear()
                self._ramps.clear()
            else:
                self._forced_ma.pop(sensor_idx, None)
                self._ramps.pop(sensor_idx, None)

    def _ramp_value_locked(self, sensor_idx: int) -> float | None:
        """Current interpolated mA for an in-progress ramp, or None if this
        channel has no ramp. Caller must hold self._lock."""
        r = self._ramps.get(sensor_idx)
        if r is None:
            return None
        from_ma, to_ma, start_s, duration_s = r
        frac = max(0.0, min(1.0, (time.monotonic() - start_s) / duration_s))
        return from_ma + (to_ma - from_ma) * frac

    def snapshot(self) -> list[float]:
        """Current mA per channel, for the console status display."""
        with self._lock:
            out = []
            for i in range(SENSOR_COUNT):
                if i in self._forced_ma:
                    out.append(self._forced_ma[i])
                    continue
                ramped = self._ramp_value_locked(i)
                out.append(ramped if ramped is not None else self._baseline_ma[i])
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
                    ramped = self._ramp_value_locked(i)
                    if ramped is not None:
                        ma = ramped
                    else:
                        # slow random walk, clamped to the 4-20 mA loop range
                        drift = random.uniform(-0.03, 0.03)
                        self._baseline_ma[i] = min(FULLSCALE_MA, max(BASELINE_MA, self._baseline_ma[i] + drift))
                        ma = self._baseline_ma[i] + random.uniform(-0.01, 0.01)
                readings.append(ma)

        for i, ma in enumerate(readings):
            # Hyphen, not underscore — must match the firmware's own naming
            # (kitchen/Kitchen_Settings.h: KITCHEN_LOCAL_SENSOR_NAMES is
            # "H2-1".."H2-6") and everything downstream that's keyed on it
            # (thresholds.py's ^H2-(\d+)$ regex, DataAcquisition calibration
            # rows). An underscore here silently reads back unconverted,
            # since no calibration is ever keyed on "H2_1".
            sensor_name = f"H2-{i + 1}"
            topic = f"{self.topic_prefix}/{self.device_id}/{sensor_name}"
            payload = json.dumps({"pin": i, "type": "current", "raw_ma": round(ma, 4), "ts": ts_ms})
            if self.client is not None:
                self.client.publish(topic, payload, qos=0, retain=False)
