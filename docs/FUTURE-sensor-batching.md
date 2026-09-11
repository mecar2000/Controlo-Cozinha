# FUTURE — Batched 10 ms sensor publishing

**Status: deferred, not implemented.** This file records a fully-researched design so it
can be picked up later without redoing the investigation. Nothing here is built yet.

## Why this exists

The kitchen PLC can *sample* its 6 local H2 sensors at 10 ms cheaply (one I2C transaction
per tick — see the implemented `expansionRefreshAnalogInputs()` work). What it cannot
cheaply do is *publish* at that rate.

Publishing one MQTT message per sample per sensor at 10 ms means **~600 blocking
`mqttClient.publish()` calls per second** on `EthernetClient`. `PubSubClient::publish()`
blocks on the TCP write; a single retransmit or a slow broker stalls the loop that also
runs the safety interlock. That is the reason batching exists as an idea, and the reason
the near-term implementation decimates the publish rate instead.

Batching gets 10 ms **resolution** into the historian at ~5 msg/s/sensor.

---

## The historian accepts batches — DONE

`DataAcquisition/dashboard/app/ingest.py` accepts `{"samples":[...]}`: `_on_message`
dispatches each entry through `_process_reading()`, so a batch behaves exactly like N
single messages (dedupe, routing, recording gate, SSE, DB enqueue and derived tick all
per sample). `_MAX_BATCH_SAMPLES = 100` caps one message, and `app/state.py::_SEEN_TS_WINDOW`
was raised 32 → 256 so a batch cannot evict its own dedup entries. Equivalence and
backward compatibility are covered by `DataAcquisition/tests/recording/test_ingest_batch.py`.

A payload with no `samples` key takes the original path unchanged, so existing publishers
(CM7 firmware, Turbine PLCs, `cm7_simulator.py`) are unaffected — verified against all
three legacy wire shapes.

## Second constraint: timestamps must be real epoch-ms

`ingest.py:192`:
```python
if isinstance(fw_ts_ms, (int, float)) and fw_ts_ms >= _MIN_REAL_EPOCH_MS:  # 1_500_000_000_000
    ts_ms = int(fw_ts_ms)
else:
    ts_ms = int(wall_ts * 1000)   # server receipt time
```
`millis()`-based timestamps are below that floor, so the historian **replaces them with
receipt time** — collapsing 10 ms spacing into network jitter. Batching is pointless
without a real clock. See the NTP note below.

## Third constraint: de-dup is keyed on `(key, ts_ms)`

`dashboard/app/state.py::_is_duplicate` keeps a per-key deque of recently seen `ts_ms`.
Every sample in a batch must carry a **distinct** `ts`. A batch that stamps all samples
with one timestamp collapses to a single stored row. The 10 ms cadence gives distinct
millisecond values naturally — but the batch builder must stamp each sample at its own
sample time, not at drain time.

Also check `_SEEN_TS_WINDOW` is larger than one batch, or a batch evicts its own entries.

---

## Design

### Wire format

One topic per sensor (matching the existing tree), batched samples:

```
topic: DataAcquisition/Kitchen/mainBoard/<sensorName>      (non-retained)

{"pin":100,"type":"current",
 "samples":[{"raw_ma":12.345,"ts":1737000000801},
            {"raw_ma":12.350,"ts":1737000000811},
            ...]}
```

- `pin` and `type` stay **top-level** — they are per-sensor constants. Only varying fields
  go per-sample. Keeps payloads small and the ingest change minimal.
- **Non-retained.** Retaining a 5 Hz stream would hammer broker disk.
- Build with `snprintf`, **not ArduinoJson**. `Protocol.cpp` already builds payloads this
  way, and the existing `publishState()` pre-gate exists precisely because per-pass
  ArduinoJson builds were too expensive.
- Size: 20 samples × ~40 B ≈ 800 B, within `MQTT_MAX_PAYLOAD` (2048). The builder must
  return 0 on overflow and the caller must **split the batch, never truncate**.

### Firmware pieces

`kitchen/Protocol.h/.cpp`:
```cpp
size_t protocolBuildSensorBatch(char* out, size_t cap,
                                int sensorIdx, int encodedPin,
                                const H2Sample* samples, int count);
```

`kitchen/SensorStream.h/.cpp` — ring buffer feeding the batch:
```cpp
struct H2Sample { uint64_t tsMs; uint16_t counts[...]; float mA[...]; };
void sensorStreamBegin();
void sensorStreamTick(uint32_t nowMs);   // 10 ms sampler
bool sensorStreamDrain(...);             // hand batches to the publisher
```
- Ring of 32 samples = 320 ms at 10 ms, comfortably more than one 200 ms publish window.
- 8 + 6×2 + 6×4 = 44 B per sample; 32 of them ≈ 1.4 KB. Acceptable.
- Overwrite-oldest on overflow **and count drops** — silent data loss must be visible.
- Store the **calibrated mA float** alongside counts. `Sensors.cpp` already applies
  `SENSOR_CALIBRATION_OFFSET_MA` at the read boundary; do **not** invert counts→mA at
  publish time.

Settings:
```c
#define SENSOR_SAMPLE_INTERVAL_MS    10UL
#define SENSOR_PUBLISH_INTERVAL_MS   200UL
#define H2_STREAM_RING_SAMPLES       32
```

### Timestamps — NTP is required, and the CM7's version does NOT work here

`DataAcquisition/CM7/Comms.cpp:204` `syncClock()` calls `WiFi.getTime()` and is wrapped in
`#ifndef USE_ETHERNET` — **it is a no-op on Ethernet.** The kitchen is Ethernet-only, so
it cannot be copied.

Replacement: `NTPClient` (present at `~/Documents/Arduino/libraries/NTPClient`, takes any
`UDP&`) + `EthernetUDP`. API: `begin()`, `update()`, `forceUpdate()`, `getEpochTime()`.

Reuse the CM7's proven anchoring shape (`CM7/Comms.cpp:229 currentTsMs()`):
```cpp
_ntpEpoch = ntp.getEpochTime(); _ntpMs = millis();
// commsNowMs() = _ntpEpoch*1000 + (millis() - _ntpMs)
```
- Bounded sync attempt in `setup()`; non-blocking `ntp.update()` thereafter. **NTP must
  never block the control loop.**
- Explicit fallback: if NTP never succeeds, return `millis()`, let the historian
  substitute receipt time, and **log a warning once** so degraded resolution is visible
  rather than silent.
- NTP on a lab network may be firewalled — this fallback is the whole reason it's explicit.

---

## Consumer changes required (do not skip)

### `DataAcquisition/dashboard/app/ingest.py` — DONE

The wire format the historian now accepts:

```json
{"pin":100,"type":"current","samples":[{"raw_ma":12.34,"ts":1737000000000},
                                       {"raw_ma":12.36,"ts":1737000000010}]}
```

Top-level keys are shared across the batch; per-sample keys override them. Max 100
samples per message.

Settled while implementing:
- `_count(_RECEIVED)` moved inside `_process_reading`, so counters stay per-reading.
- `_SEEN_TS_WINDOW` 32 → 256 (> `_MAX_BATCH_SAMPLES`), asserted by a test.
- **Throughput is a non-issue.** 600 rows/s against `db_writer.py`'s benchmarked
  13,400–16,900 rows/s (`db_writer.py:25-33`) — ~4% of capacity. The stale "40
  readings/s" comment refers only to log volume, not a limit.
- A non-numeric raw field used to raise out of `_on_message` and be swallowed by paho;
  it is now caught and logged, so one bad sample costs only that sample.

Still open, deliberately: `derived.py:~117` debounces derived-sensor emits on the
**reading clock**, so a batch of 10 ms-spaced samples gives `elapsed = 10 ms` for all
but the first. Correct for the default 500 ms interval (emit rate follows sensor time),
but **re-verify before making a batched device an input to a short-interval derived
sensor.** No existing derived sensor has a batched input today.

### `Controlo-Cozinha/webapp/app/mqtt.py`

`_handle_sensor_sample` must handle a `samples[]` batch by taking the **last** sample —
it only stores a latest-value-per-sensor view, so newest wins. Apply the conversion
(`app/conversion.py`) to that one sample only; converting all N and discarding N-1
would be pure waste.

(The non-batch format fix — reading `raw_ma`/`raw_v`/`ts` instead of the
never-published `value`/`ts_ms` — is **done**, along with the mA→%v/v conversion that
uses DataAcquisition's stored calibration.)

---

## Verification when this is picked up

- **Equivalence test:** a batch of N samples must produce exactly the same N records as N
  single messages. That is the one test that matters most.
- `protocolBuildSensorBatch` unit test: exact expected JSON for 2–3 samples; returns 0
  (not truncated output) when `cap` is too small.
- Ring buffer test: FIFO order, overflow overwrites oldest AND increments the drop
  counter, drained timestamps strictly increasing.
- On hardware: `mosquitto_sub -t 'DataAcquisition/Kitchen/mainBoard/#' -v` — confirm
  ~5 msg/s/sensor, `ts` 10 ms apart and **13-digit real epoch**, not small millis.
- Confirm the DAQ overload banner does not fire at 600 rows/s.

## Related ceiling (not solved by batching)

`Controller::wait_for_device_answer` (`Arduino_Opta_Blueprint/src/OptaController.cpp:1075`)
is a **busy-wait spin with a 50 ms timeout**. One unresponsive expansion stalls a 10 ms
loop for five full periods. Batching does not help this; it needs its own decision about
how that failure should behave.
