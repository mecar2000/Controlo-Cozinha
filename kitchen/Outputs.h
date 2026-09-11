#ifndef KITCHEN_OUTPUTS_H
#define KITCHEN_OUTPUTS_H

// =============================================================================
// Outputs.h — THE ONLY PIN WRITER (plan invariant 1).
//
// Nothing else in the firmware calls digitalWrite/analogWrite on a control pin
// or expansionWriteVoltage/expansionSetRelay/expansionWritePwm. outputsDrive()
// takes the OutputRequest that KitchenCore::update() returned and actuates it:
//   - vent-register coils via RegisterSequencer (owns the inlet-open delay, so
//     the ordering is identical for danger-forced and spec-driven transitions)
//   - gas relay + flowmeter setpoint DAC (the two independent gas cuts)
//   - fan on/off relay + fan speed DAC
//   - alarm relay
//   - the "gas may be present" breathing lamp (A0602 PWM channel)
//   - ALL FOUR on-board LEDs as one 2+2 status word (connection / run-state,
//     with an alarm whole-bank strobe and a peer-stale slow-blink). Comms no
//     longer drives any LED — two writers on the same pins would fight.
//
// Sensor-power fields on OutputRequest (localSensorsOn / remoteSensorsOn) are
// NOT actuated here — local power has no dedicated pin in this build and the
// remote command is an MQTT publish owned by kitchen.ino.
// =============================================================================

#include <stdint.h>
#include "KitchenCore.h"        // OutputRequest, RegisterSequencer, CoilStates

// Call once in setup() AFTER expansionBegin(). Notes the D1608E relay index and
// arms the O1/O2 DAC output channels, drives everything to the safe rest state
// (gas shut, fans idle, no alarm).
void outputsBegin();

// Call every loop() iteration with the request from KitchenCore::update() and
// millis().
//   peerAlarmStale  — drives the distinct stale-peer LED slow-blink; comes
//                     straight off SensorState (a warn, not a core state).
//   state           — core.state(), for the D3/D2 run-state LED bits.
//   isLeakTestRole  — sensorsState().isLeakTestRole; EQUIPMENT_TEST shares the
//                     LEAKING LED code.
//   roleMisflip     — core.roleMisflip(): selector in equipment-test while the
//                     core is not in WAITING. Slow-blinks ONLY D3/D2 at the
//                     real run-state code so an operator sees the true state.
void outputsDrive(const OutputRequest& req, uint32_t nowMs, bool peerAlarmStale,
                  KitchenState state, bool isLeakTestRole, bool roleMisflip);

#endif // KITCHEN_OUTPUTS_H
