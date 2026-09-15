"""
daq_device_sim — simulates a remote CM7 DAQ acquisition device publishing H2
sensor channels over MQTT, at full protocol parity with the real CM7 (see
DataAcquisition/tests/simulator/cm7_simulator.py, the protocol-faithful
external stand-in this class mirrors): its own broker connection, retained
`status`/LWT, and the `config/set`/`config/get`/`config/ack` handshake.

    DataAcquisition/{location}/{deviceId}/{sensorName}
        {"pin": <int>, "type": "voltage", "raw_v": <float>, "ts": <int>}
    DataAcquisition/{location}/{deviceId}/status        "online"/"offline", retained
    DataAcquisition/{location}/{deviceId}/config/set    <- dashboard -> device
    DataAcquisition/{location}/{deviceId}/config/get    <-> device <-> dashboard
    DataAcquisition/{location}/{deviceId}/config/ack    -> device -> dashboard

Two independent instances represent two physical DAQ boxes (e.g. one per lab
zone), each with its own deviceId and, by default, 8 channels H2-1..H2-8.
Values are reported as a 0.5-4.5 V reading (matching
DataAcquisition/CM7/CM7.ino's own publishVoltage()), random-walked around a
baseline "clean air" voltage with occasional simulated noise, and can be
individually forced to a "leak" level by the interactive console. This value
generation is unchanged from before protocol parity work — only how the
class behaves as an MQTT device changed.

Purely a wire-format simulator: these readings are informational only and
have NO effect on the kitchen state machine. On real hardware kitchen.ino
subscribes to this device's topic tree only to detect that the remote CM7 is
alive (liveness, not value) — see kitchen.ino's TOPIC_DAQ_KITCHEN comment.
LOCAL_SENSOR_THRESHOLD is driven exclusively by the kitchen PLC's own local
A0602 current sensors (KitchenCoreSim.sensor_counts, set directly by the
console/scenario engine) — never by this class.

A device only publishes while `powered` is True, mirroring the real
sensors/power command the kitchen PLC sends
(KitchenControl/{id}/sensors/power) — the interactive sim wires that
topic to DaqDeviceSim.set_powered() so the two DAQ boxes only come alive
when the simulated kitchen PLC turns them on for a leak-test run.
"""

from __future__ import annotations

import json
import math
import random
import threading
import time

try:
    import paho.mqtt.client as mqtt
except ImportError:  # pragma: no cover - broker-free callers never hit this
    mqtt = None

DEFAULT_SENSOR_COUNT = 8
BASELINE_V = 0.5           # clean-air live-zero of the 0.5-4.5V loop
FULLSCALE_V = 4.5
DEFAULT_INTERVAL_MS = 500
_CONNECT_RETRY_ATTEMPTS = 3
_CONNECT_RETRY_BACKOFF_S = 1.0

# problems.txt Area D1: "Concentration should increase gradually, and
# decrease fast once ventilation starts." These are the DEFAULTS for a plain
# DaqDeviceSim (DAQ-1-like); SimRuntime constructs DAQ-2 with a lower ceiling
# and a slower rise (see runtime.DAQ2_AUTO_LEAK_KWARGS) so it visibly lags
# DAQ-1's own auto-leak curve. Purely informational, like the rest of this
# class — never fed into KitchenCore's own danger check.
AUTO_LEAK_MAX_V_DEFAULT = 3.5
AUTO_LEAK_RISE_MS_DEFAULT = 30_000
# The fall time constant at FULL fan, not a fixed dump-to-zero duration — the
# decay is exponential and fan-scaled (see _auto_leak_value_locked). Mirrors
# kitchen_core_sim's AUTO_LEAK_FALL_MS / AUTO_LEAK_MIN_FAN_PCT /
# AUTO_LEAK_SETTLE_FRAC so both sensor paths clear the room at the same rate.
AUTO_LEAK_FALL_MS_DEFAULT = 5_000
AUTO_LEAK_MIN_FAN_PCT = 5.0
AUTO_LEAK_SETTLE_FRAC = 0.02


def _default_sensors() -> list[dict]:
    return [{"pin": i, "name": f"H2-{i + 1}", "type": "voltage"} for i in range(DEFAULT_SENSOR_COUNT)]


class DaqDeviceSim:
    def __init__(self, device_id: str, location: str = "Kitchen",
                 host: str = "localhost", port: int = 1883,
                 user: str = "", password: str = "", transport=None,
                 auto_leak_max_v: float = AUTO_LEAK_MAX_V_DEFAULT,
                 auto_leak_rise_ms: int = AUTO_LEAK_RISE_MS_DEFAULT,
                 auto_leak_fall_ms: int = AUTO_LEAK_FALL_MS_DEFAULT):
        self.device_id = device_id
        self.location = location
        self.host = host
        self.port = port
        self._user = user
        self._password = password
        self.topic_prefix = f"DataAcquisition/{location}/{device_id}"

        self.powered = False
        self.online = True

        # Active sensor config — updated by an inbound config/set.
        self._sensors: list[dict] = _default_sensors()
        self._interval_ms: int = DEFAULT_INTERVAL_MS
        self._config_lock = threading.Lock()

        # per-pin state: baseline V + an optional forced override V, grown
        # lazily as pins are seen (config or force_leak/ramp on a new pin).
        self._baseline_v: dict[int, float] = {}
        self._forced_v: dict[int, float] = {}
        # pin -> (start_v, end_v, start_time_s, duration_s). Real wall-clock
        # time (time.monotonic()), not simulated — scenarios keep ramps
        # <=30s, so this stays fast enough to run in CI/interactively.
        self._ramps: dict[int, tuple[float, float, float, float]] = {}
        self._lock = threading.Lock()

        # Automatic leak drive (problems.txt Area D1) — see set_leak_active().
        # Constructor kwargs so SimRuntime can give DAQ-2 a lower/slower curve
        # than DAQ-1 without a second subclass.
        self.auto_leak_max_v = auto_leak_max_v
        self.auto_leak_rise_ms = auto_leak_rise_ms
        self.auto_leak_fall_ms = auto_leak_fall_ms
        self._leak_active = False
        # Fan speed last reported by SimRuntime, driving the fall rate. 100 =
        # full purge; the default matters only if set_leak_active() is called
        # without one (older callers/tests), which keeps the previous behaviour.
        self._fan_speed_pct = 100.0
        # pin -> (phase_start_s, phase_start_v) for the CURRENT leak-active
        # state, rebuilt on every set_leak_active() edge from wherever the
        # pin's auto-driven value actually was — never touches a pin forced/
        # ramped by force_leak()/ramp(), which always take priority.
        self._auto_leak_phase: dict[int, tuple[float, float]] = {}

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        # transport=<object> uses it as the publish target and never
        # connects — keeps broker-free callers (run_scenario.py's
        # _NullClient, the webapp bridge, unit-test fakes) working exactly
        # as before.
        self._external_transport = transport
        self._client = transport
        if transport is None:
            self._build_client()

    # --- connection --------------------------------------------------------
    def _build_client(self) -> None:
        if mqtt is None:
            raise RuntimeError("paho-mqtt is required unless transport= is given")
        # client_id is NOT "opta-{device_id}": that would collide with a real
        # cm7_simulator.py using the same device id, and MQTT brokers kick
        # the older session on a duplicate client id, producing an endless
        # reconnect fight between the two sims.
        client_id = f"kitchen-daq-sim-{self.device_id}"
        try:
            self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
        except AttributeError:
            self._client = mqtt.Client(client_id=client_id)

        if self._user:
            self._client.username_pw_set(self._user, self._password)

        self._client.will_set(f"{self.topic_prefix}/status", payload="offline", qos=1, retain=True)
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message

    def _on_connect(self, client, userdata, flags, reason_code, properties=None) -> None:
        client.publish(f"{self.topic_prefix}/status", "online", qos=1, retain=True)
        client.subscribe(f"{self.topic_prefix}/config/set", qos=1)
        client.subscribe(f"{self.topic_prefix}/config/get", qos=1)
        self._publish_config_ack()
        self._request_config()

    def _on_message(self, client, userdata, msg) -> None:
        topic = msg.topic
        payload = msg.payload.decode(errors="replace")
        if topic == f"{self.topic_prefix}/config/set":
            self._handle_config_set(payload)
        elif topic == f"{self.topic_prefix}/config/get":
            self._publish_config_ack()

    def _connect_with_retry(self) -> None:
        """Bounded retry with backoff, then give up and carry on
        unconnected — the sim stays usable for local-sensor work even if the
        broker is down. Unlike cm7_simulator's endless retry-with-stdin-
        prompt, SimRuntime.__init__ calls daq.start() before starting its
        own tick thread, so an unbounded blocking connect would hang
        gui_app.py at startup with no output."""
        backoff = _CONNECT_RETRY_BACKOFF_S
        for attempt in range(1, _CONNECT_RETRY_ATTEMPTS + 1):
            try:
                self._client.connect(self.host, self.port, keepalive=30)
                self._client.loop_start()
                return
            except Exception as exc:
                print(f"[DAQ:{self.device_id}] connect attempt {attempt}/{_CONNECT_RETRY_ATTEMPTS} "
                      f"failed: {exc}")
                if attempt < _CONNECT_RETRY_ATTEMPTS:
                    time.sleep(backoff)
                    backoff *= 2
        print(f"[DAQ:{self.device_id}] giving up connecting to {self.host}:{self.port}; "
              f"continuing unconnected")

    # --- config handshake ---------------------------------------------------
    def _handle_config_set(self, payload: str) -> None:
        try:
            cfg = json.loads(payload)
        except json.JSONDecodeError:
            return
        sensors = cfg.get("sensors", [])
        if not sensors:
            return
        interval_ms = int(cfg.get("interval_ms", self._interval_ms))
        with self._config_lock:
            self._sensors = sensors
            self._interval_ms = interval_ms
        self._publish_config_ack()

    def _publish_config_ack(self) -> None:
        with self._config_lock:
            ack = {
                "device_name": self.device_id,
                "expansions": 0,
                "base_pins": 8,
                "sensors": list(self._sensors),
                "interval_ms": self._interval_ms,
            }
        if self._client is not None:
            self._client.publish(f"{self.topic_prefix}/config/ack", json.dumps(ack), qos=1)

    def _request_config(self) -> None:
        if self._client is not None:
            self._client.publish(f"{self.topic_prefix}/config/get", self.device_id, qos=1)

    # --- console controls ------------------------------------------------
    def set_powered(self, on: bool) -> None:
        with self._lock:
            self.powered = on

    def set_online(self, on: bool) -> None:
        """Simulate the DAQ box itself being unreachable (network cut, crash)."""
        with self._lock:
            self.online = on

    def _baseline_locked(self, pin: int) -> float:
        if pin not in self._baseline_v:
            self._baseline_v[pin] = BASELINE_V + random.uniform(-0.02, 0.02)
        return self._baseline_v[pin]

    def force_leak(self, pin: int, volts: float) -> None:
        """Force one pin to a specific voltage reading, e.g. to simulate a
        leak spike. Pass None via clear_force() to release it back to the
        random walk. Cancels any ramp() in progress on this pin — the two
        are mutually exclusive overrides of the same reading, most-recent
        call wins."""
        with self._lock:
            self._forced_v[pin] = volts
            self._ramps.pop(pin, None)

    def ramp(self, pin: int, from_v: float, to_v: float, duration_s: float) -> None:
        """Move one pin linearly from from_v to to_v over duration_s of REAL
        wall-clock time — a slow leak, not an instant spike. Holds at to_v
        once duration_s has elapsed (does not auto-release back to the
        random walk; call clear_force() for that). Cancels any
        force_leak()/ramp() already in progress on this pin."""
        with self._lock:
            self._forced_v.pop(pin, None)
            self._ramps[pin] = (from_v, to_v, time.monotonic(), max(1e-6, duration_s))

    def clear_force(self, pin: int | None = None) -> None:
        with self._lock:
            if pin is None:
                self._forced_v.clear()
                self._ramps.clear()
            else:
                self._forced_v.pop(pin, None)
                self._ramps.pop(pin, None)

    def _ramp_value_locked(self, pin: int) -> float | None:
        """Current interpolated voltage for an in-progress ramp, or None if
        this pin has no ramp. Caller must hold self._lock."""
        r = self._ramps.get(pin)
        if r is None:
            return None
        from_v, to_v, start_s, duration_s = r
        frac = max(0.0, min(1.0, (time.monotonic() - start_s) / duration_s))
        return from_v + (to_v - from_v) * frac

    # --- automatic leak drive (problems.txt Area D1) ------------------------
    def set_leak_active(self, active: bool, now_s: float | None = None,
                        fan_speed_pct: float | None = None) -> None:
        """Called by SimRuntime as the kitchen state machine enters/leaves
        LEAKING-or-HOLD (HOLD keeps the room's concentration exactly where
        LEAKING left it, fan off — see runtime.py's leak_active) — NOT fed by
        anything a real CM7 would ever do; this is the
        same "informational only" display path the rest of this class is
        (see module docstring). Every pin not currently forced/ramped starts
        moving toward auto_leak_max_v (gradual, over auto_leak_rise_ms) or
        back toward its baseline (fast, over auto_leak_fall_ms) from
        whichever value it actually holds right now — a fresh edge never
        snaps."""
        now = time.monotonic() if now_s is None else now_s
        with self._lock:
            # Track fan speed even on a non-edge call: the fan ramps up as the
            # kitchen moves VENTILATING -> FULLY_VENTILATING while leak_active
            # stays False throughout, so an edge-only update would decay the
            # whole purge at whatever speed happened to be commanded first.
            if fan_speed_pct is not None:
                self._fan_speed_pct = fan_speed_pct
            if active == self._leak_active:
                return
            with self._config_lock:
                pins = [int(s["pin"]) for s in self._sensors]
            # Read each pin's CURRENT value BEFORE flipping _leak_active:
            # _auto_leak_value_locked switches branch on that flag, so reading
            # after the flip returned the decay branch's own answer (the
            # baseline) and re-phased the pin from clean air — the voltage
            # snapped straight down instead of decaying from the peak it had
            # actually reached.
            current_by_pin = {
                pin: self._auto_leak_value_locked(pin, now)
                for pin in pins
                if pin not in self._forced_v and pin not in self._ramps
            }
            self._leak_active = active
            for pin, current in current_by_pin.items():
                self._auto_leak_phase[pin] = (now, current)

    def _auto_leak_value_locked(self, pin: int, now: float) -> float:
        """Current auto-driven voltage for `pin`, ignoring any force/ramp
        override (callers check those first). Caller must hold self._lock.
        Once a fully-decayed phase is read, it is dropped so the pin falls back
        to the plain random walk instead of being silently pinned to the
        auto-leak path forever.

        The rise is linear (a leak feeds the room at a steady rate); the fall
        is exponential and scaled by the fan speed last reported through
        set_leak_active(), matching KitchenCoreSim._auto_leak_fall_tau_ms so
        the DAQ voltages and the local sensors clear the room at the same
        proportional rate instead of both dumping on a fixed 5s deadline."""
        phase = self._auto_leak_phase.get(pin)
        baseline = self._baseline_locked(pin)
        if phase is None:
            return baseline
        phase_start_s, phase_start_v = phase
        if self._leak_active:
            duration_s = max(1e-6, self.auto_leak_rise_ms / 1000.0)
            frac = max(0.0, min(1.0, (now - phase_start_s) / duration_s))
            return phase_start_v + (self.auto_leak_max_v - phase_start_v) * frac

        fan_pct = max(AUTO_LEAK_MIN_FAN_PCT, self._fan_speed_pct)
        tau_s = max(1e-6, (self.auto_leak_fall_ms / 1000.0) * (100.0 / fan_pct))
        above = (phase_start_v - baseline) * math.exp(-(now - phase_start_s) / tau_s)
        if abs(above) <= max(1e-3, abs(phase_start_v - baseline) * AUTO_LEAK_SETTLE_FRAC):
            self._auto_leak_phase.pop(pin, None)
            return baseline
        return baseline + above

    def snapshot(self, now_s: float | None = None) -> list[dict]:
        """Current voltage per active sensor, in config order, for the
        console/GUI status display. Each entry carries at least pin, name
        and volts — pin disambiguates "forced" now that index != pin.
        `now_s` overrides the real clock — used by tests driving the
        auto-leak ramp deterministically; production callers never pass it."""
        now = time.monotonic() if now_s is None else now_s
        with self._config_lock:
            sensors = list(self._sensors)
        with self._lock:
            out = []
            for s in sensors:
                pin = int(s["pin"])
                if pin in self._forced_v:
                    v = self._forced_v[pin]
                else:
                    ramped = self._ramp_value_locked(pin)
                    v = ramped if ramped is not None else self._auto_leak_value_locked(pin, now)
                out.append({"pin": pin, "name": s["name"], "volts": v})
            return out

    # --- publishing loop ---------------------------------------------------
    def start(self) -> None:
        if self._external_transport is None:
            self._connect_with_retry()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._external_transport is None and self._client is not None:
            try:
                self._client.publish(f"{self.topic_prefix}/status", "offline", qos=1, retain=True)
                time.sleep(0.2)
                self._client.loop_stop()
                self._client.disconnect()
            except Exception:
                pass

    def _run(self) -> None:
        while not self._stop.is_set():
            with self._config_lock:
                interval_ms = self._interval_ms
            self._tick()
            time.sleep(max(0.01, interval_ms / 1000.0))

    def _tick(self) -> None:
        with self._config_lock:
            sensors = list(self._sensors)

        with self._lock:
            powered = self.powered
            online = self.online
            if not powered or not online:
                return
            ts_ms = int(time.time() * 1000)
            readings = []
            for s in sensors:
                pin = int(s["pin"])
                if pin in self._forced_v:
                    v = self._forced_v[pin]
                else:
                    ramped = self._ramp_value_locked(pin)
                    if ramped is not None:
                        v = ramped
                    elif self._leak_active or pin in self._auto_leak_phase:
                        # Auto-leak drive owns this pin — see set_leak_active().
                        # No random-walk noise layered on top while it's
                        # active, mirroring how a forced/ramped pin also skips
                        # the walk: a real leak drowns it out anyway, and it
                        # would only make the rise/fall curve noisy to look at.
                        v = self._auto_leak_value_locked(pin, time.monotonic())
                    else:
                        # slow random walk, clamped to the 0.5-4.5V loop range
                        baseline = self._baseline_locked(pin)
                        drift = random.uniform(-0.01, 0.01)
                        baseline = min(FULLSCALE_V, max(BASELINE_V, baseline + drift))
                        self._baseline_v[pin] = baseline
                        v = baseline + random.uniform(-0.005, 0.005)
                readings.append((s, v))

        for s, v in readings:
            pin = int(s["pin"])
            # Hyphen, not underscore — must match the firmware's own naming
            # (kitchen/Kitchen_Settings.h: KITCHEN_LOCAL_SENSOR_NAMES is
            # "H2-1".."H2-6") and everything downstream that's keyed on it
            # (thresholds.py's ^H2-(\d+)$ regex, DataAcquisition calibration
            # rows). An underscore here silently reads back unconverted,
            # since no calibration is ever keyed on "H2_1". The default
            # config still names its sensors this way; a config/set can
            # rename pins arbitrarily.
            sensor_name = s["name"]
            topic = f"{self.topic_prefix}/{sensor_name}"
            payload = json.dumps({"pin": pin, "type": s.get("type", "voltage"),
                                   "raw_v": round(v, 4), "ts": ts_ms})
            if self._client is not None:
                self._client.publish(topic, payload, qos=0, retain=False)
