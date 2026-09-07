"""routes — registers every blueprint onto the Flask app."""

from . import status, configs, runs, sensors, daq_proxy, frontend


def register_all(app):
    app.register_blueprint(status.bp)
    app.register_blueprint(configs.bp)
    app.register_blueprint(runs.bp)
    app.register_blueprint(sensors.bp)
    app.register_blueprint(daq_proxy.bp)
    # Last: its catch-all route serves the SPA shell for anything unmatched,
    # so every /api/... rule above must already be registered.
    app.register_blueprint(frontend.bp)
