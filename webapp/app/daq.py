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
    return _request("GET", "/experiments")


def create_experiment(name: str) -> dict:
    return _request("POST", "/experiments", json={"name": name})


def experiment_id_of(created: dict) -> int:
    """
    Pull the experiment id out of a create_experiment() response.

    Raises rather than returning None: an id that quietly went missing would
    flow into create_run(daq_experiment_id=None), producing a run the operator
    believes is being recorded but which is stored as recorded=0.
    """
    experiment_id = created.get("id") or created.get("experiment_id")
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
    return _request("GET", f"/experiments/{experiment_id}/stages")


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
    """LTTB-downsampled history for replay (design spec, 'Replay')."""
    return _request("GET", "/history/experiment", params={"experiment_id": experiment_id, **params})


def get_history_window(experiment_id: int, start_ms: int, end_ms: int, **params) -> dict:
    """Full-resolution zoom window, matching DataAcquisition's own analysis
    behaviour — used at high replay zoom."""
    return _request(
        "GET", "/history/experiment/window",
        params={"experiment_id": experiment_id, "start_ms": start_ms, "end_ms": end_ms, **params},
    )


def get_history_experiment_stage(experiment_id: int) -> list[dict]:
    """Phase boundaries — what makes 'compare the decay curve across every
    run' a single query (design spec, 'Stage mapping')."""
    return _request("GET", "/history/experiment/stage", params={"experiment_id": experiment_id})


def get_conversion(device_id: str, sensor_name: str) -> dict:
    """Display calibration provenance (conv_id) alongside a reading."""
    return _request("GET", f"/conversions/{device_id}/{sensor_name}")
