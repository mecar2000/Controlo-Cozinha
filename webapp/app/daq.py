"""
app.daq — HTTP client for DataAcquisition, used through its public REST API
only. No shared modules, no direct SQL against its database — see the design
spec's "The boundary with DataAcquisition" rules. This is the ONLY module in
this app that imports requests / talks to DAQ_BASE_URL.

The browser never talks to DataAcquisition directly — this backend holds the
token server-side and proxies whatever the frontend needs through its own
routes (design spec, "Auth").
"""

from typing import Optional

import requests

from app.config import DAQ_BASE_URL, DAQ_TOKEN, DAQ_TIMEOUT_S, DAQ_LOCATION


class DaqError(Exception):
    """Base for every failure talking to DataAcquisition, so a caller that
    only needs 'this did not work' can catch one thing."""


class DaqUnreachable(DaqError):
    """Raised when DataAcquisition cannot be reached or errors. Callers that
    gate run-start on this must treat it as 'starting is blocked' (design
    spec, 'Availability' table) — never as 'DAQ says no', which is a
    different, more specific outcome."""


class DaqRejected(DaqError):
    """DataAcquisition was reached and answered 4xx — it understood the
    request and refused it. Distinct from DaqUnreachable: this IS 'DAQ says
    no', and the operator needs the reason rather than a transport error."""

    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.status_code = status_code


def _headers() -> dict:
    h = {}
    if DAQ_TOKEN:
        h["Authorization"] = f"Bearer {DAQ_TOKEN}"
    return h


def _error_detail(resp) -> str:
    """DataAcquisition returns {"error": ...} on refusal; fall back to the
    status line when the body isn't the JSON we expect."""
    try:
        body = resp.json()
    except ValueError:
        return f"{resp.status_code} {resp.reason}"
    if isinstance(body, dict) and body.get("error"):
        return str(body["error"])
    return f"{resp.status_code} {resp.reason}"


def _request(method: str, path: str, **kwargs) -> dict:
    url = f"{DAQ_BASE_URL}{path}"
    try:
        resp = requests.request(
            method, url, headers=_headers(), timeout=DAQ_TIMEOUT_S, **kwargs
        )
    except requests.exceptions.RequestException as exc:
        raise DaqUnreachable(f"{method} {path} failed: {exc}") from exc
    if resp.status_code >= 500:
        raise DaqUnreachable(f"{method} {path} returned {resp.status_code}")
    if resp.status_code >= 400:
        # Never let a bare requests.HTTPError escape: no caller in this app
        # catches it, so a DAQ 404 would surface as a 500 from this backend.
        raise DaqRejected(f"{method} {path}: {_error_detail(resp)}", resp.status_code)
    if resp.content:
        try:
            return resp.json()
        except ValueError as exc:
            raise DaqUnreachable(
                f"{method} {path} returned a non-JSON body"
            ) from exc
    return {}


def ping() -> bool:
    """Cheap reachability check for the Availability banner / start-gating.
    Never raises — returns False on any failure."""
    try:
        _request("GET", "/status")
        return True
    except Exception:
        return False


def ensure_kitchen_location() -> None:
    """
    Registers the Kitchen location if DataAcquisition doesn't already know
    it. In practice DataAcquisition ships "Kitchen" as a built-in location
    (app/state.py: _BUILTIN_LOCATIONS), so this is normally a no-op — kept as
    a defensive startup check, not a real prerequisite.
    """
    locations = _request("GET", "/locations")
    names = locations if isinstance(locations, list) else locations.get("locations", [])
    if DAQ_LOCATION in names:
        return
    try:
        _request("POST", "/locations", json={"name": DAQ_LOCATION})
    except Exception as exc:
        print(f"[DAQ] Could not register location {DAQ_LOCATION!r}: {exc}")


def list_experiments() -> list[dict]:
    # DataAcquisition wraps the list: {"experiments": [...], "active_id":
    # ..., "active_name": ..., "location": ...}, not a bare array.
    body = _request("GET", "/experiments")
    return body if isinstance(body, list) else body.get("experiments", [])


def create_experiment(name: str) -> dict:
    # Wrapped as {"experiment": {...}, "ok": true}, not the experiment
    # itself — experiment_id_of() below unwraps this same shape.
    return _request("POST", "/experiments", json={"name": name})


def experiment_id_of(created: dict) -> int:
    """
    Pull the experiment id out of a create_experiment() response.

    Raises rather than returning None: an id that quietly went missing would
    flow into create_run(daq_experiment_id=None), producing a run the operator
    believes is being recorded but which is stored as recorded=0.
    """
    experiment = created.get("experiment") if isinstance(created.get("experiment"), dict) else created
    experiment_id = experiment.get("id") or experiment.get("experiment_id")
    if experiment_id is None:
        raise DaqUnreachable(
            f"DataAcquisition created an experiment but returned no id: {created!r}"
        )
    return int(experiment_id)


def set_active_experiment(experiment_id: int) -> dict:
    return _request(
        "POST", "/experiments/active",
        json={"experiment_id": experiment_id, "location": DAQ_LOCATION},
    )


def list_stages(experiment_id: int) -> list[dict]:
    # Wrapped as {"stages": [...], "counts": [...]}, not a bare array.
    body = _request("GET", f"/experiments/{experiment_id}/stages")
    return body if isinstance(body, list) else body.get("stages", [])


def set_stage(stage: str) -> dict:
    """Called on each firmware phase transition (auto) or by operator
    override (manual) — see the design spec's 'Stage mapping' section."""
    return _request("POST", "/stage", json={"location": DAQ_LOCATION, "stage": stage})


def start_recording(experiment_name: Optional[str] = None) -> dict:
    body = {"location": DAQ_LOCATION, "recording": True}
    if experiment_name:
        body["experiment_name"] = experiment_name
    return _request("POST", "/recording", json=body)


def stop_recording() -> dict:
    return _request("POST", "/recording", json={"location": DAQ_LOCATION, "recording": False})


def recording_status() -> dict:
    return _request("GET", "/recording", params={"location": DAQ_LOCATION})


def get_history_experiment(experiment_id: int, **params) -> dict:
    """LTTB-downsampled history for replay (design spec, 'Replay').

    DataAcquisition's own /history/experiment route (dashboard/app/routes/
    data.py) reads the experiment id as `id`, not `experiment_id` — this
    app's internal `/api/daq/...` routes and RunSpec fields keep the more
    readable `experiment_id` name throughout; only the outbound param to
    DataAcquisition itself is renamed here, at the one place that crosses
    the boundary. Sending `experiment_id` used to get back DAQ's own
    "id param required" 400 (problems.txt).
    """
    return _request("GET", "/history/experiment", params={"id": experiment_id, **params})


def get_history_window(experiment_id: int, start_ms: int, end_ms: int, **params) -> dict:
    """Full-resolution zoom window, matching DataAcquisition's own analysis
    behaviour — used at high replay zoom."""
    return _request(
        "GET", "/history/experiment/window",
        params={"id": experiment_id, "start_ms": start_ms, "end_ms": end_ms, **params},
    )


def get_history_experiment_stage(experiment_id: int, stage: str) -> dict:
    """Readings restricted to one named stage within an experiment (e.g. just
    the "hold" phase's sensor data) — DataAcquisition's data.py:
    get_experiment_stage_history, which requires BOTH `id` and `stage`.

    NOT a list of stage boundaries despite the similar name — this app's own
    list_stages()/`/api/daq/experiments/<id>/stages` (-> DataAcquisition's
    /experiments/<id>/stages) is the boundary-ish listing (stage label +
    reading count + started_at; there is currently no DataAcquisition
    endpoint that returns an end time per stage, so a real {stage, start_ms,
    end_ms} timeline cannot be built from either call as-is — pre-existing
    gap, not something this rename could fix).
    """
    return _request("GET", "/history/experiment/stage", params={"id": experiment_id, "stage": stage})


def get_conversions(device_id: str) -> dict[str, dict]:
    """Every sensor's calibration for one device, keyed by sensor_name.

    DataAcquisition owns conversion definitions (design spec, "Division of
    responsibility") — this app READS them and never defines its own, so a
    recalibration there is picked up here without a second place to edit.

    `GET /conversions/{device_id}` is the only conversion READ endpoint DAQ
    exposes; the per-sensor path is POST/DELETE only. It currently answers
    {"conversions": {sensor_name: {...}, ...}, "conversions_list": [...]}
    (dashboard/app/routes/data.py::get_conversions) — "conversions" is a
    DICT already keyed by sensor_name despite that route's own docstring
    calling it "a list" (conversions_list is the actual list). Handle both:
    a dict is used directly; a list (the shape this app originally assumed,
    and what the confusingly-named field suggests) is re-keyed the same way.
    """
    body = _request("GET", f"/conversions/{device_id}")
    convs = body.get("conversions", []) if isinstance(body, dict) else []
    if isinstance(convs, dict):
        return convs
    return {c["sensor_name"]: c for c in convs if isinstance(c, dict) and c.get("sensor_name")}


def set_conversion(
    device_id: str, sensor_name: str, *,
    type: Optional[str], method: str, params: dict, unit_symbol: str,
) -> dict:
    """Create or update one sensor's calibration in DataAcquisition.

    This is the write half of get_conversions() — added so this app's own
    calibration editor can write through to DAQ's store instead of owning a
    second copy. DAQ stays the single source of truth: the same row backs
    both this app's live conversion and the historian's stored `conv_id`
    provenance, so live and history can never disagree about what a number
    means (design spec, "Division of responsibility").

    `POST /conversions/{device_id}/{sensor_name}`, preferred new body shape
    (DataAcquisition/dashboard/app/routes/data.py::set_conversion):
        {"type": ..., "method": ..., "params": {...}, "unit_symbol": ...}
    `method` must be one this app's applier supports (app.conversion) — the
    caller is expected to have already rejected "custom" before this point,
    since accepting one here would silently read back as unconverted.
    """
    body = {"method": method, "params": params, "unit_symbol": unit_symbol}
    if type is not None:
        body["type"] = type
    return _request("POST", f"/conversions/{device_id}/{sensor_name}", json=body)


def delete_conversion(device_id: str, sensor_name: str) -> dict:
    """Remove one sensor's calibration in DataAcquisition — it reverts to raw
    passthrough there, and this app's next read will see no conversion for
    it (converted=False on the next sample, per app.conversion)."""
    return _request("DELETE", f"/conversions/{device_id}/{sensor_name}")


# --- Pin map (Part 5: PLC/sensor commissioning wizard) ----------------------
#
# DataAcquisition owns the pin map (which pin on which device is active, its
# hardware type); this app is only ever an editor over its existing REST API
# — see the design spec's "The boundary with DataAcquisition" rules. No
# second copy of the pin list lives in this app's own database.


def list_devices() -> list[dict]:
    """Every device DataAcquisition has seen, with its capabilities and
    current pin configuration.

    `GET /devices` (dashboard/app/routes/core.py) answers
    {"devices": [{device_id, location, status, expansions, base_pins,
    interval_ms, config: {sensors: [{pin, name, type}]}}, ...]} — unwrapped
    here the same way list_experiments() unwraps its envelope.
    """
    body = _request("GET", "/devices")
    return body if isinstance(body, list) else body.get("devices", [])


def pin_label(pin: int) -> str:
    """Encode a raw pin integer the way DataAcquisition displays it.

    Mirrors dashboard/app/conversion.py::_pin_label exactly — base board
    pins 0..7 are "A0".."A7"; expansion pins are 100*(expansion_index+1) +
    channel, decoded back to "E{expansion_index}:CH{channel}". This is the
    only place in this app that implements the encoding; kept beside the
    DAQ-shaped device/pin data it describes rather than as an HTTP call,
    since it is pure local arithmetic DataAcquisition already agrees on.
    """
    if pin >= 100:
        exp_idx = (pin // 100) - 1
        channel = pin % 100
        return f"E{exp_idx}:CH{channel}"
    return f"A{pin}"


def push_config(
    device_id: str, sensors: list[dict], *, interval_ms: Optional[int] = None,
    location: Optional[str] = None,
) -> dict:
    """Replace a device's whole pin map in DataAcquisition.

    `POST /config` (dashboard/app/routes/core.py::push_config) is a FULL
    REPLACE: DataAcquisition DELETEs and re-INSERTs the device's entire
    `device_sensors` list (dashboard/db/devices.py::save_device_config) —
    there is no per-pin add/remove and no version/etag, so the last write
    wins unconditionally. The caller (routes/daq_proxy.py) must re-fetch
    GET /devices immediately before calling this and send the COMPLETE
    sensor list every time; a partial list here silently drops every pin
    left out of it.

    `sensors` is `[{"pin": int, "name": str, "type": str}, ...]`, the exact
    shape GET /devices' `config.sensors` already returns (see list_devices),
    so a round-trip read-modify-write needs no reshaping. DataAcquisition
    also rejects a pin referencing an unreported expansion, and a device
    with no known location — see the design spec's constraint 2; a PLC
    cannot be configured until it has published at least once.
    """
    body: dict = {"device_id": device_id, "sensors": sensors}
    if interval_ms is not None:
        body["interval_ms"] = interval_ms
    if location is not None:
        body["location"] = location
    return _request("POST", "/config", json=body)


def delete_device(device_id: str) -> dict:
    """Permanently remove a device from DataAcquisition: its config, sensor
    list, and stored conversions (dashboard/app/routes/core.py::
    delete_device_route). Does not touch historical readings, which live in
    per-experiment tables keyed by device id, not by the device row itself.

    This app's own sensor_config has no foreign key on daq_device_id/daq_pin
    — a Cozinha sensor still pointing at a deleted device becomes exactly
    the existing "bound to a pin the device no longer reports" case the
    frontend's device pane already surfaces (Part 5's DeviceList/DevicePane
    unbound-pin cross-reference), so no cascade is needed here, same as
    when a single pin is dropped from a device's config via push_config.

    If the device publishes again later, DataAcquisition simply
    re-registers it with defaults, same as any other never-seen device.
    """
    return _request("DELETE", f"/devices/{device_id}")
