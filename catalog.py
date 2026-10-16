"""Локальное зеркало каталога портала в SQLite.

Класс ``Catalog`` инкапсулирует один файл SQLite со справочными данными:
остановки, маршруты, связи «остановка → маршрут» и полные детали маршрутов.
Сервер предпочитает эти данные, а на портал ходит только за живыми
прогнозами и данными о бортах.
"""

import sqlite3
import threading

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
