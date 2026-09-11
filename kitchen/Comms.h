#ifndef KITCHEN_COMMS_H
#define KITCHEN_COMMS_H

// =============================================================================
// Comms.h — Ethernet + MQTT layer for the kitchen PLC.
//
// Copied from DataAcquisition/CM7/Comms.h and adapted:
//   - Ethernet only (the kitchen is wired, as the retired sketch was).
//   - NTP ADDED (unlike the original "NTP dropped" design): the fast H2
//     sensor stream (SensorStream.cpp) publishes wall-clock timestamps to
//     DataAcquisition, which discards anything below a real-epoch floor and
//     substitutes receipt time instead — so a real clock is what makes those
//     timestamps meaningful. CM7's own syncClock() is WiFi-only
//     (Comms.cpp:204, #ifndef USE_ETHERNET, calls WiFi.getTime()) and is NOT
//     reused here; this uses NTPClient + EthernetUDP instead. See
//     commsSyncClock()/commsNowMs() below.
//   - Three ADDITIVE changes vs CM7 (plan step 5), each defaulted so CM7's own
//     usage would still compile unchanged:
//       1. mqttPublish() gains `bool retain = false`.
//       2. commsSetSubscriptions(topics, count) — call before commsBegin().
//       3. commsSetLwt(topic, payload)          — call before commsBegin().
//
// LED policy (review row 173): Comms no longer drives any status LED. It
// exposes commsLinkUp() / commsMqttConnected() and Outputs owns all four LEDs
// as a single 2+2 status word. Two writers on the same pins would fight.
//
// Dependency direction: Comms knows nothing about the kitchen state machine.
// kitchen.ino injects the MQTT message handler via commsSetMqttCallback().
//
// DIAGNOSTICS (was Log.h) live here too — run-mode-gated logging that mirrors
// to Serial and, at >= VERBOSE, publishes non-retained JSON to the `log`
// topic. It publishes via mqttPublish() in this same file; kitchen.ino still
// injects the sink through logSetPublish() so the topic string stays owned by
// kitchen.ino, matching the commsSetMqttCallback() injection pattern.
// =============================================================================

#include <Ethernet.h>
#include <PubSubClient.h>
#include <stdint.h>
#include "Kitchen_Settings.h"

extern EthernetClient ethClient;
extern PubSubClient   mqttClient;
extern String         deviceId;      // == KITCHEN_DEVICE_ID (fixed, not MAC-derived)

// Register the MQTT message handler (defined in kitchen.ino). Before commsBegin().
void commsSetMqttCallback(MQTT_CALLBACK_SIGNATURE);

// Callback fired after every successful MQTT (re)connect + subscribe. kitchen.ino
// uses this to re-publish its retained state so a broker restart self-heals.
void commsSetOnConnect(void (*cb)());

// ADDITIVE #2 — the exact topic set to (re)subscribe on every connect. Pointers
// must stay valid for the program's life (point them at static strings in
// kitchen.ino). If never called, nothing is subscribed.
void commsSetSubscriptions(const char* const* topics, int count);

// ADDITIVE #3 — Last Will: retained `payload` published to `topic` by the broker
// if this device drops ungracefully. If never called, no LWT is registered.
void commsSetLwt(const char* topic, const char* payload);

// Ethernet + MQTT connect. Blocking at boot, returns once MQTT is connected.
void commsBegin();

// Call every loop() iteration — maintains the MQTT connection (non-blocking
// unless a full reconnect is needed).
void commsLoop();

// Force-drop MQTT so the next commsLoop() does a full reconnect + re-subscribe
// + onConnect. Used when publishes keep failing on a link connected() still
// reports up (half-open socket).
void commsForceReconnect();

// True while the MQTT client reports connected.
bool commsMqttConnected();

// True while the Ethernet PHY reports a live link (LinkON). Outputs reads this
// for the D0 connection bit. Independent of MQTT — link can be up with the
// broker unreachable.
bool commsLinkUp();

// ADDITIVE #1 — `retain` defaults to false (CM7-compatible). Returns true if
// the client accepted the publish.
bool mqttPublish(const char* topic, const char* payload, bool retain = false);

// ---------------------------------------------------------------------------
// NTP — real wall-clock timestamps for the fast sensor stream. See the header
// note above for why this exists and why CM7's syncClock() isn't reused.
// ---------------------------------------------------------------------------

// Attempt to sync the clock, blocking up to ~maxRetries*retryDelayMs. Call
// once from setup() AFTER commsBegin() (needs a live network for the UDP
// exchange). Returns false (and leaves commsNowMs() falling back to millis())
// if every attempt fails — e.g. NTP firewalled on a lab network. Never fatal.
bool commsSyncClock(int maxRetries, int retryDelayMs);

// Call periodically from loop() (e.g. once a pass is fine — internally
// rate-gated to NTP_RESYNC_INTERVAL_MS via NTPClient's own update()). NOTE:
// when a re-sync IS due, the underlying call blocks up to ~1 s waiting for
// the NTP reply (NTPClient::forceUpdate()) — bounded and rare (every
// NTP_RESYNC_INTERVAL_MS), never per-pass, so it does not affect the 10 ms
// sensor sample cadence, but it is not sub-millisecond either.
void commsPumpClock();

// Current wall-clock estimate in epoch milliseconds if NTP has ever synced
// successfully; otherwise millis() (a small number the receiving historian
// recognizes as "not a real clock" and substitutes its own receipt time for —
// see docs/FUTURE-sensor-batching.md). Logs a ONE-SHOT warning the first time
// it is called while still unsynced, so degraded timestamp resolution is
// visible rather than silently accepted.
uint64_t commsNowMs();

// True once commsSyncClock() (or a later commsPumpClock() resync) has
// succeeded at least once.
bool commsClockSynced();

// =============================================================================
// Diagnostics / run modes (was Log.h)
//
// TWO LAYERS of verbosity control:
//   1. COMPILE CEILING — KITCHEN_ENABLE_DEBUG (Kitchen_Settings.h). Undefined
//      in production: DEBUG/PROFILING are compiled to no-ops and cannot be
//      selected at runtime at all.
//   2. RUNTIME SELECTION — the active RunMode, starting at KITCHEN_DEFAULT_MODE
//      and overridable live via the non-retained MQTT `mode` topic (so a
//      reboot always returns to the compiled default).
//
// Ordered ">=" ladder: PROFILING implies VERBOSE implies CLEAN.
//   CLEAN     — errors only
//   VERBOSE   — + state transitions, connect/disconnect
//   DEBUG     — + every MQTT message, sensor counts, decode
//   PROFILING — VERBOSE + per-loop-stage micros() deltas, ~5 s summary
// =============================================================================

enum class RunMode : uint8_t { CLEAN = 0, VERBOSE = 1, DEBUG = 2, PROFILING = 3 };

// KITCHEN_DEFAULT_MODE_ORDINAL (Kitchen_Settings.h) as the enum.
#define KITCHEN_DEFAULT_MODE  ((RunMode)(KITCHEN_DEFAULT_MODE_ORDINAL))

// Log levels a call site tags itself with. ERROR is always emitted.
enum class LogLevel : uint8_t { ERROR = 0, VERBOSE = 1, DEBUG = 2 };

// The publish sink for the MQTT `log` topic. kitchen.ino passes mqttPublish
// + the topic string here; nullptr (the default) means Serial-only.
typedef bool (*LogPublishFn)(const char* topic, const char* payload, bool retain);
void logSetPublish(LogPublishFn fn, const char* logTopic);

// Set / get the active run mode. logSetMode() clamps to the compile ceiling:
// without KITCHEN_ENABLE_DEBUG, DEBUG/PROFILING requests land on VERBOSE.
void    logSetMode(RunMode m);
RunMode logMode();

// True if the active mode is at or above `want` on the ">=" ladder.
bool logAtLeast(RunMode want);

// Emit one line. Mirrors to Serial always (for ERROR) / when the mode allows
// (VERBOSE, DEBUG); additionally publishes non-retained JSON {level,msg,t} to
// the `log` topic when the mode is >= VERBOSE and a sink is registered.
void logPublish(LogLevel level, const char* msg);

// printf-style convenience — formats into a small stack buffer then logPublish.
void logPrintf(LogLevel level, const char* fmt, ...);

// --- PROFILING: per-loop-stage micros() deltas ----------------------------
// No-ops unless the mode is PROFILING. profileStage() records the time since
// the previous profileStage()/profileLoopStart() call under `name`;
// profileLoopEnd() rolls the pass into a ~5 s rolling summary logPublish'd at
// VERBOSE level.
void profileLoopStart();
void profileStage(const char* name);
void profileLoopEnd();

#endif // KITCHEN_COMMS_H
