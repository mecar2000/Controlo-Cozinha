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

import math
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
    # External H2 sensors (base A6/A7) — firmware-only, like LOCAL_SENSOR_STALE
    # above: KitchenCoreSim has no A6/A7 model, so nothing in this sim drives
    # these. Listed here only so code that enumerates DangerReason (e.g. a
    # completeness check against the firmware's wire vocabulary) stays in sync.
    EXTERNAL_H2_THRESHOLD = "EXTERNAL_H2_THRESHOLD"
    EXTERNAL_H2_SENSOR_FAULT = "EXTERNAL_H2_SENSOR_FAULT"


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

# problems.txt Area D1: "Concentration should increase gradually, and
# decrease fast once ventilation starts." Governs the automatic ramp on any
# local sensor NOT already under console/scenario control (force_local_sensor/
# ramp_local_sensor) — see _apply_auto_leak_ramp(). Tunable; the only hard
# requirement is FALL << RISE.
AUTO_LEAK_RISE_MS = 30_000
# FALL is the time constant at FULL fan (100%), not a fixed dump-to-zero
# duration: the actual decay is exponential and scales inversely with the
# commanded fan speed (see _auto_leak_fall_tau_ms). A flat constant made
# VENTILATING at 15% fan clear the room exactly as fast as FULLY_VENTILATING
# at 100%, which is what "it all disappears too fast" was describing.
AUTO_LEAK_FALL_MS = 5_000
# Fan speeds below this are treated as this for decay purposes, so a 0%/idle
# fan still eventually clears instead of dividing by zero and stalling forever.
AUTO_LEAK_MIN_FAN_PCT = 5.0
# An exponential only approaches zero asymptotically; below this fraction of
# the starting value a sensor is snapped to clean air so the decay actually
# terminates (and _auto_leak_phase stops tracking it).
AUTO_LEAK_SETTLE_FRAC = 0.02


def now_ms() -> int:
    return int(time.monotonic() * 1000)


# Local A0602 current sensors (KITCHEN_WIRED_LOCAL_SENSORS I1-I6) are a
# 4-20mA loop; sensor_counts compares against thresholds in the same 0-4095
# ADC-count space kitchen.ino's analogRead() produces. This is the ONE place
# that mapping lives — runtime.py's quorum-threshold parsing, the scenario
# engine's Spike/Ramp, and the interactive console's lspike/lclear commands
# all call this rather than re-deriving it, after a prior divergence (ma/20
# vs (ma-4)/16) silently raised every quorum threshold by ~819 counts.
def ma_to_counts(ma: float) -> int:
    frac = _clamp((ma - 4.0) / 16.0, 0.0, 1.0)
    return int(frac * 4095)


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
    leak_registers: dict = field(default_factory=lambda: {"central": False, "exhaust": False, "inlet": False})
    leak_fan_speed_pct: float = 0.0
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
        # sensor_index -> counts (0-4095), for the kitchen PLC's own local
        # A0602 current sensors (0-5, matching KITCHEN_WIRED_LOCAL_SENSORS
        # I1-I6). Rebuilt on every update() from whatever force_local_sensor/
        # ramp_local_sensor overrides are active (see below) — an unforced
        # sensor is simply absent (clean air, never compared to a
        # threshold). This is the ONLY sensor path that can trip
        # LOCAL_SENSOR_THRESHOLD: on real hardware kitchen.ino never reads
        # the remote DAQ's values, only its own local sensors (see
        # daq_device_sim.py's module docstring).
        self.sensor_counts: dict[int, int] = {}
        self.sensor_threshold_counts: dict[int, int] = {}
        self.default_threshold_counts = 1024
        self._forced_sensor_ma: dict[int, float] = {}
        self._sensor_ramps: dict[int, tuple[float, float, float, float]] = {}

        # Module-level defaults, exposed per-instance so callers/tests can
        # read (or override) them without reaching for the module constant.
        self.AUTO_LEAK_RISE_MS = AUTO_LEAK_RISE_MS
        self.AUTO_LEAK_FALL_MS = AUTO_LEAK_FALL_MS
        # Auto-ramp state per sensor index, independent of the manual
        # force/ramp rig: (phase_start_ms, phase_start_counts, rising: bool).
        # Rebuilt on every LEAKING<->not-LEAKING edge in _apply_auto_leak_ramp.
        self._auto_leak_phase: dict[int, tuple[int, int, bool]] = {}
        self._auto_leak_was_leaking = False

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
            # The automatic leak ramp is DISPLAY ONLY and can never trip a
            # danger on its own: it drives all six wired indices regardless of
            # which sensors the operator has actually configured, so letting it
            # trip meant six phantom sensors crossing the (hardcoded) default
            # threshold simultaneously at the end of a high-setpoint run — a
            # spike on hardware that isn't there. Only the explicit spike/ramp
            # rig (lspike, the GUI Spike buttons, scenario Spike/Ramp steps)
            # represents a real sensor reading something, so only it can trip.
            if idx in self._auto_leak_phase:
                continue
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

    # -- local current sensor rig (console lspike/lclear, scenario Spike/
    #    Ramp/ClearForce) -----------------------------------------------
    def force_local_sensor(self, sensor_idx: int, ma: float) -> None:
        self._forced_sensor_ma[sensor_idx] = ma
        self._sensor_ramps.pop(sensor_idx, None)

    def ramp_local_sensor(self, sensor_idx: int, from_ma: float, to_ma: float, duration_s: float) -> None:
        self._forced_sensor_ma.pop(sensor_idx, None)
        self._sensor_ramps[sensor_idx] = (from_ma, to_ma, time.monotonic(), max(1e-6, duration_s))

    def clear_local_sensor(self, sensor_idx: int | None = None) -> None:
        if sensor_idx is None:
            for idx in set(self._forced_sensor_ma) | set(self._sensor_ramps):
                self.sensor_counts.pop(idx, None)
            self._forced_sensor_ma.clear()
            self._sensor_ramps.clear()
        else:
            self._forced_sensor_ma.pop(sensor_idx, None)
            self._sensor_ramps.pop(sensor_idx, None)
            self.sensor_counts.pop(sensor_idx, None)

    def _apply_local_sensor_forces(self) -> None:
        """Merge every sensor currently forced/ramped into sensor_counts,
        keyed by index — does not touch entries this rig doesn't own, so
        sensor_counts set some other way (tests assigning it directly) is
        left alone. clear_local_sensor() is what removes a rig-owned entry
        (no reading = never compared to a threshold), not this method."""
        for idx in set(self._forced_sensor_ma) | set(self._sensor_ramps):
            if idx in self._forced_sensor_ma:
                ma = self._forced_sensor_ma[idx]
            else:
                from_ma, to_ma, start_s, duration_s = self._sensor_ramps[idx]
                frac = _clamp((time.monotonic() - start_s) / duration_s, 0.0, 1.0)
                ma = from_ma + (to_ma - from_ma) * frac
            self.sensor_counts[idx] = ma_to_counts(ma)

    def _auto_leak_target_counts(self) -> int:
        """Where an unforced sensor is heading during LEAKING: the same
        %->mA->counts mapping start()/_parse_spec already use for the
        quorum threshold, scaled by the operator's own gas_setpoint_pct — so
        a high enough setpoint legitimately drives counts past
        default_threshold_counts (or a per-sensor override) and trips a real
        FULLY_VENTILATING, exactly as an ungoverned leak should be able to."""
        ma = 4.0 + (_clamp(self.spec.gas_setpoint_pct, 0.0, 100.0) / 100.0) * 16.0
        return ma_to_counts(ma)

    def _auto_leak_fall_tau_ms(self) -> float:
        """Exponential decay time constant for the auto-leak fall, scaled by
        the fan speed actually commanded right now: purging a room twice as
        hard clears it twice as fast. AUTO_LEAK_FALL_MS is the constant at
        100% fan, so VENTILATING at the operator's fan_speed_pct decays
        proportionally slower while FULLY_VENTILATING (always 100%) stays at
        the full rate. Clamped at AUTO_LEAK_MIN_FAN_PCT so an idle/0% fan
        still clears eventually instead of dividing by zero."""
        fan_pct = max(AUTO_LEAK_MIN_FAN_PCT, self.fan_speed_pct())
        return max(1.0, self.AUTO_LEAK_FALL_MS * (100.0 / fan_pct))

    def _apply_auto_leak_ramp(self, now: int) -> None:
        """Drives LOCAL sensors NOT owned by the manual force/ramp rig
        (_forced_sensor_ma / _sensor_ramps) toward _auto_leak_target_counts()
        while LEAKING (gradual rise over AUTO_LEAK_RISE_MS), holds steady
        through HOLD (fan is off, nothing vents the room), and falls back to
        zero once VENTILATING starts (fast fall over AUTO_LEAK_FALL_MS) — see
        problems.txt "In hold concentrations should be maintained as they
        were at the end of leak". Runs AFTER _apply_local_sensor_forces so a
        manual override on a given index always wins.

        Deliberately narrow about what it touches: an index only enters
        self._auto_leak_phase (and so becomes eligible for the fast-decay
        half of this method) once an actual LEAKING edge starts driving it up
        here. A sensor_counts entry set some other way entirely — a test
        assigning it directly, as several pre-existing ones do — is never
        adopted mid-flight and is left alone, exactly like
        _apply_local_sensor_forces's own "does not touch entries this rig
        doesn't own" rule."""
        leaking_now = self.state == KitchenState.LEAKING and not self.warmup_pending
        holding_now = self.state == KitchenState.HOLD
        rising_or_held = leaking_now or holding_now
        if leaking_now and not self._auto_leak_was_leaking:
            # Edge into LEAKING: every unforced index starts rising from
            # wherever it currently sits (usually 0, but not snapping to 0 if
            # it wasn't) rather than from a hardcoded zero.
            for idx in range(6):
                if idx in self._forced_sensor_ma or idx in self._sensor_ramps:
                    continue
                self._auto_leak_phase[idx] = (now, self.sensor_counts.get(idx, 0))
        elif not rising_or_held and self._auto_leak_was_leaking:
            # Edge out of LEAKING/HOLD (i.e. into VENTILATING or an abort):
            # only sensors THIS method was already driving start decaying —
            # restart their phase from their actual current value so the
            # fall doesn't jump.
            for idx in list(self._auto_leak_phase):
                if idx in self._forced_sensor_ma or idx in self._sensor_ramps:
                    continue
                self._auto_leak_phase[idx] = (now, self.sensor_counts.get(idx, 0))
        self._auto_leak_was_leaking = rising_or_held

        if holding_now:
            return  # HOLD: fan is off, nothing vents the room — hold steady

        if not leaking_now and not self._auto_leak_phase:
            return  # nothing this method has ever driven — never touch sensor_counts

        target = self._auto_leak_target_counts() if leaking_now else 0
        tau_ms = None if leaking_now else self._auto_leak_fall_tau_ms()

        for idx in list(self._auto_leak_phase):
            if idx in self._forced_sensor_ma or idx in self._sensor_ramps:
                # A manual override has since claimed this index — stop
                # tracking it here entirely; it may rejoin on a future edge.
                del self._auto_leak_phase[idx]
                continue
            phase_start_ms, phase_start_counts = self._auto_leak_phase[idx]
            dt_ms = max(0, now - phase_start_ms)
            if leaking_now:
                # Rise stays linear — a leak feeds the room at a steady rate.
                frac = _clamp(dt_ms / max(1, self.AUTO_LEAK_RISE_MS), 0.0, 1.0)
                counts = int(phase_start_counts + (target - phase_start_counts) * frac)
                settled = False
            else:
                # Fall is exponential and fan-scaled: a room purges at a rate
                # proportional to how hard it is being ventilated, so the curve
                # eases out instead of hitting zero on a fixed deadline.
                counts = int(phase_start_counts * math.exp(-dt_ms / tau_ms))
                settled = counts <= max(1, int(phase_start_counts * AUTO_LEAK_SETTLE_FRAC))
            if counts > 0 and not settled:
                self.sensor_counts[idx] = counts
            else:
                self.sensor_counts.pop(idx, None)
                if not leaking_now:
                    del self._auto_leak_phase[idx]  # fully decayed — stop tracking

    # -- the once-per-tick update, mirrors KitchenCore::update() -----------
    def update(self, now: int) -> None:
        self._apply_local_sensor_forces()
        self._apply_auto_leak_ramp(now)
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

    def flow_rate_mLps(self) -> float:
        """Live leak flow, mL/s — same rate _integrate_inventory() actually
        accrues by, so the webapp's flow animation speed matches the
        inventory number ticking up next to it."""
        if not self.gas_open():
            return 0.0
        return (self.spec.gas_setpoint_pct / 100.0) * 50.0

    def fan_speed_pct(self) -> float:
        if self.state in (KitchenState.WAITING, KitchenState.ARMED):
            return VENT_SPEED_IDLE_PCT
        if self.state == KitchenState.LEAKING:
            return _clamp(self.spec.leak_fan_speed_pct, 0.0, VENT_SPEED_MAX_PCT)
        if self.state == KitchenState.HOLD:
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
        if self.state == KitchenState.LEAKING:
            return dict(self.spec.leak_registers)
        return {"central": False, "exhaust": False, "inlet": False}

    def alarm_on(self) -> bool:
        return self.state == KitchenState.FULLY_VENTILATING

    def gas_may_be_present(self) -> bool:
        return self.state != KitchenState.WAITING

    def elapsed_ms(self, now: int) -> int:
        return now - self.state_entered_ms
