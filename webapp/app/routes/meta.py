"""routes.meta — backend-derived settings the frontend needs to know without
duplicating them as a hardcoded constant of its own.

KITCHEN_DAQ_DEVICE_ID ("mainBoard" by default — see app/config.py) is one
example: SensorPanel.tsx used to hardcode its own copy
(KITCHEN_DEVICE_ID = 'mainBoard') to recognise the kitchen PLC's read-only
pins in the commissioning wizard. That duplicated a setting this backend
already owns and could drift from it silently if the env var were ever
changed. This endpoint is the one place that value now comes from.

daq_dashboard_url is DAQ_BASE_URL itself: DataAcquisition's dashboard UI and
its REST API are the SAME Flask app on the SAME port
(DataAcquisition/dashboard/server.py serves both), so the link the frontend
needs to open DataAcquisition's own calibration editor is exactly the base
URL this app already talks to — not a second setting that could drift out
of sync with it.
"""

from flask import Blueprint, jsonify

import app.config as config
from app.auth import require_auth

bp = Blueprint("meta", __name__)


@bp.get("/api/meta")
@require_auth
def get_meta():
    return jsonify({
        "kitchen_daq_device_id": config.KITCHEN_DAQ_DEVICE_ID,
        "kitchen_device_id": config.KITCHEN_DEVICE_ID,
        "daq_dashboard_url": config.DAQ_BASE_URL,
    })
