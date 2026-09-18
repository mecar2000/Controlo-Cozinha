# Webapp changes needed after the firmware update

Written for: whoever is editing the webapp (you) — the firmware side is already
done and tested, nothing here has been applied to `webapp/` or `sim/`.

The firmware's `KitchenControl/{deviceId}/state` payload changed field names and
gained fields, and the run spec gained two optional fields. Nothing in the
webapp has been touched, so **right now the webapp and firmware disagree** —
apply the changes below to bring them back in line.

Firmware verification: `401/401` checks pass
(`g++ -std=c++17 -DKITCHEN_ALLOW_PLACEHOLDER_SCALES -I kitchen -I <ArduinoJson>/src kitchen/test/test_*.cpp kitchen/KitchenCore.cpp kitchen/Protocol.cpp -o t && ./t`).

---

## 1. Why the renames happened (read this first)

Three fields the firmware published were read by **nothing**. The browser was
reading names the firmware never sent, so the readouts silently rendered blank
— no error, no warning, just empty values:

| Firmware sent (old) | Browser reads | Visible symptom |
|---|---|---|
| `inventory_mL` | `deliveredInventory_mL` | "delivered" always blank |
| `reason` | `dangerReason` / `latchCause` | latch cause blank — *told the run latched but not why* |
| `sensorsOn` (scalar) | `localSensorsOn` / `remoteSensorsOn` | nothing read it |
| *(never sent)* | `clearForMs` / `clearRequiredMs` | all-clear countdown frozen at 0 |

The firmware was changed to publish the **browser's** names, because those are
what the sim already agreed with and what the frontend already reads. The old
names are now gone entirely — deliberately not aliased, so there stays exactly
one name per field on the wire.

---

## 2. New `state` payload shape

```json
{
  "state": "LEAKING",
  "role": "leak-test",
  "elapsedMs": 1234,
  "deliveredInventory_mL": 56.0,     // RENAMED from inventory_mL
  "ackRequired": false,
  "acked": false,
  "dangerReason": "NONE",            // RENAMED from reason
  "localSensorsOn": true,            // SPLIT from scalar sensorsOn
  "remoteSensorsOn": false,          // SPLIT from scalar sensorsOn
  "fanSpeedPct": 42.0,
  "gasSetpointPct": 33.0,            // NEW — was in types.ts, never published
  "flowRate_mLps": 7.5,
  "clearForMs": 12000,               // NEW — all-clear hold progress
  "clearRequiredMs": 300000,         // NEW — the total to count against
  "registers": { "central": true, "exhaust": false, "inlet": true }
}
```

Worst-case measured size: **410 bytes** (512-byte buffer, 102 spare). No
truncation risk.

### 2a. `sim/runtime.py` — make the sim match

The sim is the reference the webapp tests run against, so it must publish the
same names. In the `state_payload` dict (~line 331):

- `"inventory_mL"` → `"deliveredInventory_mL"`
- `"reason"` → `"dangerReason"`
- `"sensorsOn": sensors_on` → `"localSensorsOn": ...` + `"remoteSensorsOn": ...`
- add `"gasSetpointPct"`

`clearForMs` / `clearRequiredMs` are already published by the sim — leave them.

**Also update `change_key`** (~line 346) to match, or the discrete-change gate
will stop firing on the fields it no longer names. `clearForMs` must stay OUT
of `change_key` (it ticks continuously; it rides the heartbeat) — same rule the
firmware follows.

### 2b. `tests/test_scenarios_against_webapp.py:153`

```python
assert resp.get_json()["kitchen_state"]["reason"] == "ESTOP"
```
→ change `"reason"` to `"dangerReason"`. **This test will fail until you do.**

### 2c. `webapp/frontend/src/api/types.ts`

The `KitchenState` interface already declares every new name, so **no change is
strictly required**. Optional cleanup now that the firmware no longer sends the
old shapes:

- `phase?: KitchenPhase` — firmware only ever sent `state`; the `phase ?? state`
  fallbacks throughout the UI can stay, they're harmless.
- `latchCause` / `description` — the sim-era aliases. Safe to keep as fallbacks.

No backend change needed: `app/state.py:88` stores the payload verbatim, so the
renamed fields reach the UI on their own.

---

## 3. New run-spec fields: ventilation during a leak

Leaking and ventilating are no longer mutually exclusive. A run can now leak
into a partially-vented room (one damper cracked, fan at 20%).

Two **optional** fields added to the `start` spec:

```json
{
  "leakRegisters":   { "central": false, "exhaust": true, "inlet": false },
  "leakFanSpeedPct": 20.0
}
```

**Both default to sealed** (all registers closed, fan 0), which is exactly the
old behaviour — an existing spec that omits them runs identically. The firmware
echoes both back post-clamp in the `ack`, under `spec.leakRegisters` and
`spec.leakFanSpeedPct`, so the review screen can show what will actually run.

Safety is unchanged and was tested: `leakFanSpeedPct` is clamped by
`clampFanSpeedPct()` like every other fan figure, and any danger trip still
forces all three registers open and the fan to 100%
(`danger_during_vented_leak_still_forces_full_ventilation`).

### 3a. `webapp/frontend/src/api/types.ts` — extend `RunSpec`

```ts
export interface RunSpec {
  gasSetpointPct: number
  leakStop: StopCondition
  /** Ventilation DURING the leak. Optional; omitted = sealed room (the
   *  pre-existing behaviour). Clamped to VENT_SPEED_MAX_PCT by firmware. */
  leakRegisters?: RegisterSet
  leakFanSpeedPct?: number
  holdStop: StopCondition
  ventRegisters: RegisterSet
  fanSpeedPct: number
  ventStop: StopCondition
}
```

### 3b. `webapp/app/runs.py:73` — `_inlet_only()` **(do not skip this)**

Its own docstring says *"Checked against every stop-condition phase's register
set that a spec can carry (today just ventRegisters)"*. That's no longer true —
a spec can now carry `leakRegisters`, and an inlet-only leak phase would slip
past the interlock entirely.

```python
def _inlet_only(spec: dict) -> bool:
    """True when ANY phase's requested vent registers open the inlet but no
    exhaust/central path for it to draw through — the interlock the webapp is
    responsible for (firmware treats any register combination as legal; see
    kitchen/RunSpec.h). Covers every phase register set a spec can carry:
    ventRegisters (VENTILATING) and leakRegisters (LEAKING)."""
    for key in ("ventRegisters", "leakRegisters"):
        vr = spec.get(key) or {}
        if bool(vr.get("inlet")) and not (vr.get("central") or vr.get("exhaust")):
            return True
    return False
```

### 3c. `webapp/frontend/src/components/configs/ConfigEditor.tsx`

Add a `RegisterCheckboxes` group + fan slider for the leak phase, same controls
already used for `ventRegisters`/`fanSpeedPct`. Also add the defaults to the
initial spec at ~line 27 (`leakRegisters: {central:false,exhaust:false,inlet:false}`,
`leakFanSpeedPct: 0`).

### 3d. `sim/kitchen_core_sim.py`

The sim ignores `leakRegisters`/`leakFanSpeedPct` today, so a vented-leak run
simulates as sealed. Mirror the firmware's `outputsFor()` LEAKING branch if you
want the sim to exercise this path.

### 3e. Optionally `webapp/app/db/configs.py`

If stored configs are schema-validated or column-mapped, the two new spec keys
need to round-trip. If the spec is stored as opaque JSON, nothing to do.

---

## 3f. Pre-existing bug found while checking this — `ack` rejection reason

**Not caused by the firmware change; broken before it too, and still broken.**
Worth fixing while you are in these files.

Both the firmware (`protocolBuildAck`, `Protocol.cpp:345`) and the sim
(`runtime.py:277`) send the rejection cause as **`rejection`**:

```json
{ "runId": "...", "valid": false, "rejection": "WRONG_ROLE" }
```

Two places read a name that neither publisher sends:

- `webapp/app/runs.py:238`
  `ack.get("reason") or ack.get("rejectReason") or "Rejected by firmware"`
  → never matches, so every rejection is recorded as the generic
  `"Rejected by firmware"` instead of `WRONG_ROLE` / `INVALID_SPEC` /
  `RUN_ID_MISMATCH` / `ARM_TIMED_OUT`.
- `webapp/frontend/src/components/start/ReviewModal.tsx:65`
  `ack?.reason ?? ack?.rejectReason ?? run.outcome_detail ?? '…'`
  → falls through to `outcome_detail`, which (because of the line above) is
  itself the generic string.

Net effect: **the operator is told a run was rejected but never why**, which is
the same class of bug as the `dangerReason` one in section 1.

Fix — add `rejection` as the first lookup in both:

```python
# webapp/app/runs.py:238
reason = (ack.get("rejection") or ack.get("reason")
          or ack.get("rejectReason") or "Rejected by firmware")
```

```tsx
// ReviewModal.tsx:65
{ack?.rejection ??
  ack?.reason ??
  ack?.rejectReason ??
  run.outcome_detail ??
  'The firmware did not answer in time.'}
```

`types.ts:435` already declares `rejection?: string`, so no type change needed.

---

## 4. Hardware change — relays (FYI, no webapp work)

- **Alarm beacon moved** to the Opta **base board RELAY4** (`D3`), freeing a
  D1608E channel.
- **New equipment-test relay** on the freed D1608E ch7 (`RELAY_EQUIP_TEST`):
  closed *only* in equipment-test bench mode (selector in equipment-test **and**
  state `WAITING`), open in every other state — including a mid-run selector
  flip, which the core treats as inert.

One consequence worth knowing: the Opta BSP pairs each base relay with a status
LED (`LED_RELAY4 == LED_D3`), so **`LED_D3` now belongs to the beacon relay**
and the firmware no longer drives it. The 2+2 LED status word lost its high
run-state bit; the four run-state codes are now encoded as blink *rates* on
`LED_D2` (dark / solid / slow / fast), so no state distinction was lost.

`RELAY_EQUIP_TEST`'s channel number is a **PLACEHOLDER** like the rest of the
relay map — confirm it against the physical wiring before energising.

---

## 5. Order to apply

**Required — the webapp is out of sync with the firmware until these are done:**

1. `sim/runtime.py` field renames + `change_key` (2a)
2. `tests/test_scenarios_against_webapp.py:153` (2b) — then run the suite; it
   should go green
3. `webapp/app/runs.py` `_inlet_only` (3b) — the only safety-relevant item here

**Needed before the new leak-vent feature is reachable from the UI:**

4. `types.ts` `RunSpec` (3a), then `ConfigEditor.tsx` (3c)
5. `sim/kitchen_core_sim.py` (3d) if you want the sim to exercise vented leaks

**Independent pre-existing bug, fix whenever:**

6. `rejection` lookup in `runs.py:238` + `ReviewModal.tsx:65` (3f)

### What happens if you do nothing

The firmware runs correctly and safely either way — none of this is in the
safety path. But: the scenario test fails, an inlet-only leak phase bypasses
the webapp interlock (3b), the new leak-vent fields are unreachable from the
UI, and the sim publishes different field names from the firmware, so anything
tested against the sim proves nothing about the real device.
