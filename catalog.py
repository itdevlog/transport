"""Локальное зеркало каталога портала в SQLite.

Класс ``Catalog`` инкапсулирует один файл SQLite со справочными данными:
остановки, маршруты, связи «остановка → маршрут» и полные детали маршрутов.
Сервер предпочитает эти данные, а на портал ходит только за живыми
прогнозами и данными о бортах.
"""

import json
import sqlite3
import threading
import time

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS stops (
  id          INTEGER PRIMARY KEY,
  name        TEXT NOT NULL DEFAULT '',
  lat         REAL,
  lng         REAL,
  direction   INTEGER,
  adress      TEXT DEFAULT '',
  description TEXT DEFAULT '',
  shortname   TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_stops_name ON stops(name);

CREATE TABLE IF NOT EXISTS routes (
  id            INTEGER PRIMARY KEY,
  number        TEXT DEFAULT '',
  name          TEXT DEFAULT '',
  short_name    TEXT DEFAULT '',
  route_type_id INTEGER,
  company       TEXT DEFAULT '',
  from_station  TEXT DEFAULT '',
  to_station    TEXT DEFAULT '',
  price         TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_routes_type ON routes(route_type_id);

CREATE TABLE IF NOT EXISTS stop_routes (
  stop_id  INTEGER NOT NULL,
  route_id INTEGER NOT NULL,
  PRIMARY KEY (stop_id, route_id)
);
CREATE INDEX IF NOT EXISTS idx_stop_routes_route ON stop_routes(route_id);

CREATE TABLE IF NOT EXISTS route_detail (
  route_id INTEGER PRIMARY KEY,
  payload  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT
);
"""

_TABLES = ("stops", "routes", "stop_routes", "route_detail", "meta")


class Catalog:
    def __init__(self, path):
        self._path = path
        self._lock = threading.Lock()
        self._building = False
        self._last_error = None
        self._init_db()

    def _connect(self):
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        conn = self._connect()
        try:
            existing = None
            row = conn.execute(
                "SELECT value FROM meta WHERE key = 'schema_version'"
            ).fetchone() if self._has_meta(conn) else None
            if row is not None:
                existing = row[0]
            if existing is not None and existing != str(SCHEMA_VERSION):
                for table in _TABLES:
                    conn.execute("DROP TABLE IF EXISTS " + table)
                conn.commit()
            conn.executescript(_SCHEMA)
            conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _has_meta(conn):
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'meta'"
        ).fetchone()
        return row is not None

    def counts(self):
        conn = self._connect()
        try:
            stops = conn.execute("SELECT COUNT(*) FROM stops").fetchone()[0]
            routes = conn.execute("SELECT COUNT(*) FROM routes").fetchone()[0]
            return {"stops": stops, "routes": routes}
        finally:
            conn.close()

    def ready(self):
        return self.counts()["routes"] > 0

    def status(self):
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT value FROM meta WHERE key = 'last_refresh'"
            ).fetchone()
            last_refresh = int(row[0]) if row is not None and row[0] else None
        finally:
            conn.close()
        return {
            "building": self._building,
            "lastRefresh": last_refresh,
            "counts": self.counts(),
            "lastError": self._last_error,
        }

    def rebuild_from(self, stops, routes, details):
        conn = self._connect()
        try:
            with conn:
                conn.execute("DELETE FROM stops")
                conn.execute("DELETE FROM routes")
                conn.execute("DELETE FROM stop_routes")
                conn.execute("DELETE FROM route_detail")

                for s in stops or []:
                    if not isinstance(s, dict):
                        continue
                    conn.execute(
                        "INSERT OR REPLACE INTO stops"
                        "(id, name, lat, lng, direction, adress, description, shortname)"
                        " VALUES(?,?,?,?,?,?,?,?)",
                        (
                            s.get("id"),
                            s.get("name", "") or "",
                            s.get("lat"),
                            s.get("lng"),
                            s.get("direction"),
                            s.get("adress", "") or "",
                            s.get("description", "") or "",
                            s.get("shortname", "") or "",
                        ),
                    )

                for r in routes or []:
                    if not isinstance(r, dict):
                        continue
                    conn.execute(
                        "INSERT OR REPLACE INTO routes"
                        "(id, number, name, short_name, route_type_id, company,"
                        " from_station, to_station, price)"
                        " VALUES(?,?,?,?,?,?,?,?,?)",
                        (
                            r.get("id"),
                            r.get("number", "") or "",
                            r.get("name", "") or "",
                            r.get("shortName", "") or "",
                            r.get("routeTypeId"),
                            r.get("companyName", "") or "",
                            r.get("fromStation", "") or "",
                            r.get("toStation", "") or "",
                            r.get("price", "") or "",
                        ),
                    )

                for rid, detail in (details or {}).items():
                    if not isinstance(detail, dict):
                        detail = {}
                    conn.execute(
                        "INSERT OR REPLACE INTO route_detail(route_id, payload) VALUES(?,?)",
                        (rid, json.dumps(detail, ensure_ascii=False)),
                    )
                    for sub in detail.get("subroutes") or []:
                        if not isinstance(sub, dict):
                            continue
                        stations = sub.get("stations")
                        if not isinstance(stations, list):
                            continue
                        for st in stations:
                            if not isinstance(st, dict):
                                continue
                            sid = st.get("id")
                            if sid is None:
                                continue
                            conn.execute(
                                "INSERT OR IGNORE INTO stop_routes(stop_id, route_id) VALUES(?,?)",
                                (sid, rid),
                            )

                conn.execute(
                    "INSERT OR REPLACE INTO meta(key, value) VALUES('last_refresh', ?)",
                    (str(int(time.time())),),
                )
        finally:
            conn.close()
        return {"stops": len(stops or []), "routes": len(routes or [])}

    def rebuild(self, portal):
        stops = portal.get_stations() or []
        routes = portal.get_routes() or []
        details = {}
        for r in routes:
            if not isinstance(r, dict):
                continue
            rid = r.get("id")
            if rid is None:
                continue
            details[rid] = portal.get_route(rid)
        result = self.rebuild_from(stops, routes, details)
        self._last_error = None
        return result

    @staticmethod
    def _route_row(row):
        return {
            "id": row["id"],
            "number": row["number"],
            "name": row["name"],
            "shortName": row["short_name"],
            "routeTypeId": row["route_type_id"],
            "company": row["company"],
            "fromStation": row["from_station"],
            "toStation": row["to_station"],
            "price": row["price"],
        }

    @staticmethod
    def _station_row(row):
        return {
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "adress": row["adress"],
            "direction": row["direction"],
            "lat": row["lat"],
            "lng": row["lng"],
            "shortname": row["shortname"],
            "tags": [],
        }

    def all_routes(self, route_type=None):
        conn = self._connect()
        try:
            if route_type is None:
                rows = conn.execute("SELECT * FROM routes").fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM routes WHERE route_type_id = ?", (route_type,)
                ).fetchall()
            return [self._route_row(r) for r in rows]
        finally:
            conn.close()

    def get_route(self, route_id):
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT payload FROM route_detail WHERE route_id = ?", (route_id,)
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return {}
        try:
            return json.loads(row[0])
        except (TypeError, ValueError):
            return {}

    def search_stations(self, q):
        needle = (q or "").strip().lower()
        conn = self._connect()
        try:
            rows = conn.execute("SELECT * FROM stops").fetchall()
        finally:
            conn.close()
        result = []
        for r in rows:
            if needle and needle not in (r["name"] or "").lower():
                continue
            result.append(self._station_row(r))
        return result

    def routes_for_stop(self, sid):
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT r.* FROM routes r"
                " JOIN stop_routes sr ON sr.route_id = r.id"
                " WHERE sr.stop_id = ?",
                (sid,),
            ).fetchall()
            return [self._route_row(r) for r in rows]
        finally:
            conn.close()

    def stops_with_routes(self, q):
        return [
            {
                "id": s["id"],
                "name": s["name"],
                "lat": s["lat"],
                "lng": s["lng"],
                "direction": s["direction"],
                "routes": self.routes_for_stop(s["id"]),
            }
            for s in self.search_stations(q)
        ]
