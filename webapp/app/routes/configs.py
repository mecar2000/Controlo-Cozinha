"""routes.configs — run_configs CRUD, backing the compose sheet's saved-config
picker. Configs are first-class saved objects from day one, which is what
makes a future batch sequencer cheap to add later (deferred, per spec)."""

from flask import Blueprint, jsonify, request

import app.db as db
from app.auth import require_auth

bp = Blueprint("configs", __name__)


@bp.get("/api/configs")
@require_auth
def list_configs():
    include_archived = request.args.get("include_archived") == "1"
    return jsonify(db.list_configs(include_archived=include_archived))


@bp.get("/api/configs/<int:config_id>")
@require_auth
def get_config(config_id: int):
    config = db.get_config(config_id)
    if config is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(config)


@bp.post("/api/configs")
@require_auth
def create_config():
    body = request.get_json(force=True, silent=True) or {}
    name = body.get("name")
    spec = body.get("spec")
    if not name or not isinstance(spec, dict):
        return jsonify({"error": "name and spec are required"}), 400
    return jsonify(db.create_config(name, spec)), 201


@bp.put("/api/configs/<int:config_id>")
@require_auth
def update_config(config_id: int):
    body = request.get_json(force=True, silent=True) or {}
    name = body.get("name")
    spec = body.get("spec")
    if not name or not isinstance(spec, dict):
        return jsonify({"error": "name and spec are required"}), 400
    updated = db.update_config(config_id, name, spec)
    if updated is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(updated)


@bp.delete("/api/configs/<int:config_id>")
@require_auth
def archive_config(config_id: int):
    db.archive_config(config_id)
    return jsonify({"archived": True})
