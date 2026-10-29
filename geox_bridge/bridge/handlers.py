"""HTTP request handling for the GEOX bridge.

Pure Python on purpose - no QGIS imports. All QGIS work happens through an
injected dispatcher object (see dispatch.MainThreadDispatcher), so this
module is unit-testable with a fake dispatcher and a plain HTTP server.

Security model:
- server binds 127.0.0.1 only (see server.py);
- Origin allow-list enforced server-side (browser CORS alone does not stop
  non-browser local processes, hence the token as well);
- 6-digit pairing token shown in the QGIS dock is exchanged at /pair for a
  128-bit session id; everything except /ping and OPTIONS requires it;
- Chrome Private Network Access preflights are acknowledged.
"""

import hmac
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler

from ..core.constants import (
    ALLOWED_ORIGINS,
    BRIDGE_PROTOCOL,
    DEV_ORIGINS,
    MAX_REQUEST_BYTES,
    PLUGIN_VERSION,
)

PAIR_MAX_ATTEMPTS = 5


class BridgeState:
    """Mutable state shared between the dock UI and the HTTP handlers."""

    def __init__(self, dispatcher, qgis_version="", dev_mode=False):
        self.dispatcher = dispatcher          # .push(bundle)->dict, .pull(ids)->dict, .layers()->list
        self.qgis_version = qgis_version
        self.dev_mode = dev_mode
        self.lock = threading.Lock()
        self.session = None                   # 32-hex session id once paired
        self.pair_attempts = 0
        self.last_op = None                   # {"kind","at","ok"}
        self.busy = False
        self.operation_lock = threading.Lock()
        self.on_event = None                  # callable(str) for the dock log
        self.on_token_regenerated = None      # callable(str) for the dock UI
        self.regenerate_token()

    def regenerate_token(self):
        self.token = "".join(secrets.choice("0123456789") for _ in range(6))
        self.pair_attempts = 0
        if self.on_token_regenerated:
            try:
                self.on_token_regenerated(self.token)
            except Exception:
                pass
        return self.token

    def log(self, message):
        if self.on_event:
            try:
                self.on_event(message)
            except Exception:
                pass

    def allowed_origins(self):
        return ALLOWED_ORIGINS + (DEV_ORIGINS if self.dev_mode else ())

    def try_pair(self, token):
        with self.lock:
            if self.pair_attempts >= PAIR_MAX_ATTEMPTS:
                self.regenerate_token()
                self.log("pairing token regenerated after too many attempts")
            ok = hmac.compare_digest(str(token or ""), self.token)
            if not ok:
                self.pair_attempts += 1
                if self.pair_attempts >= PAIR_MAX_ATTEMPTS:
                    self.regenerate_token()
                    self.log("pairing token regenerated after too many attempts")
                return None
            self.session = secrets.token_hex(16)
            self.pair_attempts = 0
            self.log("GEOX paired successfully")
            return self.session

    def check_session(self, token):
        with self.lock:
            return (self.session is not None
                    and hmac.compare_digest(str(token or ""), self.session))


def make_handler(state):
    """Build a BaseHTTPRequestHandler subclass bound to a BridgeState."""

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "geox-bridge/" + PLUGIN_VERSION

        # ------------------------------------------------------ plumbing
        def log_message(self, fmt, *args):  # silence stderr chatter
            pass

        def _host_ok(self):
            # DNS rebinding: a page on evil.example can point its own name at
            # 127.0.0.1 and reach this server same-origin, sending no Origin
            # header. Its requests still say "Host: evil.example", so only the
            # loopback names are accepted. (A missing Host - HTTP/1.0 tools - passes;
            # the pairing token is still required.)
            host = (self.headers.get("Host") or "").strip().lower()
            if not host:
                return True
            port = self.server.server_address[1]
            return host in ("127.0.0.1:%d" % port, "localhost:%d" % port)

        def _origin_ok(self):
            if not self._host_ok():
                return False
            origin = self.headers.get("Origin")
            if origin is None:
                return True  # non-browser client (curl, tests); token still required
            return origin in state.allowed_origins()

        def _cors_headers(self):
            origin = self.headers.get("Origin")
            if origin and origin in state.allowed_origins():
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
                self.send_header("Access-Control-Allow-Headers", "Content-Type, X-GEOX-Token")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

        def _json(self, code, payload):
            body = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self._cors_headers()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _reject_origin(self):
            self._json(403, {"error": "origin_not_allowed" if self._host_ok() else "host_not_allowed"})

        def _require_session(self):
            if not state.check_session(self.headers.get("X-GEOX-Token")):
                self._json(403, {"error": "not_paired"})
                return False
            return True

        def _read_body(self):
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except (TypeError, ValueError):
                self._json(400, {"error": "invalid_content_length"})
                return None
            if length < 0:
                self._json(400, {"error": "invalid_content_length"})
                return None
            if length > MAX_REQUEST_BYTES:
                self._json(413, {"error": "too_large", "maxBytes": MAX_REQUEST_BYTES})
                return None
            raw = self.rfile.read(length) if length else b""
            if len(raw) != length:
                self._json(400, {"error": "incomplete_body"})
                return None
            try:
                return json.loads(raw.decode("utf-8")) if raw else {}
            except (ValueError, UnicodeDecodeError):
                self._json(400, {"error": "invalid_json"})
                return None

        # ------------------------------------------------------- OPTIONS
        def do_OPTIONS(self):
            if not self._origin_ok():
                self._reject_origin()
                return
            self.send_response(204)
            self._cors_headers()
            if self.headers.get("Access-Control-Request-Private-Network") == "true":
                self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Access-Control-Max-Age", "600")
            self.send_header("Content-Length", "0")
            self.end_headers()

        # ----------------------------------------------------------- GET
        def do_GET(self):
            if not self._origin_ok():
                self._reject_origin()
                return
            path = self.path.split("?")[0]
            if path == "/ping":
                self._json(200, {
                    "app": "geox-qgis-bridge",
                    "version": PLUGIN_VERSION,
                    "qgis": state.qgis_version,
                    "paired": state.session is not None,
                    "protocol": BRIDGE_PROTOCOL,
                })
            elif path == "/layers":
                if not self._require_session():
                    return
                try:
                    layers = state.dispatcher.layers()
                    self._json(200, {"layers": layers})
                except Exception as exc:
                    self._json(500, {"error": "internal", "detail": str(exc)})
            elif path == "/pull":
                if not self._require_session():
                    return
                if not state.operation_lock.acquire(blocking=False):
                    self._json(409, {"error": "bridge_busy"})
                    return
                wanted = None
                if "?" in self.path:
                    from urllib.parse import parse_qs, urlparse
                    qs = parse_qs(urlparse(self.path).query)
                    if qs.get("layers"):
                        wanted = [x for x in qs["layers"][0].split(",") if x]
                try:
                    state.busy = True
                    bundle = state.dispatcher.pull(wanted)
                    state.last_op = {"kind": "pull", "at": _now_iso(), "ok": True}
                    state.log("pulled {} layer(s) to GEOX".format(
                        len(bundle.get("project", {}).get("layers", []))))
                    self._json(200, bundle)
                except Exception as exc:
                    state.last_op = {"kind": "pull", "at": _now_iso(), "ok": False}
                    code = 413 if "guardrail" in str(exc) else 500
                    self._json(code, {"error": "pull_failed", "detail": str(exc)})
                finally:
                    state.busy = False
                    state.operation_lock.release()
            elif path == "/status":
                if not self._require_session():
                    return
                self._json(200, {"busy": state.busy, "lastOp": state.last_op})
            else:
                self._json(404, {"error": "not_found"})

        # ---------------------------------------------------------- POST
        def do_POST(self):
            if not self._origin_ok():
                self._reject_origin()
                return
            path = self.path.split("?")[0]
            if path == "/pair":
                body = self._read_body()
                if body is None:
                    return
                session = state.try_pair(body.get("token"))
                if session:
                    self._json(200, {"ok": True, "session": session})
                else:
                    self._json(403, {"error": "bad_token"})
            elif path == "/push":
                if not self._require_session():
                    return
                body = self._read_body()
                if body is None:
                    return
                if not state.operation_lock.acquire(blocking=False):
                    self._json(409, {"error": "bridge_busy"})
                    return
                try:
                    state.busy = True
                    result = state.dispatcher.push(body)
                    state.last_op = {"kind": "push", "at": _now_iso(), "ok": True}
                    state.log("push received: {}".format(
                        ", ".join(result.get("layersCreated", [])) or "no layers"))
                    self._json(200, result)
                except Exception as exc:
                    state.last_op = {"kind": "push", "at": _now_iso(), "ok": False}
                    self._json(400, {"error": "push_failed", "detail": str(exc)})
                finally:
                    state.busy = False
                    state.operation_lock.release()
            else:
                self._json(404, {"error": "not_found"})

    return Handler


def _now_iso():
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
