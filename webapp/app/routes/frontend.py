"""
routes.frontend — serves the built React app.

The frontend is a Vite project in webapp/frontend/. In development it runs on
its own dev server (npm run dev, port 5173) and proxies /api here, so these
routes are unused. In production `npm run build` writes frontend/dist/ and
these routes serve it.

Deliberately NOT decorated with @require_auth: the token gates the API, not
the shell. Serving index.html to an unauthenticated browser reveals nothing —
every route it calls still returns 401 without a token, and a login screen
that cannot load is worse than one that loads and says so.
"""

import os

from flask import Blueprint, send_from_directory

bp = Blueprint("frontend", __name__)

_DIST = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "frontend", "dist")


def _dist_exists() -> bool:
    return os.path.isfile(os.path.join(_DIST, "index.html"))


@bp.get("/")
def index():
    if not _dist_exists():
        # A plain instruction beats a 404 when someone starts the backend
        # before building the frontend for the first time.
        return (
            "<pre>Frontend not built yet.\n\n"
            "  cd webapp/frontend\n"
            "  npm install\n"
            "  npm run build\n\n"
            "Or run the dev server (npm run dev) and open http://localhost:5173"
            "</pre>",
            503,
        )
    return send_from_directory(_DIST, "index.html")


@bp.get("/assets/<path:filename>")
def assets(filename: str):
    return send_from_directory(os.path.join(_DIST, "assets"), filename)


@bp.get("/<path:filename>")
def static_passthrough(filename: str):
    """Any other top-level file in dist/ (favicon, manifest). Unknown paths
    fall back to index.html so a client-side route survives a page reload."""
    # An unmatched /api/... path must 404 as JSON, never fall through to the
    # SPA shell: a frontend typo should surface as a clean 404, not as HTML
    # arriving where JSON was expected and failing to parse.
    if filename == "api" or filename.startswith("api/"):
        return {"error": "not found"}, 404

    candidate = os.path.join(_DIST, filename)
    if os.path.isfile(candidate):
        return send_from_directory(_DIST, filename)
    if not _dist_exists():
        return {"error": "not found"}, 404
    return send_from_directory(_DIST, "index.html")
