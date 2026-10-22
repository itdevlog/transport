import http.cookies
import http.server
import json
import mimetypes
import os
import re
import sqlite3
import tempfile
import threading
import time
import urllib.parse
import uuid

import catalog as catalog_module
from portal import PortalClient, PortalError
from profiles import ProfilesStore

# .webmanifest должен отдаваться как application/manifest+json, иначе
# браузер может отказаться устанавливать приложение.
mimetypes.add_type("application/manifest+json", ".webmanifest")


KIND_KEYS = {"route": "routes", "stop": "stops"}


class FavoritesStore:
    def __init__(self, path):
        self._path = path
        self._lock = threading.Lock()

    @property
    def path(self):
        return self._path

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


class _LegacyFavorites:
    """Adapter giving the shared-file store the per-user interface.

    Used only when no ProfilesStore is configured (tests, or a deployment
    that has not switched to per-user profiles yet).
    """

    def __init__(self, store):
        self._store = store

    def get(self, uid):
        return self._store.load()

    def add(self, uid, kind, item):
        return self._store.add(kind, item)

    def remove(self, uid, kind, item_id):
        return self._store.remove(kind, item_id)


def _maybe_migrate(profiles, store, uid):
    """Одноразово переносит общий favorites.json в профиль первого браузера."""
    legacy = store.path
    if not os.path.exists(legacy):
        return
    try:
        data = store.load()
        current = profiles.get(uid)
        empty = not current["routes"] and not current["stops"]
        if empty and (data.get("routes") or data.get("stops")):
            profiles.save(uid, data)
    except (OSError, ValueError):
        pass
    try:
        os.replace(legacy, legacy + ".migrated")
    except OSError:
        pass


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _vehicle_position(item):
    """Компактная запись борта для карты (без тяжёлых animPoints)."""
    try:
        lat = float(item["lat"])
        lng = float(item["lng"])
    except (KeyError, TypeError, ValueError):
        return None
    return {
        "deviceCode": item.get("deviceCode"),
        "lat": lat,
        "lng": lng,
        "dir": item.get("dir"),
        "speed": item.get("speed"),
        "routeNumber": item.get("rnum"),
        "gosNumber": item.get("gosNum"),
    }


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
    if catalog is None:
        return False
    try:
        return catalog.ready()
    except sqlite3.Error:
        return False


def _with_catalog(catalog, query, fallback):
    """Serve from the mirror when ready; on a read-time DB error fall back.

    The spec (§9) requires any DB error to degrade to the portal rather than
    surface as a 500.
    """
    if _catalog_ready(catalog):
        try:
            return query(catalog)
        except sqlite3.Error:
            pass
    return fallback()


UID_RE = re.compile(r"[0-9a-f]{32}")


def _valid_uid(value):
    return bool(UID_RE.fullmatch(value or ""))


def _new_uid():
    return uuid.uuid4().hex


def _uid_from_cookie(cookie_header):
    if not cookie_header:
        return None
    try:
        jar = http.cookies.SimpleCookie()
        jar.load(cookie_header)
    except http.cookies.CookieError:
        return None
    morsel = jar.get("marshrut_uid")
    if morsel is None:
        return None
    value = morsel.value
    return value if _valid_uid(value) else None


def handle_api(path, params, method, body, portal, store, catalog=None,
               profiles=None, uid=None):
    try:
        if path == "/api/routes" and method == "GET":
            rtype = params.get("type")

            def _portal_routes():
                routes = portal.get_routes()
                if rtype is not None:
                    routes = [r for r in routes if str(r.get("routeTypeId")) == str(rtype)]
                return routes

            routes = _with_catalog(catalog, lambda c: c.all_routes(rtype), _portal_routes)
            return 200, {"routes": routes}

        if path == "/api/route" and method == "GET":
            rid = _as_int(params.get("id"))
            if rid is None:
                return 400, {"error": {"reason": "bad_param", "message": "id required"}}
            route = _with_catalog(catalog, lambda c: c.get_route(rid),
                                  lambda: portal.get_route(rid))
            return 200, {"route": route}

        if path == "/api/stations" and method == "GET":
            q = (params.get("q") or "").strip()

            def _portal_stations():
                stations = portal.get_stations()
                if q:
                    stations = [s for s in stations if q.lower() in (s.get("name") or "").lower()]
                return stations

            stations = _with_catalog(catalog, lambda c: c.search_stations(q), _portal_stations)
            return 200, {"stations": stations}

        if path == "/api/stops" and method == "GET":
            q = (params.get("q") or "").strip()

            def _portal_stops():
                stops = portal.get_stations()
                if q:
                    stops = [s for s in stops if q.lower() in (s.get("name") or "").lower()]
                return [dict(s, routes=[]) for s in stops]

            stops = _with_catalog(catalog, lambda c: c.stops_with_routes(q), _portal_stops)
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

        if path == "/api/route-vehicles" and method == "GET":
            route_id = _as_int(params.get("routeId"))
            if route_id is None:
                return 400, {"error": {"reason": "bad_param",
                                       "message": "routeId required"}}
            positions = []
            for item in portal.get_vehicles_animation(str(route_id)):
                if str(item.get("rid")) != str(route_id):
                    continue
                position = _vehicle_position(item)
                if position is not None:
                    positions.append(position)
            return 200, {"vehicles": positions}

        if path == "/api/stop-vehicles" and method == "GET":
            sid = _as_int(params.get("sid"))
            if sid is None:
                return 400, {"error": {"reason": "bad_param", "message": "sid required"}}
            forecasts = sorted(
                portal.get_forecasts(sid),
                key=lambda f: f.get("arrivalTimeInSec")
                if isinstance(f.get("arrivalTimeInSec"), (int, float)) else float("inf"),
            )
            nearest = [f for f in forecasts if f.get("deviceCode")][:3]
            rids = sorted({str(f["routeId"]) for f in nearest
                           if f.get("routeId") is not None})
            by_device = {}
            if rids:
                for item in portal.get_vehicles_animation(",".join(rids)):
                    position = _vehicle_position(item)
                    if position is not None:
                        by_device[str(item.get("deviceCode"))] = position
            return 200, {
                "vehicles": [by_device[str(f["deviceCode"])] for f in nearest
                             if str(f["deviceCode"]) in by_device],
            }

        if path == "/api/stop" and method == "GET":
            sid = _as_int(params.get("sid"))
            if sid is None:
                return 400, {"error": {"reason": "bad_param", "message": "sid required"}}
            stop = _with_catalog(
                catalog, lambda c: c.get_stop(sid),
                lambda: next((s for s in portal.get_stations()
                              if str(s.get("id")) == str(sid)), {}))
            if not stop:
                return 404, {"error": {"reason": "not_found", "message": "stop"}}
            return 200, {"stop": stop}

        if path == "/api/stop-routes" and method == "GET":
            sid = _as_int(params.get("sid"))
            if sid is None:
                return 400, {"error": {"reason": "bad_param", "message": "sid required"}}
            routes = _with_catalog(catalog, lambda c: c.routes_for_stop(sid),
                                   lambda: portal.get_station_routes(sid))
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

        if path == "/api/favorites":
            if profiles is not None:
                _maybe_migrate(profiles, store, uid)
            backend = profiles if profiles is not None else _LegacyFavorites(store)
            if method == "GET":
                return 200, backend.get(uid)
            body = body or {}
            action, kind = body.get("action"), body.get("kind")
            if kind not in KIND_KEYS or action not in ("add", "remove"):
                return 400, {"error": {"reason": "bad_param", "message": "action/kind"}}
            if action == "add":
                name = body.get("name") or _resolve_name(portal, kind, body.get("id"))
                return 200, backend.add(uid, kind, {"id": body.get("id"), "name": name})
            return 200, backend.remove(uid, kind, body.get("id"))

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
    "catalog": {"enabled": True, "path": "catalog.db", "refreshSeconds": 86400},
    "profiles": {"path": "profiles.db"},
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


def make_handler(portal, store, static_dir, catalog=None, profiles=None):
    class Handler(http.server.BaseHTTPRequestHandler):
        def _send(self, status, payload, set_uid=None):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            if set_uid is not None:
                cookie = ("marshrut_uid=%s; HttpOnly; SameSite=Lax; Path=/; "
                          "Max-Age=31536000" % set_uid)
                if self.headers.get("X-Forwarded-Proto") == "https":
                    cookie += "; Secure"
                self.send_header("Set-Cookie", cookie)
            self.end_headers()
            self.wfile.write(body)

        def _uid(self):
            """Return (uid, fresh) — mint and flag a new cookie when absent/bad."""
            uid = _uid_from_cookie(self.headers.get("Cookie"))
            if uid is None:
                return _new_uid(), True
            return uid, False

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path.startswith("/api/"):
                params = {k: v[-1] for k, v in
                          urllib.parse.parse_qs(parsed.query).items()}
                uid, fresh = self._uid()
                status, payload = handle_api(parsed.path, params, "GET", None,
                                             portal, store, catalog, profiles, uid)
                self._send(status, payload, set_uid=uid if fresh else None)
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
            uid, fresh = self._uid()
            status, payload = handle_api(parsed.path, {}, "POST", body,
                                         portal, store, catalog, profiles, uid)
            self._send(status, payload, set_uid=uid if fresh else None)

        def log_message(self, *args):
            pass

    return Handler


def _watchdog_sleep(interval):
    """Re-check the mirror at most every 300s, so a failed build retries soon."""
    return interval if 0 < interval < 300 else 300


def _catalog_watchdog(cat, portal, interval):
    """Daemon loop: rebuild the mirror on first run and then every interval."""
    try:
        interval = int(interval)
    except (TypeError, ValueError):
        interval = 0
    while True:
        try:
            if cat.should_refresh(time.time(), interval):
                cat.refresh_async(portal)
        except Exception:  # noqa: BLE001 - the watchdog must never die
            pass
        time.sleep(_watchdog_sleep(interval))


def serve(config, portal=None, store=None, static_dir="static", catalog=None, profiles=None):
    portal = portal or PortalClient(config)
    store = store or FavoritesStore(os.path.join(os.path.dirname(__file__), "favorites.json"))
    if profiles is None:
        prof_cfg = config.get("profiles") or {}
        try:
            profiles = ProfilesStore(
                os.path.join(os.path.dirname(__file__), prof_cfg.get("path", "profiles.db")))
        except (sqlite3.Error, OSError):
            profiles = None
    cat_cfg = config.get("catalog") or {}
    if catalog is None and cat_cfg.get("enabled", False):
        try:
            catalog = catalog_module.Catalog(
                os.path.join(os.path.dirname(__file__), cat_cfg.get("path", "catalog.db")))
        except (sqlite3.Error, OSError):
            catalog = None
        if catalog is not None:
            threading.Thread(target=_catalog_watchdog,
                             args=(catalog, portal, cat_cfg.get("refreshSeconds", 86400)),
                             daemon=True).start()
    handler = make_handler(portal, store, os.path.abspath(static_dir), catalog, profiles)
    return http.server.ThreadingHTTPServer((config["host"], config["port"]), handler)


def main():
    config = load_config()
    httpd = serve(config)
    print(f"Serving on {config['host']}:{httpd.server_address[1]}")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
