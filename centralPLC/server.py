"""
Safety MQTT <-> Firebase bridge — the Central Safety System's backend
-----------------------------------------------------------------------
This is the ONLY component that holds Firebase credentials. Every PLC
(Central and every experiment) publishes directly onto the shared MQTT
status/ tree; this bridge is what turns that into (a) Firebase writes for
the dashboard/app and (b) the per-experiment "permit" heartbeat that is
the actual stop/go signal experiment PLCs act on.

Topics consumed (see README for the full scheme):
  status/<ExperimentName>/<deviceId>/online                  "online"/"offline"
  status/<ExperimentName>/<deviceId>/run                     {"running","runId"}
  status/<ExperimentName>/<deviceId>/alarm/<labId>/<hazard>   {"active","locationDetail","description"}

Topics published:
  safety/permit/<deviceId>   {"permit":bool,"seq":N}   — not retained, ~1s heartbeat

Firebase:
  Reads  experimentDefs/<defId>  (name, nameKey, labId, mayEmit) — polled, cached.
         <ExperimentName> from the topic is matched against name/nameKey
         (case-insensitive) to find the defId — devices never need to know
         their own opaque defId, just publish under a name that matches
         what's set in experimentDefs.
  Writes alarms/labs/<labId>     {active, source, locationDetail, description, since}
  Writes experimentRuns/<runId>  on PLC-initiated start/stop
  Writes labs/<labId>/activeRuns/<runId>  (pure index, per existing schema convention)

Fail-safe by construction: if this process dies, permits simply stop being
published, and every experiment PLC's own staleness timer (not this process)
trips it to a safe state. A Firebase read failure keeps the last-known-good
mayEmit cache rather than ever causing a spurious trip.
"""

import json
import logging
import os
import time
from pathlib import Path

import paho.mqtt.client as mqtt
import firebase_admin
from firebase_admin import credentials, db
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
MQTT_BROKER    = os.getenv("MQTT_BROKER",    "192.168.20.199")
MQTT_PORT      = int(os.getenv("MQTT_PORT",  1883))
MQTT_USERNAME  = os.getenv("MQTT_USERNAME",  "Safety")
MQTT_PASSWORD  = os.getenv("MQTT_PASSWORD",  "")
MQTT_CLIENT_ID = "Safety-bridge"

STATUS_WILDCARD = "status/#"

FIREBASE_CRED_FILE = Path(__file__).parent / os.getenv("FIREBASE_CRED_FILE", "firebase_credentials.json")
FIREBASE_DB_URL    = os.getenv("FIREBASE_DB_URL", "https://hylabguard-292aa-default-rtdb.europe-west1.firebasedatabase.app/")

DEFS_POLL_SECS       = int(os.getenv("DEFS_POLL_SECS", 30))
PERMIT_INTERVAL_SECS = float(os.getenv("PERMIT_INTERVAL_SECS", 1.0))

# ---------------------------------------------------------------------------
# In-memory state (all rebuilt from retained MQTT state + Firebase on restart)
# ---------------------------------------------------------------------------
# (labId, hazardType) -> {"active": bool, "source": deviceId, "locationDetail": str, "description": str}
alarm_zones: dict[tuple[str, str], dict] = {}
lab_alarm_since: dict[str, float] = {}          # labId -> epoch when it last went active

# deviceId -> {"defId": str, "runId": str, "labId": str|None}
experiment_state: dict[str, dict] = {}

# defId -> {"labId": str, "mayEmit": set[str]}
experiment_defs: dict[str, dict] = {}

# normalized name/nameKey -> defId — lets a device identify its experimentDefs
# entry purely by the <ExperimentName> already in its topic path.
defs_by_name: dict[str, str] = {}

last_published_permit: dict[str, bool] = {}  # deviceId -> last permit value (log only on change)
warned_unresolved: set[str] = set()          # deviceId -> already logged "skipped" once


def normalize_name(s: str) -> str:
    return (s or "").strip().lower()


# ---------------------------------------------------------------------------
# Firebase
# ---------------------------------------------------------------------------
def init_firebase():
    """Initialise the Firebase Admin SDK and return refs used by this bridge."""
    cred = credentials.Certificate(FIREBASE_CRED_FILE)
    firebase_admin.initialize_app(cred, {"databaseURL": FIREBASE_DB_URL})
    log.info("Firebase initialised")
    return {
        "alarms_labs":     db.reference("alarms/labs"),
        "experiment_defs": db.reference("experimentDefs"),
        "experiment_runs": db.reference("experimentRuns"),
        "labs":            db.reference("labs"),
    }


def refresh_experiment_defs(defs_ref) -> None:
    """
    Poll experimentDefs -> {defId: {labId, mayEmit}} + name/nameKey -> defId.
    mayEmit is a plain boolean in the real schema (".validate": "newData.isBoolean()"),
    not a per-hazard-type list — it means "this experiment may cause hazards as
    part of normal operation, in its own lab", full stop. Keeps last-good cache
    on read failure.
    """
    try:
        raw = defs_ref.get() or {}
    except Exception as exc:
        log.error("experimentDefs read failed: %s — keeping cached defs", exc)
        return
    for def_id, entry in raw.items():
        if not entry:
            continue
        experiment_defs[def_id] = {
            "labId":   entry.get("labId"),
            "mayEmit": bool(entry.get("mayEmit", False)),
        }
        for key in (entry.get("nameKey"), entry.get("name")):
            if key:
                defs_by_name[normalize_name(key)] = def_id
    log.info("experimentDefs loaded: %d entries, names known=%s", len(raw), sorted(defs_by_name.keys()))


def write_lab_alarm(refs, lab_id: str) -> None:
    """Aggregate all zones in a lab and write the active/description summary."""
    zones_in_lab = {k: v for k, v in alarm_zones.items() if k[0] == lab_id}
    active = any(z["active"] for z in zones_in_lab.values())

    if active:
        lab_alarm_since.setdefault(lab_id, time.time())
        source_zone = next(z for z in zones_in_lab.values() if z["active"])
        payload = {
            "active":         True,
            "source":         source_zone["source"],
            "locationDetail": source_zone["locationDetail"],
            "description":    source_zone["description"],
            "since":          lab_alarm_since[lab_id],
        }
    else:
        lab_alarm_since.pop(lab_id, None)
        payload = {"active": False, "source": None, "locationDetail": "", "description": "", "since": None}

    try:
        refs["alarms_labs"].child(lab_id).update(payload)
        log.info("Firebase alarms/labs/%s updated: %s", lab_id, payload)
    except Exception as exc:
        log.error("Firebase write failed for labs/%s: %s", lab_id, exc)


def start_plc_run(refs, device_id: str, def_id: str) -> str:
    """
    Mint a run id for a PLC-initiated start and write experimentRuns +
    labs/activeRuns. Real schema requires startedByUid/startedByEmail (two
    fields, no plain human/uid distinction for a PLC) — using a synthetic
    "plc:<deviceId>" identity in both, since there's no real user here.
    Confirm this placeholder convention with whoever owns the app's Dart code.
    """
    run_id = f"plc-{device_id}-{int(time.time())}"
    lab_id = experiment_defs.get(def_id, {}).get("labId")
    now = time.time()
    identity = f"plc:{device_id}"
    try:
        refs["experiment_runs"].child(run_id).set({
            "defId":          def_id,
            "labId":          lab_id,
            "status":         "running",
            "startedByUid":   identity,
            "startedByEmail": identity,
            "startedAt":      now,
        })
        log.info("Firebase write OK: experimentRuns/%s = running (defId=%s labId=%s startedBy=%s)",
                  run_id, def_id, lab_id, identity)
        if lab_id:
            refs["labs"].child(lab_id).child("activeRuns").child(run_id).set(True)
            log.info("Firebase write OK: labs/%s/activeRuns/%s = true", lab_id, run_id)
        else:
            log.warning("experimentDefs/%s has no labId — labs/<lab>/activeRuns not updated", def_id)
    except Exception as exc:
        log.error("Firebase write FAILED starting run for %s: %s", device_id, exc)
    return run_id


def stop_plc_run(refs, device_id: str, run_id: str, lab_id: str | None) -> None:
    identity = f"plc:{device_id}"
    try:
        refs["experiment_runs"].child(run_id).update({
            "status":         "stopped",
            "stoppedByUid":   identity,
            "stoppedByEmail": identity,
            "stoppedAt":      time.time(),
        })
        log.info("Firebase write OK: experimentRuns/%s = stopped (stoppedBy=%s)", run_id, identity)
        if lab_id:
            refs["labs"].child(lab_id).child("activeRuns").child(run_id).delete()
            log.info("Firebase write OK: labs/%s/activeRuns/%s removed", lab_id, run_id)
    except Exception as exc:
        log.error("Firebase write FAILED stopping run %s: %s", run_id, exc)


# ---------------------------------------------------------------------------
# Permit computation
# ---------------------------------------------------------------------------
def compute_permit(exp_lab_id: str, exp_def_id: str) -> bool:
    """
    Fail-safe by default: an alarm anywhere stops an experiment UNLESS the
    alarm's lab matches the experiment's own lab AND that experiment's
    defId has mayEmit=true (expected to cause hazards during its own normal
    operation, in its own lab only). mayEmit is a single lab-wide boolean in
    the real schema, not per-hazard-type — hazardType is tracked for
    Firebase/description purposes only, it doesn't affect this decision.
    """
    may_emit = experiment_defs.get(exp_def_id, {}).get("mayEmit", False)
    for (lab_id, hazard_type), zone in alarm_zones.items():
        if not zone["active"]:
            continue
        if lab_id == exp_lab_id and may_emit:
            continue
        return False
    return True


# ---------------------------------------------------------------------------
# MQTT callbacks
# ---------------------------------------------------------------------------
def on_connect(client, userdata, flags, reason_code, properties):
    if reason_code == 0:
        log.info("MQTT connected to %s:%s", MQTT_BROKER, MQTT_PORT)
        client.subscribe(STATUS_WILDCARD, qos=1)
        log.info("Subscribed to %s", STATUS_WILDCARD)
    else:
        log.error("MQTT connection failed, reason_code=%s", reason_code)


def on_disconnect(client, userdata, flags, reason_code, properties):
    log.warning("MQTT disconnected (reason_code=%s) — will auto-reconnect", reason_code)


def on_status_message(client, userdata, msg):
    """Dispatch status/<ExperimentName>/<deviceId>/<...> messages."""
    refs = userdata
    parts = msg.topic.split("/")
    raw = msg.payload.decode().strip()

    log.info("MQTT <- %s : %s", msg.topic, raw)

    if len(parts) == 4 and parts[3] == "online":
        return  # already logged above; nothing else to do

    if len(parts) == 4 and parts[3] == "run":
        exp_name, device_id = parts[1], parts[2]
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            log.error("Invalid JSON on %s: %r", msg.topic, raw)
            return

        def_id = defs_by_name.get(normalize_name(exp_name))
        if def_id is None:
            log.warning("No experimentDefs match for name=%r (device=%s) — "
                        "create/rename an experimentDefs entry with name or nameKey=%r",
                        exp_name, device_id, exp_name)
            return

        running = bool(data.get("running"))
        state = experiment_state.setdefault(device_id, {"defId": None, "runId": None, "labId": None})

        if running and state["runId"] is None:
            state["defId"] = def_id
            state["labId"] = experiment_defs.get(def_id, {}).get("labId")
            state["runId"] = start_plc_run(refs, device_id, def_id)
        elif not running and state["runId"] is not None:
            stop_plc_run(refs, device_id, state["runId"], state["labId"])
            state["runId"] = None
        return

    if len(parts) == 6 and parts[3] == "alarm":
        lab_id, hazard_type = parts[4], parts[5]
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            log.error("Invalid JSON on %s: %r", msg.topic, raw)
            return
        if "active" not in data:
            return
        alarm_zones[(lab_id, hazard_type)] = {
            "active":         bool(data["active"]),
            "source":         parts[2],
            "locationDetail": data.get("locationDetail", ""),
            "description":    data.get("description", ""),
        }
        log.info("Zone %s/%s (%s) -> active=%s", lab_id, hazard_type, parts[2], data["active"])
        write_lab_alarm(refs, lab_id)
        return


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    refs = init_firebase()
    refresh_experiment_defs(refs["experiment_defs"])

    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=MQTT_CLIENT_ID,
        userdata=refs,
    )
    client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
    client.on_connect    = on_connect
    client.on_disconnect = on_disconnect
    client.message_callback_add(STATUS_WILDCARD, on_status_message)

    backoff = 2
    while True:
        try:
            log.info("Connecting to MQTT broker %s:%s ...", MQTT_BROKER, MQTT_PORT)
            client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
            break
        except (TimeoutError, OSError) as exc:
            log.warning("Connect failed: %s — retrying in %ds", exc, backoff)
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)

    client.loop_start()
    log.info("Bridge running — subscribed to %s, publishing permits every %ss",
              STATUS_WILDCARD, PERMIT_INTERVAL_SECS)

    seq = 0
    last_defs_poll = 0.0
    try:
        while True:
            now = time.time()
            if now - last_defs_poll >= DEFS_POLL_SECS:
                refresh_experiment_defs(refs["experiment_defs"])
                last_defs_poll = now

            seq += 1
            for device_id, state in experiment_state.items():
                if not state["labId"] or not state["defId"]:
                    if device_id not in warned_unresolved:
                        warned_unresolved.add(device_id)
                        log.warning("Skipping permits for %s — no labId/defId resolved yet "
                                    "(device fail-safes via its own staleness timeout)", device_id)
                    continue
                warned_unresolved.discard(device_id)
                permit = compute_permit(state["labId"], state["defId"])
                if last_published_permit.get(device_id) != permit:
                    last_published_permit[device_id] = permit
                    log.info("Permit for %s (labId=%s) -> %s", device_id, state["labId"], permit)
                payload = json.dumps({"permit": permit, "seq": seq})
                client.publish(f"safety/permit/{device_id}", payload, qos=0, retain=False)

            time.sleep(PERMIT_INTERVAL_SECS)
    except KeyboardInterrupt:
        pass
    finally:
        client.loop_stop()


if __name__ == "__main__":
    main()