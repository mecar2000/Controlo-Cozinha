"""
app.auth — bearer-token authentication + cross-origin protection for this
app's own routes.

Identical shape to DataAcquisition/dashboard/app/auth.py (no-op when
DASHBOARD_TOKEN is empty), kept as its own module rather than imported
across repos per the boundary rule: no shared modules with DataAcquisition.

The token is read from the Authorization header only. It is deliberately NOT
accepted as a query parameter: a token in a URL leaks into access logs,
Referer headers and browser history, and this token authorizes hydrogen
commands.
"""

import functools
import hmac
from urllib.parse import urlparse

from flask import jsonify, request

from app.config import DASHBOARD_TOKEN, ALLOWED_ORIGINS

# Methods that can change something. Safe methods skip the origin check so a
# plain <img> or link to a read-only endpoint doesn't 403.
_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _token_from_request() -> str:
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:]
    return ""


def _origin_host() -> "str | None":
    """The requesting page's origin, from Origin or (failing that) Referer.
    Returns None when the browser sent neither — same-origin non-CORS requests
    from some clients, and every non-browser caller (curl, the test suite)."""
    origin = request.headers.get("Origin")
    if not origin:
        referer = request.headers.get("Referer")
        if not referer:
            return None
        origin = referer
    parsed = urlparse(origin)
    if not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def _origin_allowed() -> bool:
    """
    Reject cross-origin state changes.

    This matters most when DASHBOARD_TOKEN is empty — the documented lab
    default. With auth off, every mutating route would otherwise be reachable
    by any page the operator happens to have open: a cross-origin
    POST /api/runs/start is a hydrogen release triggered by a web page.
    Browsers always send Origin on cross-origin requests, so checking it is
    enough to stop that without a token round-trip.
    """
    origin = _origin_host()
    if origin is None:
        return True  # no browser origin to disbelieve
    if origin in ALLOWED_ORIGINS:
        return True
    # Same-origin: the request arrived at the host the page was served from.
    return origin == f"{request.scheme}://{request.host}"


def _json_content_type_ok() -> bool:
    """
    Require JSON on mutating requests.

    A form POST (urlencoded/multipart/plain text) is the one cross-origin
    request a browser will send without a preflight, so refusing those closes
    the CSRF path that Origin checking alone can miss on older clients.
    Bodyless mutations (cancel, stop, ack) are allowed through.
    """
    if not request.content_length:
        return True
    return request.mimetype == "application/json"


def require_auth(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        if request.method in _UNSAFE_METHODS:
            if not _origin_allowed():
                return jsonify({"error": "Cross-origin request refused"}), 403
            if not _json_content_type_ok():
                return jsonify({"error": "Expected Content-Type: application/json"}), 415

        if not DASHBOARD_TOKEN:
            return f(*args, **kwargs)
        if not hmac.compare_digest(_token_from_request(), DASHBOARD_TOKEN):
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)

    return wrapper
