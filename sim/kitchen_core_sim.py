"""
kitchen_core_sim — a simplified, Python reimplementation of the firmware's
KitchenCore state machine (see kitchen/KitchenCore.h / KitchenCore.cpp and
docs/KitchenCore-and-Protocol.md).

This is NOT the safety-certified firmware logic — it is a simulator used to
drive the webapp in development without real hardware. It mirrors the state
graph and the observable behaviour (topics, JSON shapes, timings) closely
enough that the webapp cannot tell the difference, but trims corners that
don't matter for exercising the UI (e.g. inlet-open sequencing delay,
per-sensor calibration offsets).

State graph (see docs/KitchenCore-and-Protocol.md section 3):

  WAITING --start--> ARMED --confirm--> LEAKING --stop--> HOLD --stop--> VENTILATING --stop--> WAITING
     ^                 | timeout            |                                                  ^
     |                 v                    v (any danger, from any state)                      |
     +---------- WAITING          FULLY_VENTILATING ------------------------------- ack+5min clear+
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum


class KitchenState(str, Enum):
    WAITING = "WAITING"
    ARMED = "ARMED"
    LEAKING = "LEAKING"
    HOLD = "HOLD"
    VENTILATING = "VENTILATING"
    FULLY_VENTILATING = "FULLY_VENTILATING"


class DangerReason(str, Enum):
    NONE = "NONE"
    LOCAL_SENSOR_THRESHOLD = "LOCAL_SENSOR_THRESHOLD"
    LOCAL_SENSOR_STALE = "LOCAL_SENSOR_STALE"
    FLOW_OVER_LIMIT = "FLOW_OVER_LIMIT"
    INVENTORY_CAP_EXCEEDED = "INVENTORY_CAP_EXCEEDED"
    PEER_ALARM = "PEER_ALARM"
    PERMIT_DENIED = "PERMIT_DENIED"
    ESTOP = "ESTOP"
    EXPANSION_FAULT = "EXPANSION_FAULT"
    EXTERNAL_TRIP = "EXTERNAL_TRIP"
    OPERATOR_ABORT = "OPERATOR_ABORT"


class StartRejectReason(str, Enum):
    NONE = "NONE"
    WRONG_STATE = "WRONG_STATE"
    WRONG_ROLE = "WRONG_ROLE"
    INVALID_SPEC = "INVALID_SPEC"
    RUN_ID_MISMATCH = "RUN_ID_MISMATCH"
    ARM_TIMED_OUT = "ARM_TIMED_OUT"


# --- Timings (mirrors Kitchen_Settings.h). These are the real-hardware
#     defaults; KitchenCoreSim(...) accepts per-timing kwargs (also readable
#     from env — see runtime.py) so interactive/scripted testing can run with
#     a 10s purge/warm-up instead of waiting 5 minutes / 70s per iteration.
#     kitchen/Kitchen_Settings.h itself is left untouched — this only affects
#     the sim, never real firmware. ---
ARM_TIMEOUT_MS = 60_000
FULLY_VENT_MIN_HOLD_MS = 300_000
HOLD_MAX_DURATION_MS_DEFAULT = 600_000
SENSOR_WARMUP_MS = 70_000
VENT_SPEED_IDLE_PCT = 10.0
VENT_SPEED_MAX_PCT = 100.0


def now_ms() -> int:
    return int(time.monotonic() * 1000)


@dataclass
class StopCondition:
    max_duration_ms: int = 0
    max_inventory_ml: float = 0.0
    quorum_count: int = 0
    quorum_threshold_pct: float = 0.0


@dataclass
class RunSpec:
    run_id: str = ""
    gas_setpoint_pct: float = 0.0
    leak_stop: StopCondition = field(default_factory=StopCondition)
    hold_stop: StopCondition = field(default_factory=StopCondition)
    vent_registers: dict = field(default_factory=lambda: {"central": False, "exhaust": False, "inlet": False})
    fan_speed_pct: float = 0.0
    vent_stop: StopCondition = field(default_factory=StopCondition)
    valid: bool = False


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


class KitchenCoreSim:
    """Python port of KitchenCore's state machine. Call update() at whatever
    cadence the driving loop wants (the interactive sim ticks ~5 Hz)."""

    def __init__(
        self,
        *,
        arm_timeout_ms: int = ARM_TIMEOUT_MS,
        fully_vent_min_hold_ms: int = FULLY_VENT_MIN_HOLD_MS,
        sensor_warmup_ms: int = SENSOR_WARMUP_MS,
        hold_max_duration_ms: int = HOLD_MAX_DURATION_MS_DEFAULT,
    ):
        self.state = KitchenState.WAITING
        self.spec = RunSpec()
        self.armed_at_ms = 0
        self.state_entered_ms = now_ms()
        self.phase_clock_from_ms = 0

        # Per-instance timings — see the module-level defaults above for why
        # these are overridable (fast interactive/scripted testing).
        self.arm_timeout_ms = arm_timeout_ms
        self.fully_vent_min_hold_ms = fully_vent_min_hold_ms
        self.sensor_warmup_ms = sensor_warmup_ms
        self.hold_max_duration_ms = hold_max_duration_ms

        self.delivered_inventory_ml = 0.0
        self._last_integration_ms = 0

        self.local_sensors_on = False
        self.warmup_pending = False
        self.warmup_started_ms = 0

        self.ack_required = False
        self.acked = False
        self.reason = DangerReason.NONE
        self.clear_since_ms = 0

        self.role_is_leak_test = True

        # Danger inputs the console / peer-alarm feed can toggle live.
        self.estop_pressed = False
        self.permit_present = False
        self.permit_value = True
        self.peer_alarm_active = False
        self.expansion_unhealthy = False
        # sensor_index -> counts (0-4095). Populated/overridden by DAQ sim feed.
        self.sensor_counts: dict[int, int] = {}
        self.sensor_threshold_counts: dict[int, int] = {}
        self.default_threshold_counts = 1024

    # -- helpers -------------------------------------------------------
    def remote_sensors_on(self) -> bool:
        if not self.role_is_leak_test:
            return False
        if self.state in (
            KitchenState.LEAKING,
            KitchenState.HOLD,
            KitchenState.VENTILATING,
            KitchenState.FULLY_VENTILATING,
        ):
            return self.local_sensors_on
        return False

    def _enter_state(self, next_state: KitchenState, now: int) -> None:
        self.state = next_state
        self.state_entered_ms = now
        self.phase_clock_from_ms = now

    def _enter_fully_ventilating(self, reason: DangerReason, requires_ack: bool, now: int) -> None:
        was_already = self.state == KitchenState.FULLY_VENTILATING
        if not was_already:
            self._enter_state(KitchenState.FULLY_VENTILATING, now)
            self.ack_required = requires_ack
            self.acked = False
            self.clear_since_ms = 0 if requires_ack else (1 if now == 0 else now)
        elif requires_ack and not self.ack_required:
            self.ack_required = True
            self.acked = False
            self.clear_since_ms = 0
        self.reason = reason

    def _danger_active(self) -> DangerReason:
        for idx, counts in self.sensor_counts.items():
            threshold = self.sensor_threshold_counts.get(idx, self.default_threshold_counts)
            if counts >= threshold:
                return DangerReason.LOCAL_SENSOR_THRESHOLD
        if self.peer_alarm_active:
            return DangerReason.PEER_ALARM
        if self.permit_present and not self.permit_value:
            return DangerReason.PERMIT_DENIED
        if self.estop_pressed:
            return DangerReason.ESTOP
        if self.expansion_unhealthy:
            return DangerReason.EXPANSION_FAULT
        return DangerReason.NONE

    def _can_leave_fully_ventilating(self, now: int) -> bool:
        if not self.ack_required:
            return True
        if not self.acked:
            return False
        if self.clear_since_ms == 0:
            return False
        return (now - self.clear_since_ms) >= self.fully_vent_min_hold_ms

    def clear_for_ms(self, now: int) -> int:
        """Continuous clear-air time so far in FULLY_VENTILATING, against
        fully_vent_min_hold_ms — 0 whenever the condition isn't currently
        clear (mirrors KitchenCore::clearSinceMs_, see problems.txt: the
        clear-air countdown was never published anywhere)."""
        if self.clear_since_ms == 0:
            return 0
        return max(0, now - self.clear_since_ms)

    def _stop_condition_met(self, sc: StopCondition, now: int) -> bool:
        elapsed = now - self.phase_clock_from_ms
        if sc.max_duration_ms and elapsed >= sc.max_duration_ms:
            return True
        if sc.max_inventory_ml and self.delivered_inventory_ml >= sc.max_inventory_ml:
            return True
        if sc.quorum_count > 0:
            threshold_counts = sc.quorum_threshold_pct  # already counts, see start()
            hits = sum(1 for c in self.sensor_counts.values() if c >= threshold_counts)
            if hits >= sc.quorum_count:
                return True
        return False

    def _integrate_inventory(self, now: int) -> None:
        if self.state != KitchenState.LEAKING or self.warmup_pending:
            self._last_integration_ms = now
            return
        if self._last_integration_ms == 0:
            self._last_integration_ms = now
            return
        dt_s = max(0, now - self._last_integration_ms) / 1000.0
        # Rough sim rate: gasSetpointPct% of a nominal 50 mL/s full-scale flow.
        rate_ml_s = (self.spec.gas_setpoint_pct / 100.0) * 50.0
        self.delivered_inventory_ml += rate_ml_s * dt_s
        self._last_integration_ms = now

    # -- commands (mirror Protocol.cpp dispatch + KitchenCore commands) ----
    def start(self, spec: RunSpec, now: int) -> StartRejectReason:
        if self.state != KitchenState.WAITING:
            return StartRejectReason.WRONG_STATE
        if not self.role_is_leak_test:
            return StartRejectReason.WRONG_ROLE
        if not spec.valid:
            return StartRejectReason.INVALID_SPEC
        self.spec = spec
        self.armed_at_ms = now
        self._enter_state(KitchenState.ARMED, now)
        return StartRejectReason.NONE

    def confirm(self, run_id: str, now: int) -> StartRejectReason:
        if self.state != KitchenState.ARMED:
            return StartRejectReason.WRONG_STATE
        if now - self.armed_at_ms >= self.arm_timeout_ms:
            self._enter_state(KitchenState.WAITING, now)
            return StartRejectReason.ARM_TIMED_OUT
        if run_id != self.spec.run_id:
            return StartRejectReason.RUN_ID_MISMATCH
        self._enter_state(KitchenState.LEAKING, now)
        if not self.local_sensors_on:
            self.warmup_pending = True
            self.warmup_started_ms = now
        return StartRejectReason.NONE

    def stop(self, now: int) -> None:
        if self.state in (KitchenState.LEAKING, KitchenState.HOLD, KitchenState.VENTILATING):
            self._enter_fully_ventilating(DangerReason.NONE, requires_ack=False, now=now)
        elif self.state == KitchenState.ARMED:
            self._enter_state(KitchenState.WAITING, now)

    def human_ack(self, now: int) -> None:
        self.acked = True

    # -- the once-per-tick update, mirrors KitchenCore::update() -----------
    def update(self, now: int) -> None:
        danger = self._danger_active()
        if danger != DangerReason.NONE:
            self._enter_fully_ventilating(danger, requires_ack=True, now=now)
            self.clear_since_ms = 0
        elif self.state == KitchenState.FULLY_VENTILATING:
            if self.clear_since_ms == 0:
                self.clear_since_ms = now

        if not self.role_is_leak_test and not self.local_sensors_on:
            self.local_sensors_on = True
            self.warmup_pending = False

        if self.state == KitchenState.ARMED:
            if now - self.armed_at_ms >= self.arm_timeout_ms:
                self._enter_state(KitchenState.WAITING, now)

        elif self.state == KitchenState.LEAKING:
            self.local_sensors_on = True
            gate_open = (not self.warmup_pending) or (now - self.warmup_started_ms >= self.sensor_warmup_ms)
            if self.warmup_pending and gate_open:
                self.warmup_pending = False
                self.phase_clock_from_ms = now
                self._last_integration_ms = 0
            if not self.role_is_leak_test:
                self._enter_fully_ventilating(DangerReason.OPERATOR_ABORT, requires_ack=True, now=now)
            else:
                self._integrate_inventory(now)
                if self._stop_condition_met(self.spec.leak_stop, now):
                    self._enter_state(KitchenState.HOLD, now)

        elif self.state == KitchenState.HOLD:
            if self._stop_condition_met(self.spec.hold_stop, now):
                self._enter_state(KitchenState.VENTILATING, now)

        elif self.state == KitchenState.VENTILATING:
            if self._stop_condition_met(self.spec.vent_stop, now):
                self._enter_fully_ventilating(DangerReason.NONE, requires_ack=False, now=now)

        elif self.state == KitchenState.FULLY_VENTILATING:
            if self._can_leave_fully_ventilating(now):
                self._enter_state(KitchenState.WAITING, now)
                self.ack_required = False
                self.acked = False
                self.reason = DangerReason.NONE
                self.local_sensors_on = False
                self.delivered_inventory_ml = 0.0

        elif self.state == KitchenState.WAITING:
            self.delivered_inventory_ml = 0.0

    # -- output snapshot (mirrors OutputRequest / outputsFor()) ------------
    def gas_open(self) -> bool:
        return self.state == KitchenState.LEAKING and not self.warmup_pending

    def fan_speed_pct(self) -> float:
        if self.state in (KitchenState.WAITING, KitchenState.ARMED):
            return VENT_SPEED_IDLE_PCT
        if self.state in (KitchenState.LEAKING, KitchenState.HOLD):
            return 0.0
        if self.state == KitchenState.VENTILATING:
            return _clamp(self.spec.fan_speed_pct, 0.0, VENT_SPEED_MAX_PCT)
        if self.state == KitchenState.FULLY_VENTILATING:
            return 100.0
        return 0.0

    def registers(self) -> dict:
        if self.state == KitchenState.FULLY_VENTILATING:
            return {"central": True, "exhaust": True, "inlet": True}
        if self.state == KitchenState.VENTILATING:
            return dict(self.spec.vent_registers)
        return {"central": False, "exhaust": False, "inlet": False}

    def alarm_on(self) -> bool:
        return self.state == KitchenState.FULLY_VENTILATING

    def gas_may_be_present(self) -> bool:
        return self.state != KitchenState.WAITING

    def elapsed_ms(self, now: int) -> int:
        return now - self.state_entered_ms
