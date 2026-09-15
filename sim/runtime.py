"""
runtime — shared orchestration for the Kitchen simulator: wires
KitchenCoreSim + two DaqDeviceSim instances to a live MQTT connection,
exactly mirroring the wire protocol kitchen.ino/Protocol.cpp use (see
docs/KitchenCore-and-Protocol.md).

Both the CLI console (simulate_kitchen.py) and the web GUI (gui_app.py)
build one SimRuntime and drive it the same way, so there is exactly one
place that knows the MQTT topic names and payload shapes.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import paho.mqtt.client as mqtt
from dotenv import load_dotenv

# sim/.env overrides the SIM_* timing env vars below without having to
# export them every session — see sim/.env.example. override=False (the
# default, made explicit here) so a real env var set by the caller/CI always
# wins over a stale .env value.
load_dotenv(Path(__file__).resolve().parent / ".env", override=False)

from daq_device_sim import DaqDeviceSim
from local_sensor_publish import (
    KITCHEN_LOCAL_SENSOR_NAMES,
    KITCHEN_LOCAL_SENSOR_PINS,
    build_local_sensor_payload,
    local_sensor_topic,
)
from kitchen_core_sim import (
    ARM_TIMEOUT_MS,
    FULLY_VENT_MIN_HOLD_MS,
    HOLD_MAX_DURATION_MS_DEFAULT,
    SENSOR_WARMUP_MS,
    KitchenState,
    RunSpec,
    StartRejectReason,
    StopCondition,
    KitchenCoreSim,
    ma_to_counts,
    now_ms,
)

TICK_HZ = 5.0
# Matches kitchen/kitchen.ino's STATE_HEARTBEAT_MS. Was 5000, which made the
# simulator 5x less responsive than the firmware it stands in for: elapsedMs
# is excluded from the discrete-change key below, so it stayed frozen for a
# full 5s between heartbeats and any client-side interpolation of it visibly
# lurched. Keep these two in step.
STATE_HEARTBEAT_MS = 1000

# How often the control PLC republishes its own six local H2 sensors.
# kitchen/Kitchen_Settings.h's SENSOR_PUBLISH_INTERVAL_MS round-robins ONE
# sensor per interval to spread the blocking publishes across loop passes;
# the sim has no such constraint (no blocking I2C, no single-threaded safety
# loop to protect) so it publishes all six together on this interval instead.
# The historian sees the same topics at the same rate either way.
LOCAL_SENSOR_PUBLISH_MS = 1000


def _timing_env(name: str, default_ms: int) -> int:
    """Read a state-machine timing override from the environment, in
    milliseconds. Lets interactive/scripted testing run the 5-minute purge
    and 70s sensor warm-up in ~10s instead, without touching the real
    firmware's Kitchen_Settings.h (problems.txt: "make fixed timings ...
    small for now ... tests iterate quicker"). Unset = real-hardware default.
    """
    raw = os.getenv(name, "").strip()
    if not raw:
        return default_ms
    try:
        return int(float(raw) * 1000)
    except ValueError:
        print(f"[SIM] Ignoring non-numeric {name}={raw!r}, using default {default_ms}ms")
        return default_ms


# Env var names take seconds (SIM_FULLY_VENT_HOLD_S=10), not ms, to match how
# an operator would actually type them on a command line.
SIM_ARM_TIMEOUT_MS = _timing_env("SIM_ARM_TIMEOUT_S", ARM_TIMEOUT_MS)
SIM_FULLY_VENT_MIN_HOLD_MS = _timing_env("SIM_FULLY_VENT_HOLD_S", FULLY_VENT_MIN_HOLD_MS)
SIM_SENSOR_WARMUP_MS = _timing_env("SIM_SENSOR_WARMUP_S", SENSOR_WARMUP_MS)
SIM_HOLD_MAX_DURATION_MS = _timing_env("SIM_HOLD_MAX_DURATION_S", HOLD_MAX_DURATION_MS_DEFAULT)

# problems.txt Area D1: DAQ-2 registers lower and slower than DAQ-1 — see
# DaqDeviceSim.set_leak_active(). Plain dicts (not a dataclass) so they can be
# splatted straight into DaqDeviceSim(**kwargs) both here and in tests that
# assert the wiring itself (test_runtime_wires_daq2_lower_and_slower_than_daq1).
DAQ1_AUTO_LEAK_KWARGS = {"auto_leak_max_v": 3.5, "auto_leak_rise_ms": 30_000, "auto_leak_fall_ms": 5_000}
DAQ2_AUTO_LEAK_KWARGS = {"auto_leak_max_v": 2.5, "auto_leak_rise_ms": 45_000, "auto_leak_fall_ms": 5_000}


def build_client(host: str, port: int, user: str, password: str, client_id: str) -> mqtt.Client:
    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
    except AttributeError:
        client = mqtt.Client(client_id=client_id)
    if user:
        client.username_pw_set(user, password)
    client.connect(host, port, keepalive=30)
    return client


class KitchenSim:
    """MQTT-facing wrapper around KitchenCoreSim — parses inbound `cmd`
    payloads into RunSpec, drives the core, and publishes state/ack/alarm/
    sensors-power/run exactly like kitchen.ino does."""

    def __init__(self, client: mqtt.Client, device_id: str, experiment_name: str, lab_id: str,
                 daq_device_id: str = "mainBoard", daq_location: str = "Kitchen"):
        self.client = client
        self.device_id = device_id
        self.experiment_name = experiment_name
        self.lab_id = lab_id
        # The control PLC publishes its OWN sensors under a SEPARATE identity
        # from its control topics: KITCHEN_DAQ_DEVICE_ID ("mainBoard") in the
        # DataAcquisition sensor namespace vs KITCHEN_DEVICE_ID ("KITCHEN-01")
        # for KitchenControl/... — two deliberately distinct namespaces
        # (kitchen/Kitchen_Settings.h, webapp/app/config.py).
        self.daq_device_id = daq_device_id
        self.daq_location = daq_location
        self.core = KitchenCoreSim(
            arm_timeout_ms=SIM_ARM_TIMEOUT_MS,
            fully_vent_min_hold_ms=SIM_FULLY_VENT_MIN_HOLD_MS,
            sensor_warmup_ms=SIM_SENSOR_WARMUP_MS,
            hold_max_duration_ms=SIM_HOLD_MAX_DURATION_MS,
        )

        self.topic_state = f"KitchenControl/{device_id}/state"
        self.topic_ack = f"KitchenControl/{device_id}/ack"
        self.topic_alarm = f"status/{experiment_name}/{device_id}/alarm/{lab_id}/hydrogen"
        self.topic_sensors_power = f"KitchenControl/{device_id}/sensors/power"
        self.topic_online = f"status/{experiment_name}/{device_id}/online"
        self.topic_run = f"status/{experiment_name}/{device_id}/run"
        self.topic_cmd = f"KitchenControl/{device_id}/cmd"
        self.topic_permit = f"safety/permit/{device_id}"

        self._last_state_change_key = None
        self._last_state_pub_ms = None
        self._last_remote_on = None
        self._last_alarm_on = None
        self._last_run_running = None
        self._last_local_sensor_pub_ms = None
        self._last_transition = ""
        self.lock = threading.RLock()

        # populated by the caller so remote sensors/power commands can
        # arm/disarm the DAQ sims.
        self.daq_devices: list[DaqDeviceSim] = []

    # -- MQTT command handling (mirrors kitchen.ino::onMqttMessage) --------
    def handle_cmd(self, payload: str) -> None:
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            print("[SIM] bad cmd JSON, ignored")
            return
        cmd = data.get("cmd")
        now = now_ms()
        with self.lock:
            if cmd == "start":
                spec = self._parse_spec(data)
                run_id = data.get("runId", "")
                spec.run_id = run_id
                requested_pct = (
                    ((data.get("spec") or {}).get("leakStop") or {}).get("sensorQuorum") or {}
                ).get("thresholdPct", 0.0)
                rej = self.core.start(spec, now)
                self._publish_ack(run_id, rej, requested_pct=float(requested_pct or 0.0))
            elif cmd == "confirm":
                run_id = data.get("runId", "")
                rej = self.core.confirm(run_id, now)
                self._publish_ack(run_id, rej)
            elif cmd == "stop":
                self.core.stop(now)
            elif cmd == "ack":
                self.core.human_ack(now)
            else:
                print(f"[SIM] unknown cmd={cmd!r}, ignored")

    def _parse_spec(self, data: dict) -> RunSpec:
        spec_in = data.get("spec") or {}
        spec = RunSpec()
        spec.gas_setpoint_pct = max(0.0, min(100.0, float(spec_in.get("gasSetpointPct", 0.0))))

        def stop_from(o: dict | None, allow_inventory: bool) -> StopCondition:
            o = o or {}
            sc = StopCondition()
            sc.max_duration_ms = max(0, int(o.get("maxDurationMs", 0) or 0))
            if allow_inventory:
                sc.max_inventory_ml = max(0.0, float(o.get("maxInventory_mL", 0.0) or 0.0))
            q = o.get("sensorQuorum") or {}
            sc.quorum_count = max(0, int(q.get("quorumCount", 0) or 0))
            if sc.quorum_count > 0:
                pct = float(q.get("thresholdPct", 0.0) or 0.0)
                # Firmware-owned %->counts mapping (Protocol.cpp): with placeholder
                # scales this collapses to a fixed live-zero; the sim instead maps
                # linearly onto the local A0602 current sensors' 4-20mA loop
                # (4mA=0%, 20mA=100%) via ma_to_counts(), the one place that
                # mapping lives (see kitchen_core_sim.py). Unrelated to
                # DaqDeviceSim's voltage publishing: on real hardware the
                # remote DAQ never feeds the local danger check (kitchen.ino
                # subscribes to it only for liveness).
                ma = 4.0 + (pct / 100.0) * 16.0
                counts = ma_to_counts(ma)
                sc.quorum_threshold_pct = counts  # field reused to carry counts
            return sc

        spec.leak_stop = stop_from(spec_in.get("leakStop"), allow_inventory=True)
        spec.hold_stop = stop_from(spec_in.get("holdStop"), allow_inventory=False)
        if spec.hold_stop.max_duration_ms == 0:
            spec.hold_stop.max_duration_ms = self.core.hold_max_duration_ms
        spec.vent_stop = stop_from(spec_in.get("ventStop"), allow_inventory=False)

        vr = spec_in.get("ventRegisters") or {}
        spec.vent_registers = {
            "central": bool(vr.get("central", False)),
            "exhaust": bool(vr.get("exhaust", False)),
            "inlet": bool(vr.get("inlet", False)),
        }
        spec.fan_speed_pct = max(0.0, min(100.0, float(spec_in.get("fanSpeedPct", 0.0))))
        spec.valid = True
        return spec

    def handle_permit(self, payload: str) -> None:
        present = len(payload) > 0
        value = ('"permit":true' in payload) or ('"permit": true' in payload)
        with self.lock:
            self.core.permit_present = present
            self.core.permit_value = value

    def handle_peer_alarm(self, topic: str, payload: str, self_topic_marker: str) -> None:
        if self_topic_marker in topic:
            return
        active = ('"danger":true' in payload) or ('"active":true' in payload)
        with self.lock:
            self.core.peer_alarm_active = active

    # -- outbound publishes --------------------------------------------------
    def _publish_ack(self, run_id: str, rej: StartRejectReason, requested_pct: float = 0.0) -> None:
        accepted = rej == StartRejectReason.NONE
        spec = self.core.spec
        # Echo the quorum the sim actually adopted into the acked spec itself
        # (not just the sibling quorumInterpreted block below) — SpecDiff.tsx
        # reads acked.leakStop.sensorQuorum to show "N sensors at X %v/v" in
        # the review table, and previously always saw nothing there, i.e.
        # "0 sensors at 0 %v/v" (problems.txt: "why didn't it change the
        # quorum?"). counts -> % is the inverse of stop_from()'s % -> counts.
        interpreted_counts = spec.leak_stop.quorum_threshold_pct if spec.leak_stop.quorum_count > 0 else 0
        acked_pct = ((interpreted_counts / 4095.0) * 100.0) if spec.leak_stop.quorum_count > 0 else 0.0
        payload = {
            "runId": run_id,
            "valid": accepted,
            "spec": {
                "gasSetpointPct": spec.gas_setpoint_pct,
                "fanSpeedPct": spec.fan_speed_pct,
                "leakStop": {"maxDurationMs": spec.leak_stop.max_duration_ms,
                             "maxInventory_mL": spec.leak_stop.max_inventory_ml,
                             "sensorQuorum": {"quorumCount": spec.leak_stop.quorum_count,
                                              "thresholdPct": round(acked_pct, 3)}},
                "holdStop": {"maxDurationMs": spec.hold_stop.max_duration_ms},
                "ventStop": {"maxDurationMs": spec.vent_stop.max_duration_ms},
                "ventRegisters": spec.vent_registers,
            },
            "quorumInterpreted": {
                "requestedPct": requested_pct,
                "interpretedCounts": int(spec.leak_stop.quorum_threshold_pct),
            },
        }
        if not accepted:
            payload["rejection"] = rej.value
        self.client.publish(self.topic_ack, json.dumps(payload), qos=1, retain=False)
        print(f"[SIM] ack -> runId={run_id} accepted={accepted} rejection={rej.value if not accepted else '-'}")

    def publish_online(self) -> None:
        self.client.publish(self.topic_online, "online", qos=1, retain=True)

    def tick_and_publish(self) -> None:
        now = now_ms()
        with self.lock:
            prev_state = self.core.state
            self.core.update(now)
            state = self.core.state
            role_leak = self.core.role_is_leak_test
            ack_required = self.core.ack_required
            acked = self.core.acked
            reason = self.core.reason
            sensors_on = self.core.local_sensors_on
            elapsed = self.core.elapsed_ms(now)
            inventory = self.core.delivered_inventory_ml
            remote_on = self.core.remote_sensors_on()
            alarm_on = self.core.alarm_on()
            run_id = self.core.spec.run_id
            clear_for_ms = self.core.clear_for_ms(now)
            clear_required_ms = self.core.fully_vent_min_hold_ms
            local_sensor_counts = dict(self.core.sensor_counts)
            # HOLD keeps the room's concentration where LEAKING left it (fan
            # is off, nothing vents it) — only VENTILATING should start the
            # DAQ voltages falling. See kitchen_core_sim.py's
            # _apply_auto_leak_ramp for the same fix on local sensors.
            leak_active = state in (KitchenState.LEAKING, KitchenState.HOLD)
            fan_speed_pct = round(self.core.fan_speed_pct(), 1)
            registers = self.core.registers()
            flow_rate_mLps = round(self.core.flow_rate_mLps(), 2)

        if prev_state != state:
            self._last_transition = f"{prev_state.value} -> {state.value} (reason={reason.value})"
            print(f"[SIM] state {self._last_transition}")

        # run topic (server.py listens to this)
        running = state not in (KitchenState.WAITING, KitchenState.ARMED)
        if running != self._last_run_running:
            self._last_run_running = running
            self.client.publish(
                self.topic_run,
                json.dumps({"running": running, "runId": run_id}),
                qos=1, retain=True,
            )

        # state topic — discrete-change or heartbeat, matches kitchen.ino's gate.
        # elapsedMs/clearForMs/clearRequiredMs tick or vary continuously, so
        # they're excluded from the change-detection key (always included in
        # the payload sent over the wire) and instead ride along on whichever
        # discrete-field change or heartbeat next triggers a publish.
        state_payload = json.dumps({
            "state": state.value,
            "role": "leak-test" if role_leak else "equipment-test",
            "elapsedMs": elapsed,
            "inventory_mL": round(inventory, 3),
            "ackRequired": ack_required,
            "acked": acked,
            "reason": reason.value,
            "sensorsOn": sensors_on,
            "clearForMs": (clear_for_ms // 1000) * 1000,
            "clearRequiredMs": clear_required_ms,
            "fanSpeedPct": fan_speed_pct,
            "registers": registers,
            "flowRate_mLps": flow_rate_mLps,
        })
        change_key = json.dumps({
            "state": state.value,
            "role": "leak-test" if role_leak else "equipment-test",
            "ackRequired": ack_required,
            "acked": acked,
            "reason": reason.value,
            "sensorsOn": sensors_on,
            "fanSpeedPct": fan_speed_pct,
            "registers": registers,
        })
        heartbeat_due = (
            self._last_state_pub_ms is None
            or (now - self._last_state_pub_ms) >= STATE_HEARTBEAT_MS
        )
        if change_key != self._last_state_change_key or heartbeat_due:
            self._last_state_change_key = change_key
            self._last_state_pub_ms = now
            self.client.publish(self.topic_state, state_payload, qos=1, retain=True)

        # sensors/power — remote DAQ command, change-detect only
        if remote_on != self._last_remote_on:
            self._last_remote_on = remote_on
            self.client.publish(
                self.topic_sensors_power,
                json.dumps({"on": remote_on}),
                qos=1, retain=True,
            )
            for daq in self.daq_devices:
                daq.set_powered(remote_on)
            print(f"[SIM] remote DAQ power -> {remote_on}")

        # problems.txt Area D1: DAQ voltages auto-ramp in step with the real
        # LEAKING/not-LEAKING lifecycle. set_leak_active() no-ops on a
        # non-edge call, so this can run unconditionally every tick.
        for daq in self.daq_devices:
            daq.set_leak_active(leak_active, fan_speed_pct=fan_speed_pct)

        # alarm — edge only
        if alarm_on != self._last_alarm_on:
            self._last_alarm_on = alarm_on
            self.client.publish(
                self.topic_alarm,
                json.dumps({"danger": alarm_on, "active": alarm_on,
                            "description": reason.value, "runId": run_id}),
                qos=1, retain=True,
            )

        self._publish_local_sensors(now, local_sensor_counts, sensors_on)

    def _publish_local_sensors(self, now: int, counts_by_idx: dict[int, int],
                               sensors_on: bool) -> None:
        """Publish the control PLC's own six 4-20 mA H2 sensors to the
        historian, mirroring kitchen/SensorStream.cpp's sensorStreamPublish().

        Gated on sensor power, matching the firmware: KITCHEN_LOCAL_SENSOR
        channels are only energised while the PLC has them on
        (localSensorsOn), and publishOne() skips a sensor whose everSeen is
        still false. Publishing a powered-off sensor would invent readings
        for hardware that is not actually measuring anything.

        Every wired sensor is published each interval, not just the spiked
        ones: the sim's sensor_counts only holds entries for sensors the
        force/ramp rig owns, but real hardware always reports all six, with a
        quiet sensor sitting at the 4 mA live-zero (counts 0).
        """
        if not sensors_on:
            # Reset the timer so power-on publishes immediately rather than
            # waiting out an interval that elapsed while powered down.
            self._last_local_sensor_pub_ms = None
            return
        if (self._last_local_sensor_pub_ms is not None
                and now - self._last_local_sensor_pub_ms < LOCAL_SENSOR_PUBLISH_MS):
            return
        self._last_local_sensor_pub_ms = now

        ts_ms = int(time.time() * 1000)   # wall clock — the historian stores this
        for idx, (pin, name) in enumerate(
            zip(KITCHEN_LOCAL_SENSOR_PINS, KITCHEN_LOCAL_SENSOR_NAMES)
        ):
            counts = counts_by_idx.get(idx, 0)   # no entry = quiet sensor at live-zero
            self.client.publish(
                local_sensor_topic(self.daq_location, self.daq_device_id, name),
                build_local_sensor_payload(pin=pin, counts=counts, ts_ms=ts_ms),
                qos=0, retain=False,
            )

class SimRuntime:
    """Owns the MQTT client, the KitchenSim, the two DaqDeviceSim instances,
    and the background tick thread. One instance = one running simulator
    session; the web GUI keeps exactly one of these alive at a time."""

    def __init__(self, host: str, port: int, user: str, password: str,
                 device_id: str, experiment_name: str, lab_id: str,
                 daq1_id: str, daq2_id: str, daq_location: str = "Kitchen"):
        self.host, self.port = host, port
        self.client = build_client(user=user, password=password, host=host, port=port,
                                    client_id=f"kitchen-sim-{device_id}")
        self.sim = KitchenSim(self.client, device_id, experiment_name, lab_id,
                              daq_location=daq_location)
        self.daq1 = DaqDeviceSim(device_id=daq1_id, location=daq_location,
                                  host=host, port=port, user=user, password=password,
                                  **DAQ1_AUTO_LEAK_KWARGS)
        self.daq2 = DaqDeviceSim(device_id=daq2_id, location=daq_location,
                                  host=host, port=port, user=user, password=password,
                                  **DAQ2_AUTO_LEAK_KWARGS)
        self.daqs = [self.daq1, self.daq2]
        self.sim.daq_devices = self.daqs
        self._self_topic_marker = device_id

        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.loop_start()

        for daq in self.daqs:
            daq.start()

        self._stop_event = threading.Event()
        self._tick_thread = threading.Thread(target=self._tick_loop, daemon=True)
        self._tick_thread.start()

    def _on_connect(self, c, userdata, flags, reason_code, properties=None):
        print(f"[SIM] MQTT connected rc={reason_code}")
        c.subscribe(self.sim.topic_cmd, qos=1)
        c.subscribe(self.sim.topic_permit, qos=0)
        c.subscribe("status/+/+/alarm/+/hydrogen", qos=1)
        self.sim.publish_online()

    def _on_message(self, c, userdata, msg):
        try:
            payload = msg.payload.decode("utf-8")
        except UnicodeDecodeError:
            return
        if msg.topic == self.sim.topic_cmd:
            self.sim.handle_cmd(payload)
        elif msg.topic == self.sim.topic_permit:
            self.sim.handle_permit(payload)
        elif "/alarm/" in msg.topic and msg.topic.endswith("/hydrogen"):
            self.sim.handle_peer_alarm(msg.topic, payload, self._self_topic_marker)

    def _tick_loop(self):
        period = 1.0 / TICK_HZ
        while not self._stop_event.is_set():
            self.sim.tick_and_publish()
            time.sleep(period)

    def snapshot(self) -> dict:
        """A JSON-safe dict of everything a UI would want to show, taken
        under the core's lock so it is a consistent instant-in-time view."""
        c = self.sim.core
        with self.sim.lock:
            data = {
                "connected": self.client.is_connected(),
                "device_id": self.sim.device_id,
                "state": c.state.value,
                "role": "leak-test" if c.role_is_leak_test else "equipment-test",
                "ackRequired": c.ack_required,
                "acked": c.acked,
                "reason": c.reason.value,
                "gasOpen": c.gas_open(),
                "fanSpeedPct": round(c.fan_speed_pct(), 1),
                "registers": c.registers(),
                "inventory_mL": round(c.delivered_inventory_ml, 1),
                "elapsedMs": c.elapsed_ms(now_ms()),
                "localSensorsOn": c.local_sensors_on,
                "remoteSensorsOn": c.remote_sensors_on(),
                "warmupPending": c.warmup_pending,
                "estop": c.estop_pressed,
                "permitPresent": c.permit_present,
                "permitValue": c.permit_value,
                "peerAlarm": c.peer_alarm_active,
                "expansionUnhealthy": c.expansion_unhealthy,
                "lastTransition": self.sim._last_transition,
                "runId": c.spec.run_id,
                "clearForMs": c.clear_for_ms(now_ms()),
                "clearRequiredMs": c.fully_vent_min_hold_ms,
                "localSensorCounts": dict(c.sensor_counts),
            }
        daqs = []
        for i, daq in enumerate((self.daq1, self.daq2), start=1):
            daqs.append({
                "index": i,
                "deviceId": daq.device_id,
                "powered": daq.powered,
                "online": daq.online,
                "channels": [{**entry, "volts": round(entry["volts"], 3)} for entry in daq.snapshot()],
                "forced": sorted(daq._forced_v.keys()),
            })
        data["daqs"] = daqs
        return data

    def shutdown(self) -> None:
        self._stop_event.set()
        for daq in self.daqs:
            daq.stop()
        try:
            self.client.publish(self.sim.topic_online, "offline", qos=1, retain=True)
            time.sleep(0.2)
            self.client.loop_stop()
            self.client.disconnect()
        except Exception:
            pass
