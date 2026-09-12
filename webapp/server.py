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
  app/db/             KitchenControl MySQL layer
  app/routes/         HTTP endpoints for the frontend, as Flask blueprints

Usage:
    pip install -r requirements.txt
    cp .env.example .env   # fill in DB_SERVER, DB_USER, MQTT_HOST, DAQ_BASE_URL, etc.
    python server.py

Then open http://localhost:5010 in your browser (once the frontend exists).
"""

import os
import subprocess

from app import create_app, start_background
from app.config import FLASK_HOST, FLASK_PORT, DASHBOARD_TOKEN

_FRONTEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend")
_DIST_INDEX = os.path.join(_FRONTEND_DIR, "dist", "index.html")


def _newest_mtime(root: str) -> float:
    newest = 0.0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in ("node_modules", "dist")]
        for name in filenames:
            try:
                newest = max(newest, os.path.getmtime(os.path.join(dirpath, name)))
            except OSError:
                pass
    return newest


def build_frontend_if_stale() -> None:
    """Rebuild frontend/dist/ when the source is newer than the last build.

    A stale dist/ is silent at runtime — the browser just runs old JS against
    the current backend, which surfaces as confusing client-side errors
    rather than a clear "rebuild me". Checked on every startup so that never
    happens without an explicit `npm run build`, but skipped when dist/ is
    already newer than every source file, so a normal restart stays fast.
    """
    dist_mtime = os.path.getmtime(_DIST_INDEX) if os.path.isfile(_DIST_INDEX) else 0.0
    source_mtime = max(
        _newest_mtime(os.path.join(_FRONTEND_DIR, "src")),
        *(
            os.path.getmtime(p)
            for p in (
                os.path.join(_FRONTEND_DIR, "package.json"),
                os.path.join(_FRONTEND_DIR, "vite.config.ts"),
                os.path.join(_FRONTEND_DIR, "index.html"),
            )
            if os.path.isfile(p)
        ),
    )
    if source_mtime <= dist_mtime:
        return
    print("[Web] Frontend source is newer than dist/ — running npm run build...")
    npm = "npm.cmd" if os.name == "nt" else "npm"
    try:
        subprocess.run([npm, "run", "build"], cwd=_FRONTEND_DIR, check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"[Web] Frontend build failed ({exc}) — serving existing dist/ as-is")


app = create_app()

if __name__ == "__main__":
    build_frontend_if_stale()
    start_background()
    auth_note = " (auth enabled)" if DASHBOARD_TOKEN else " (auth disabled)"
    print(f"[Web] Kitchen control at http://localhost:{FLASK_PORT}{auth_note}")
    # threaded=True: MQTT/DAQ calls inside a request must not block other
    # requests (e.g. stop() while a start() request is still waiting on an ack).
    app.run(host=FLASK_HOST, port=FLASK_PORT, threaded=True, use_reloader=False)
