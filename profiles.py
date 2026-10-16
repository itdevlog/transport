"""Per-user favorites profiles stored in SQLite.

Each browser gets an anonymous id (see server.py cookie handling); a
profile is the same shape as the legacy favorites.json document:
{"routes": [...], "stops": [...]}.
"""

import json
import sqlite3
import time

SCHEMA_VERSION = 1
KIND_KEYS = {"route": "routes", "stop": "stops"}

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS profiles ("
    "uid TEXT PRIMARY KEY, "
    "data TEXT NOT NULL, "
    "updated INTEGER NOT NULL)"
)


def empty_profile():
    return {"routes": [], "stops": []}


class ProfilesStore:
    def __init__(self, db_path):
        self._db_path = db_path
        self._init_db()

    def _connect(self):
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        conn = self._connect()
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            with conn:
                conn.executescript(_SCHEMA)
        finally:
            conn.close()

    def get(self, uid):
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT data FROM profiles WHERE uid = ?", (uid,)
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return empty_profile()
        try:
            data = json.loads(row["data"])
        except (ValueError, TypeError):
            return empty_profile()
        if not isinstance(data, dict):
            return empty_profile()
        routes = data.get("routes")
        stops = data.get("stops")
        if not isinstance(routes, list) or not isinstance(stops, list):
            return empty_profile()
        return {"routes": routes, "stops": stops}

    def save(self, uid, data):
        payload = json.dumps(data, ensure_ascii=False)
        now = int(time.time())
        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    "INSERT INTO profiles (uid, data, updated) VALUES (?, ?, ?) "
                    "ON CONFLICT(uid) DO UPDATE SET data = excluded.data, "
                    "updated = excluded.updated",
                    (uid, payload, now),
                )
        finally:
            conn.close()
