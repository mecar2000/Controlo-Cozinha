"""
app.config — environment-derived configuration constants (read-only).

Loaded once from the environment / .env. These never change at runtime.
Mirrors DataAcquisition/dashboard/app/config.py's shape so the two backends
read the same way side by side.
"""

import os

from dotenv import load_dotenv

load_dotenv()

# --- MQTT broker (same broker the firmware and DataAcquisition use) ---
MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", 1883))
MQTT_USER = os.getenv("MQTT_USER", "")
MQTT_PASS = os.getenv("MQTT_PASS", "")

# The kitchen device this app controls. Every KitchenControl/{deviceId}/...
# topic is built from this. One app instance controls one device.
KITCHEN_DEVICE_ID = os.getenv("KITCHEN_DEVICE_ID", "KITCHEN-01")

# --- Flask web server ---
FLASK_HOST = "0.0.0.0"
FLASK_PORT = int(os.getenv("FLASK_PORT", 5010))

# Bearer token auth — leave empty in .env to disable auth entirely (matches
# DataAcquisition's model: fine for a lab machine on a trusted network).
DASHBOARD_TOKEN = os.getenv("DASHBOARD_TOKEN", "").strip()

# Extra origins allowed to make state-changing requests, comma-separated.
# Same-origin is always allowed and needs no entry here. The Vite dev server
# (http://localhost:5173) proxies /api, so it is same-origin in practice and
# also needs no entry — this is for a genuinely separate frontend host.
# Origin checking is what stops a page the operator has open from POSTing a
# run start, which matters most when DASHBOARD_TOKEN is empty.
ALLOWED_ORIGINS = frozenset(
    o.strip().rstrip("/")
    for o in os.getenv("ALLOWED_ORIGINS", "").split(",")
    if o.strip()
)

# --- DataAcquisition (the historian, used only through its REST API) ---
DAQ_BASE_URL = os.getenv("DAQ_BASE_URL", "http://localhost:5001").rstrip("/")
DAQ_TOKEN = os.getenv("DAQ_TOKEN", "").strip()
DAQ_TIMEOUT_S = float(os.getenv("DAQ_TIMEOUT_S", 5))
# DataAcquisition location name this kitchen publishes/records under. Already
# a built-in location in DataAcquisition (app/state.py:_BUILTIN_LOCATIONS) —
# nothing to provision, but kept as a setting rather than hardcoded in daq.py.
DAQ_LOCATION = os.getenv("DAQ_LOCATION", "Kitchen")
# Device id the kitchen PLC's OWN local H2 sensors publish under on the
# DataAcquisition/{DAQ_LOCATION}/{id}/... topic tree (kitchen/Kitchen_Settings.h
# KITCHEN_DAQ_DEVICE_ID). Distinct from KITCHEN_DEVICE_ID below, which names
# the KitchenControl/{id}/... control topics — two separate namespaces.
KITCHEN_DAQ_DEVICE_ID = os.getenv("KITCHEN_DAQ_DEVICE_ID", "mainBoard")
# How often to re-fetch DataAcquisition's conversion table (seconds). The
# firmware publishes raw mA; DataAcquisition owns the mA->%v/v definitions and
# this app only reads them (app/conversion.py). Re-fetched periodically so a
# recalibration in DataAcquisition reaches the live view without a restart.
DAQ_CONVERSION_REFRESH_S = float(os.getenv("DAQ_CONVERSION_REFRESH_S", 300))

# --- Fallback when DataAcquisition has no conversion for a sensor ---
# Default (empty): show the RAW mA and mark the reading unconverted, so the
# operator never sees a fabricated concentration on the safety heatmap.
#
# Set H2_FALLBACK_PCT_VV_MAX to enable a hardcoded linear fallback instead —
# H2_FALLBACK_MA_MIN..MAX maps onto 0..H2_FALLBACK_PCT_VV_MAX %v/v. This is a
# deliberate override for running with DataAcquisition unavailable; a wrong
# value here displays plausible-looking bad numbers, which is exactly why it is
# opt-in rather than a default.
_h2_fallback_max = os.getenv("H2_FALLBACK_PCT_VV_MAX", "").strip()
H2_FALLBACK_PCT_VV_MAX = float(_h2_fallback_max) if _h2_fallback_max else None
H2_FALLBACK_MA_MIN = float(os.getenv("H2_FALLBACK_MA_MIN", 4.0))
H2_FALLBACK_MA_MAX = float(os.getenv("H2_FALLBACK_MA_MAX", 20.0))

# --- KitchenControl SQL Server database ---
DB_SERVER = os.getenv("DB_SERVER", "localhost")
DB_NAME = os.getenv("DB_NAME", "KitchenControl")
DB_DRIVER = "{ODBC Driver 17 for SQL Server}"
