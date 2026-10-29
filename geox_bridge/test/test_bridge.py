"""Bridge handler tests - pure Python, no QGIS required.

The handlers module deliberately avoids qgis imports so pairing, CORS,
Private Network Access, body limits and routing can be tested anywhere.
"""

import http.client
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from geox_bridge.bridge.handlers import BridgeState, make_handler

ORIGIN = "https://xplus-studio.com"
BAD_ORIGIN = "https://evil.example"


class FakeDispatcher:
    def __init__(self):
        self.pushed = None

    def push(self, bundle):
        self.pushed = bundle
        return {"ok": True, "layersCreated": ["Roads"], "featuresSkipped": 0, "warnings": []}

    def pull(self, layer_ids):
        return {"_format": "y-plus-1", "_version": 1,
                "project": {"name": "from-qgis", "layers": [{"id": "layer-1"}]}}

    def layers(self):
        return [{"qgisId": "abc", "name": "Roads", "geomType": "line",
                 "featureCount": 12, "crs": "EPSG:32636"}]


@pytest.fixture()
def server():
    dispatcher = FakeDispatcher()
    state = BridgeState(dispatcher, qgis_version="3.34-test")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = "http://127.0.0.1:{}".format(httpd.server_address[1])
    yield base, state, dispatcher
    httpd.shutdown()
    httpd.server_close()


def request(base, path, method="GET", body=None, headers=None, origin=ORIGIN):
    req = urllib.request.Request(base + path, method=method)
    if origin:
        req.add_header("Origin", origin)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data=data) as resp:
            return resp.status, dict(resp.headers), json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), json.loads(exc.read() or b"{}")


def pair(base, state):
    code, _, payload = request(base, "/pair", "POST", {"token": state.token})
    assert code == 200 and payload["ok"]
    return payload["session"]


# ------------------------------------------------------------------- tests
def test_ping_unauthenticated(server):
    base, state, _ = server
    code, headers, payload = request(base, "/ping")
    assert code == 200
    assert payload["app"] == "geox-qgis-bridge"
    assert payload["protocol"] == 1
    assert payload["paired"] is False
    assert headers.get("Access-Control-Allow-Origin") == ORIGIN


def test_bad_origin_rejected(server):
    base, _, _ = server
    code, _, payload = request(base, "/ping", origin=BAD_ORIGIN)
    assert code == 403
    assert payload["error"] == "origin_not_allowed"


def test_rebound_host_rejected(server):
    # DNS rebinding: same-origin to 127.0.0.1 under a foreign name, no Origin.
    base, state, _ = server
    port = int(base.rsplit(":", 1)[1])
    for method, path, body in (("GET", "/ping", None), ("POST", "/pair", {"token": state.token})):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        payload = json.dumps(body).encode("utf-8") if body is not None else None
        conn.request(method, path, body=payload, headers={"Host": "evil.example:%d" % port, "Content-Type": "application/json"})
        resp = conn.getresponse()
        assert resp.status == 403
        assert json.loads(resp.read())["error"] == "host_not_allowed"
        conn.close()
    # The loopback names still work.
    code, _, _ = request("http://localhost:%d" % port, "/ping", origin=None)
    assert code == 200


def test_preflight_private_network(server):
    base, _, _ = server
    req = urllib.request.Request(base + "/push", method="OPTIONS")
    req.add_header("Origin", ORIGIN)
    req.add_header("Access-Control-Request-Method", "POST")
    req.add_header("Access-Control-Request-Private-Network", "true")
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 204
        assert resp.headers.get("Access-Control-Allow-Private-Network") == "true"
        assert resp.headers.get("Access-Control-Allow-Origin") == ORIGIN
        assert "X-GEOX-Token" in resp.headers.get("Access-Control-Allow-Headers", "")


def test_pair_flow_and_auth(server):
    base, state, _ = server
    # unauthenticated protected route
    code, _, payload = request(base, "/layers")
    assert code == 403 and payload["error"] == "not_paired"
    # wrong token
    code, _, payload = request(base, "/pair", "POST", {"token": "000000"})
    assert code == 403 and payload["error"] == "bad_token"
    # right token
    session = pair(base, state)
    code, _, payload = request(base, "/layers", headers={"X-GEOX-Token": session})
    assert code == 200
    assert payload["layers"][0]["name"] == "Roads"
    # ping reports paired
    code, _, payload = request(base, "/ping")
    assert payload["paired"] is True


def test_pair_rate_limit_regenerates_token(server):
    base, state, _ = server
    original = state.token
    for _ in range(5):
        code, _, _ = request(base, "/pair", "POST", {"token": "999999"})
        assert code == 403
    assert state.token != original  # regenerated after 5 failures
    # old token no longer works even if guessed later
    code, _, _ = request(base, "/pair", "POST", {"token": original})
    assert code == 403


def test_push_and_pull_round_trip(server):
    base, state, dispatcher = server
    session = pair(base, state)
    bundle = {"_format": "y-plus-1", "_version": 1, "project": {"layers": []}}
    code, _, payload = request(base, "/push", "POST", bundle,
                               headers={"X-GEOX-Token": session})
    assert code == 200 and payload["ok"]
    assert dispatcher.pushed == bundle
    code, _, payload = request(base, "/pull",
                               headers={"X-GEOX-Token": session})
    assert code == 200
    assert payload["project"]["name"] == "from-qgis"
    code, _, payload = request(base, "/status", headers={"X-GEOX-Token": session})
    assert code == 200 and payload["lastOp"]["kind"] == "pull"


def test_invalid_json_rejected(server):
    base, state, _ = server
    session = pair(base, state)
    req = urllib.request.Request(base + "/push", method="POST",
                                 data=b"{not json", headers={
                                     "Origin": ORIGIN,
                                     "X-GEOX-Token": session,
                                     "Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req)
        assert False, "should have raised"
    except urllib.error.HTTPError as exc:
        assert exc.code == 400
        assert json.loads(exc.read())["error"] == "invalid_json"


def test_oversized_body_rejected(server):
    base, state, _ = server
    session = pair(base, state)
    req = urllib.request.Request(base + "/push", method="POST", data=b"x",
                                 headers={"Origin": ORIGIN,
                                          "X-GEOX-Token": session,
                                          "Content-Type": "application/json"})
    # Lie about the length: handler must reject before reading the body.
    req.add_unredirected_header("Content-Length", str(200 * 1024 * 1024))
    try:
        urllib.request.urlopen(req)
        assert False, "should have raised"
    except urllib.error.HTTPError as exc:
        assert exc.code == 413
    except urllib.error.URLError:
        pass  # server may drop the connection; acceptable rejection


def test_unknown_route_404(server):
    base, state, _ = server
    code, _, payload = request(base, "/nope")
    assert code == 404
