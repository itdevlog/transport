import http.server
import json
import mimetypes
import os
import tempfile
import threading
import urllib.parse

from portal import PortalClient, PortalError


KIND_KEYS = {"route": "routes", "stop": "stops"}


class FavoritesStore:
    def __init__(self, path):
        self._path = path
        self._lock = threading.Lock()

    def load(self):
        with self._lock:
            return self._read()

    def _read(self):
        try:
            with open(self._path, encoding="utf-8") as fh:
                data = json.load(fh)
            return {"routes": list(data.get("routes", [])),
                    "stops": list(data.get("stops", []))}
        except (OSError, ValueError):
            return {"routes": [], "stops": []}

    def add(self, kind, item):
        key = KIND_KEYS[kind]
        with self._lock:
            data = self._read()
            if not any(str(x.get("id")) == str(item.get("id")) for x in data[key]):
                data[key].append(item)
            self._write(data)
            return data

    def remove(self, kind, item_id):
        key = KIND_KEYS[kind]
        with self._lock:
            data = self._read()
            data[key] = [x for x in data[key] if str(x.get("id")) != str(item_id)]
            self._write(data)
            return data

    def _write(self, data):
        directory = os.path.dirname(os.path.abspath(self._path)) or "."
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".favorites-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self._path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _resolve_name(portal, kind, item_id):
    if kind == "stop":
        for s in portal.get_stations():
            if str(s.get("id")) == str(item_id):
                return s.get("name")
    else:
        for r in portal.get_routes():
            if str(r.get("id")) == str(item_id):
                return r.get("name") or r.get("shortName") or r.get("number")
    return None


def handle_api(path, params, method, body, portal, store):
    try:
        if path == "/api/routes" and method == "GET":
            routes = portal.get_routes()
            rtype = params.get("type")
            if rtype is not None:
                routes = [r for r in routes if str(r.get("routeTypeId")) == str(rtype)]
            return 200, {"routes": routes}

        if path == "/api/route" and method == "GET":
            rid = _as_int(params.get("id"))
            if rid is None:
                return 400, {"error": {"reason": "bad_param", "message": "id required"}}
            return 200, {"route": portal.get_route(rid)}

        if path == "/api/stations" and method == "GET":
            q = (params.get("q") or "").strip().lower()
            stations = portal.get_stations()
            if q:
                stations = [s for s in stations if q in (s.get("name") or "").lower()]
            return 200, {"stations": stations}

        if path == "/api/forecast" and method == "GET":
            sid = _as_int(params.get("sid"))
            if sid is None:
                return 400, {"error": {"reason": "bad_param", "message": "sid required"}}
            forecasts = sorted(
                portal.get_forecasts(sid),
                key=lambda f: f.get("arrivalTimeInSec")
                if isinstance(f.get("arrivalTimeInSec"), (int, float)) else float("inf"),
            )
            return 200, {"forecasts": forecasts}

        if path == "/api/favorites" and method == "GET":
            return 200, store.load()

        if path == "/api/favorites" and method == "POST":
            body = body or {}
            action, kind = body.get("action"), body.get("kind")
            if kind not in KIND_KEYS or action not in ("add", "remove"):
                return 400, {"error": {"reason": "bad_param", "message": "action/kind"}}
            if action == "add":
                name = body.get("name") or _resolve_name(portal, kind, body.get("id"))
                return 200, store.add(kind, {"id": body.get("id"), "name": name})
            return 200, store.remove(kind, body.get("id"))

        return 404, {"error": {"reason": "not_found", "message": path}}
    except PortalError as exc:
        return 502, {"error": {"reason": exc.reason, "message": exc.message}}


DEFAULTS = {
    "portalUrl": "https://xn--80axnakf7a.xn--80acgfbsl1azdqr.xn--p1ai/php/apiRequest.php",
    "token": "11111111-50b3-4fec-b922-8a50a1d38366",
    "reg": 66002, "wuid": "marshrut-web", "host": "0.0.0.0", "port": 8000,
    "cacheTtl": {"routes": 21600, "route": 21600, "stations": 21600, "forecasts": 10},
}


def load_config(path="config.json"):
    config = dict(DEFAULTS)
    try:
        with open(path, encoding="utf-8") as fh:
            config.update(json.load(fh))
    except (OSError, ValueError):
        pass
    return config


def make_handler(portal, store, static_dir):
    class Handler(http.server.BaseHTTPRequestHandler):
        def _send(self, status, payload):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path.startswith("/api/"):
                params = {k: v[-1] for k, v in
                          urllib.parse.parse_qs(parsed.query).items()}
                self._send(*handle_api(parsed.path, params, "GET", None, portal, store))
                return
            self._serve_static(parsed.path)

        def _serve_static(self, url_path):
            rel = url_path.lstrip("/") or "index.html"
            full = os.path.normpath(os.path.join(static_dir, rel))
            if not full.startswith(os.path.abspath(static_dir)) or not os.path.isfile(full):
                self._send(404, {"error": {"reason": "not_found", "message": url_path}})
                return
            with open(full, "rb") as fh:
                data = fh.read()
            ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            if not self.path.startswith("/api/"):
                self._send(404, {"error": {"reason": "not_found", "message": self.path}})
                return
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8")) if length else None
            except ValueError:
                self._send(400, {"error": {"reason": "bad_json"}})
                return
            parsed = urllib.parse.urlparse(self.path)
            self._send(*handle_api(parsed.path, {}, "POST", body, portal, store))

        def log_message(self, *args):
            pass

    return Handler


def serve(config, portal=None, store=None, static_dir="static"):
    portal = portal or PortalClient(config)
    store = store or FavoritesStore(os.path.join(os.path.dirname(__file__), "favorites.json"))
    handler = make_handler(portal, store, os.path.abspath(static_dir))
    return http.server.ThreadingHTTPServer((config["host"], config["port"]), handler)


def main():
    config = load_config()
    httpd = serve(config)
    print(f"Serving on {config['host']}:{httpd.server_address[1]}")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
