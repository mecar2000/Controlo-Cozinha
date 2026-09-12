"""
app — Kitchen H2 Control webapp backend (Flask).

create_app() builds the Flask app and registers routes; start_background()
starts the MQTT client and phase watcher. Split the same way DataAcquisition
splits create_app()/start_background(), so both can be imported by a WSGI
entry point without starting background threads twice per worker.
"""

from flask import Flask

import app.db as db


def create_app() -> Flask:
    flask_app = Flask(__name__)
    db.init_db()

    from app.routes import register_all
    register_all(flask_app)

    @flask_app.get("/api/health")
    def health():
        return {"ok": True}

    return flask_app


def start_background() -> None:
    import app.mqtt as mqtt_module
    import app.phase_watcher as phase_watcher

    mqtt_module.start()
    phase_watcher.start()

    try:
        import app.daq as daq
        daq.ensure_kitchen_location()
    except Exception as exc:
        print(f"[startup] Could not verify DataAcquisition Kitchen location: {exc}")
