import http.server
import json
import mimetypes
import os
import tempfile
import threading
import urllib.parse

from portal import PortalClient, PortalError


KIND_KEYS = {"route": "routes", "stop": "stops"}
MAX_NOTE_LEN = 60


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
            if not isinstance(data, dict):
                return {"routes": [], "stops": []}
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

    def set_note(self, kind, item_id, note):
        key = KIND_KEYS[kind]
        note = (note or "").strip()
        with self._lock:
            data = self._read()
            for item in data[key]:
                if str(item.get("id")) == str(item_id):
                    if note:
                        item["note"] = note
                    else:
                        item.pop("note", None)
            self._write(data)
            return data

    def set_dir_label(self, kind, item_id, where, label):
        key = KIND_KEYS[kind]
        where = (where or "").strip()
        label = (label or "").strip()
        with self._lock:
            data = self._read()
            for item in data[key]:
                if str(item.get("id")) == str(item_id):
                    dirs = item.get("dirs") or {}
                    if label:
                        dirs[where] = label
                        item["dirs"] = dirs
                    else:
                        dirs.pop(where, None)
                        if dirs:
                            item["dirs"] = dirs
                        else:
                            item.pop("dirs", None)
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


def _catalog_ready(catalog):
    return catalog is not None and catalog.ready()


def handle_api(path, params, method, body, portal, store, catalog=None):
    try:
        if path == "/api/routes" and method == "GET":
            if _catalog_ready(catalog):
                routes = catalog.all_routes(params.get("type"))
            else:
                routes = portal.get_routes()
                rtype = params.get("type")
                if rtype is not None:
                    routes = [r for r in routes if str(r.get("routeTypeId")) == str(rtype)]
            return 200, {"routes": routes}

        if path == "/api/route" and method == "GET":
            rid = _as_int(params.get("id"))
            if rid is None:
                return 400, {"error": {"reason": "bad_param", "message": "id required"}}
            route = catalog.get_route(rid) if _catalog_ready(catalog) else portal.get_route(rid)
            return 200, {"route": route}

        if path == "/api/stations" and method == "GET":
            q = (params.get("q") or "").strip()
            if _catalog_ready(catalog):
                stations = catalog.search_stations(q)
            else:
                stations = portal.get_stations()
                if q:
                    stations = [s for s in stations if q.lower() in (s.get("name") or "").lower()]
            return 200, {"stations": stations}

        if path == "/api/stops" and method == "GET":
            q = (params.get("q") or "").strip()
            if _catalog_ready(catalog):
                stops = catalog.stops_with_routes(q)
            else:
                stops = portal.get_stations()
                if q:
                    stops = [s for s in stops if q.lower() in (s.get("name") or "").lower()]
                stops = [dict(s, routes=[]) for s in stops]
            return 200, {"stops": stops}

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

        if path == "/api/stop-routes" and method == "GET":
            sid = _as_int(params.get("sid"))
            if sid is None:
                return 400, {"error": {"reason": "bad_param", "message": "sid required"}}
            routes = catalog.routes_for_stop(sid) if _catalog_ready(catalog) \
                else portal.get_station_routes(sid)
            return 200, {"routes": routes}

        if path == "/api/vehicle" and method == "GET":
            code = params.get("deviceCode")
            if not code:
                return 400, {"error": {"reason": "bad_param", "message": "deviceCode required"}}
            return 200, {
                "vehicle": portal.get_vehicle(code),
                "forecasts": portal.get_vehicle_forecasts(code),
            }

        if path == "/api/catalog" and method == "GET":
            if catalog is None:
                return 200, {"enabled": False}
            return 200, {"enabled": True, **catalog.status()}

        if path == "/api/refresh" and method == "POST":
            if catalog is None:
                return 200, {"status": "disabled"}
            return 200, {"status": catalog.refresh_async(portal)}

        if path == "/api/favorites" and method == "GET":
            return 200, store.load()

        if path == "/api/favorites" and method == "POST":
            body = body or {}
            action, kind = body.get("action"), body.get("kind")
            if kind not in KIND_KEYS or action not in ("add", "remove", "note", "dir"):
                return 400, {"error": {"reason": "bad_param", "message": "action/kind"}}
            if action == "add":
                name = body.get("name") or _resolve_name(portal, kind, body.get("id"))
                return 200, store.add(kind, {"id": body.get("id"), "name": name})
            if action == "note":
                if body.get("id") is None:
                    return 400, {"error": {"reason": "bad_param", "message": "id required"}}
                note = body.get("note")
                if note is None:
                    note = ""
                if not isinstance(note, str) or len(note.strip()) > MAX_NOTE_LEN:
                    return 400, {"error": {"reason": "bad_param", "message": "note too long"}}
                return 200, store.set_note(kind, body.get("id"), note)
            if action == "dir":
                if body.get("id") is None:
                    return 400, {"error": {"reason": "bad_param", "message": "id required"}}
                where = body.get("where")
                label = body.get("label")
                if label is None:
                    label = ""
                if not isinstance(where, str) or not where.strip():
                    return 400, {"error": {"reason": "bad_param", "message": "where required"}}
                if not isinstance(label, str) or len(label.strip()) > MAX_NOTE_LEN:
                    return 400, {"error": {"reason": "bad_param", "message": "label too long"}}
                return 200, store.set_dir_label(kind, body.get("id"), where, label)
            return 200, store.remove(kind, body.get("id"))

        return 404, {"error": {"reason": "not_found", "message": path}}
    except PortalError as exc:
        return 502, {"error": {"reason": exc.reason, "message": exc.message}}
    except Exception as exc:  # noqa: BLE001 - keep the server answering
        return 500, {"error": {"reason": "internal", "message": str(exc)}}


DEFAULTS = {
    "portalUrl": "https://xn--80axnakf7a.xn--80acgfbsl1azdqr.xn--p1ai/php/apiRequest.php",
    "token": "11111111-50b3-4fec-b922-8a50a1d38366",
    "reg": 66002, "wuid": "marshrut-web", "host": "0.0.0.0", "port": 8000,
    "cacheTtl": {"routes": 21600, "route": 21600, "stations": 21600, "forecasts": 10,
                 "vehicle": 21600, "vehicleForecasts": 30},
}


def load_config(path="config.json"):
    """Load config.json, then overlay sibling config.local.json (git-ignored).

    Missing, unreadable, unparseable or wrongly-shaped files are skipped, so
    the app always starts with at least the built-in DEFAULTS.
    """
    config = dict(DEFAULTS)
    base, ext = os.path.splitext(path)
    for candidate in (path, base + ".local" + ext):
        try:
            with open(candidate, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            config.update(data)
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
