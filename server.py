import json
import os
import tempfile
import threading


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
