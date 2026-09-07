"""
server.py — Kitchen H2 Control webapp backend (entry-point shim).

The implementation lives in the app/ package:
  app/config.py       environment-derived configuration constants
  app/state.py        all shared mutable state + locks
  app/mqtt.py         broker connection; subscribes live state + sensor topics
  app/commands.py     the ONLY module that publishes to KitchenControl/.../cmd
  app/daq.py          HTTP client for DataAcquisition (its public REST API only)
  app/runs.py         start_run() — the single entry point; orchestrates
                       DAQ recording + firmware confirm
  app/layout.py       sensor config, layout snapshots
  app/phase_watcher.py watches firmware phase transitions -> stage mapping + outcomes
  app/db/             KitchenControl SQL Server layer
  app/routes/         HTTP endpoints for the frontend, as Flask blueprints

Usage:
    pip install -r requirements.txt
    cp .env.example .env   # fill in DB_SERVER, MQTT_HOST, DAQ_BASE_URL, etc.
    python server.py

Then open http://localhost:5010 in your browser (once the frontend exists).
"""

from app import create_app, start_background
from app.config import FLASK_HOST, FLASK_PORT, DASHBOARD_TOKEN

app = create_app()

if __name__ == "__main__":
    start_background()
    auth_note = " (auth enabled)" if DASHBOARD_TOKEN else " (auth disabled)"
    print(f"[Web] Kitchen control at http://localhost:{FLASK_PORT}{auth_note}")
    # threaded=True: MQTT/DAQ calls inside a request must not block other
    # requests (e.g. stop() while a start() request is still waiting on an ack).
    app.run(host=FLASK_HOST, port=FLASK_PORT, threaded=True, use_reloader=False)
