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

Framing: every response carries Content-Security-Policy frame-ancestors
(add_frame_ancestors, an after_request hook), so only this app and
DataAcquisition's dashboard (its Cozinha tab) can embed it. Without it, any
page could frame the control UI and trick a click onto Start or Stop.
"""

import functools
import hmac
from urllib.parse import urlparse

from flask import jsonify, request

from app.config import DASHBOARD_TOKEN, ALLOWED_ORIGINS, DAQ_BASE_URL, FRAME_ANCESTORS

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


def _clean_origin(url: str) -> "str | None":
    """scheme://host[:port] of an http(s) URL, or None. Anything else (a bare
    host, '*', a CSP keyword, a ';' that would start a new directive) is
    dropped rather than pasted into the header."""
    try:
        parsed = urlparse((url or "").strip())
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    if not all(c.isalnum() or c in ".-:" for c in parsed.hostname):
        return None
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    return f"{parsed.scheme}://{host}" + (f":{port}" if port else "")


def _daq_on_this_host() -> "str | None":
    """DataAcquisition's port on the host this request came in on. The Cozinha
    tab's default iframe URL is its own page's host on our port, so this is
    exactly the dashboard's origin without having to configure it."""
    daq = urlparse(DAQ_BASE_URL)
    try:
        port = daq.port
    except ValueError:
        return None
    if not port:
        return None
    hostname = urlparse(f"//{request.host}").hostname
    if not hostname:
        return None
    if ":" in hostname:
        hostname = f"[{hostname}]"   # IPv6: urlparse strips the brackets
    return _clean_origin(f"{request.scheme}://{hostname}:{port}")


def frame_ancestors_policy() -> str:
    """The frame-ancestors directive for this request."""
    sources = ["'self'"]
    for candidate in (_clean_origin(DAQ_BASE_URL), _daq_on_this_host(),
                      *map(_clean_origin, FRAME_ANCESTORS)):
        if candidate and candidate not in sources:
            sources.append(candidate)
    return "frame-ancestors " + " ".join(sources)


def add_frame_ancestors(response):
    """after_request hook: restrict who may embed this app (see module doc)."""
    response.headers["Content-Security-Policy"] = frame_ancestors_policy()
    return response


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
