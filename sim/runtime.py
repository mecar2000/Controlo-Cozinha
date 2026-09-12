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

import paho.mqtt.client as mqtt

from daq_device_sim import DaqDeviceSim
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
    now_ms,
)

TICK_HZ = 5.0


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

    def __init__(self, client: mqtt.Client, device_id: str, experiment_name: str, lab_id: str):
        self.client = client
        self.device_id = device_id
        self.experiment_name = experiment_name
        self.lab_id = lab_id
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

        self._last_state_payload = None
        self._last_remote_on = None
        self._last_alarm_on = None
        self._last_run_running = None
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
                # linearly onto the 4-20mA loop (4mA=0%, 20mA=100%) converted to
                # the 0-4095 ADC count space used by sensor_counts. This MUST use
                # the same live-zero as feed_daq_sensors()'s (ma-4)/16 mapping —
                # they used to disagree (this one was ma/20), which silently
                # raised every quorum threshold by ~819 counts (~4mA worth) and
                # meant a requested "N sensors at X %v/v" never tripped at X.
                ma = 4.0 + (pct / 100.0) * 16.0
                frac = max(0.0, min(1.0, (ma - 4.0) / 16.0))
                counts = int(frac * 4095)
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

        # state topic — discrete-change or 1Hz heartbeat, matches kitchen.ino's gate.
        # clearForMs is rounded to whole seconds: this payload is only
        # re-published on change (see below), and an unrounded ms value would
        # change — and therefore publish — every single tick.
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
        })
        if state_payload != self._last_state_payload:
            self._last_state_payload = state_payload
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

        # alarm — edge only
        if alarm_on != self._last_alarm_on:
            self._last_alarm_on = alarm_on
            self.client.publish(
                self.topic_alarm,
                json.dumps({"danger": alarm_on, "active": alarm_on,
                            "description": reason.value, "runId": run_id}),
                qos=1, retain=True,
            )

    def feed_daq_sensors(self, daq_devices: list[DaqDeviceSim]) -> None:
        """Mirror each DAQ device's live mA readings into the core's danger
        check, converted to the same 0-4095 ADC-count space used by counts
        comparisons (4mA=0 counts .. 20mA=4095 counts) so a forced spike can
        actually trip LOCAL_SENSOR_THRESHOLD through the same path the
        firmware uses."""
        counts: dict[int, int] = {}
        idx = 0
        for daq in daq_devices:
            for ma in daq.snapshot():
                frac = max(0.0, min(1.0, (ma - 4.0) / 16.0))
                counts[idx] = int(frac * 4095)
                idx += 1
        with self.lock:
            self.core.sensor_counts = counts


class SimRuntime:
    """Owns the MQTT client, the KitchenSim, the two DaqDeviceSim instances,
    and the background tick thread. One instance = one running simulator
    session; the web GUI keeps exactly one of these alive at a time."""

    def __init__(self, host: str, port: int, user: str, password: str,
                 device_id: str, experiment_name: str, lab_id: str,
                 daq1_id: str, daq2_id: str):
        self.host, self.port = host, port
        self.client = build_client(user=user, password=password, host=host, port=port,
                                    client_id=f"kitchen-sim-{device_id}")
        self.sim = KitchenSim(self.client, device_id, experiment_name, lab_id)
        self.daq1 = DaqDeviceSim(self.client, daq1_id)
        self.daq2 = DaqDeviceSim(self.client, daq2_id)
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
            self.sim.feed_daq_sensors(self.daqs)
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
            }
        daqs = []
        for i, daq in enumerate((self.daq1, self.daq2), start=1):
            daqs.append({
                "index": i,
                "deviceId": daq.device_id,
                "powered": daq.powered,
                "online": daq.online,
                "channels": [round(v, 3) for v in daq.snapshot()],
                "forced": sorted(daq._forced_ma.keys()),
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
