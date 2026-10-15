# Веб-обёртка «Маршрут» (Екатеринбург) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Лёгкий веб-сервис на Python (только stdlib), который показывает прогноз прибытия транспорта на избранных остановках Екатеринбурга и позволяет ходить по меню «маршруты → остановки → прогноз» без карты и без тяжёлого SPA портала.

**Architecture:** `server.py` (http.server: статика + JSON-API) — единственная точка, обращающаяся к порталу через `portal.py` (HTTP-клиент + TTL-кэш + single-flight). Браузер работает только с нашим `/api/*`. Избранное хранится в `favorites.json` (атомарная запись). Фронтенд — чистые HTML/CSS/JS с hash-роутингом.

**Tech Stack:** Python 3 (стандартная библиотека: `http.server`, `urllib.request`, `json`, `ssl`, `threading`), HTML/CSS/JS без сборки, тесты — stdlib `unittest` (без pip).

**Spec:** `docs/superpowers/specs/2026-10-15-marshrut-wrapper-design.md`

## Global Constraints

- Только стандартная библиотека Python 3 — **никаких зависимостей, никакого pip, никакой сборки**. Тесты запускаются `python3 -m unittest`.
- Проверка SSL-сертификата отключается **только** для домена портала (`ssl.CERT_NONE`, `check_hostname=False`).
- Все данные от портала — UTF-8 JSON; ответы нашего API — UTF-8 JSON.
- Конфиг читается из `config.json`; значения по умолчанию: `portalUrl=https://xn--80axnakf7a.xn--80acgfbsl1azdqr.xn--p1ai/php/apiRequest.php`, `token=11111111-50b3-4fec-b922-8a50a1d38366`, `reg=66002`, `wuid=marshrut-web`, `host=0.0.0.0`, `port=8000`, `cacheTtl={routes:21600, route:21600, stations:21600, forecasts:10}`.
- Тело запроса к порталу: `{"t","ct":26,"cd":"<cmd>.php","reg","w":-1,"data":{"wuid",...}}`.
- Команды портала: `getRoutes`, `getRouteById` (`data.id`), `getStations`, `getStationForecasts` (`data.sid`).
- Границы YAGNI: без карты/геолокации/авторизации/БД/уведомлений; только чтение портала.
- Структура репозитория строго по спеке: `server.py`, `portal.py`, `config.json`, `favorites.json`, `static/{index.html,app.js,style.css}`, `tests/{test_portal.py,test_server.py}`, `systemd/marshrut.service`, `README.md`.
- Кэш на стороне сервера: TTL `forecasts≈10с`, `routes/route/stations≈21600с`; защита от параллельного дублирования (single-flight).

## Review Focus

Пять классов входных данных/сбоев, которые спека подразумевает, но задачи-тесты должны явно закрепить (по одной строке; тест живёт в задаче-владельце кода):

1. **Пустой ответ портала** — `getStationForecasts`/`getRouteById` вернули `[]` или `data:null`, либо у маршрута нет `subroutes`. Ожидаемо: пустой список/словарь, без исключения (Task 3, Task 5).
2. **`arrivalTimeInSec` отсутствует/`None`/отрицательный/огромный** — фронт не должен показывать `NaN мин`/"−2 мин" (Task 8).
3. **Невалидный ответ портала** (не JSON, HTTP≥400, обрыв) — `PortalError` → 502, а не 500/падение (Task 1, Task 5).
4. **Отсутствующий/битый `favorites.json`** при старте — сервис стартует с пустым избранным, не падает (Task 4).
5. **Неизвестный `routeTypeId` / отсутствие `number`** — «Другое», без падения; нечисловой `id`/`sid` в запросе → 400 (Task 5).

---

## Файловая структура (что и зачем)

| Файл | Ответственность |
|---|---|
| `portal.py` | `PortalError`, `CachedValue`, `PortalClient` — сеть, кэш, разбор ответов портала |
| `server.py` | `load_config`, `FavoritesStore`, `handle_api`, HTTP-хендлер статики+API, `main` |
| `config.json` | конфигурация (значения по умолчанию = портал) |
| `favorites.json` | избранное (создаётся при первом изменении) |
| `static/index.html` | каркас SPA-обёртки |
| `static/app.js` | hash-роутинг, вызовы `/api/*`, авто-обновление |
| `static/style.css` | адаптивная вёрстка |
| `tests/test_portal.py` | тесты сети/кэша/ошибок портала |
| `tests/test_server.py` | тесты API-роутинга, фильтров, избранного, хендлера |
| `systemd/marshrut.service` | unit для Debian |
| `README.md` | установка, конфиг, systemd |

---

### Task 1: Ядро доступа к порталу (`portal.py`: тело, сеть, ошибки)

**Files:**
- Create: `portal.py`
- Create: `tests/test_portal.py`

**Interfaces:**
- Consumes: ничего.
- Produces:
  - `class PortalError(Exception)` — `PortalError(reason: str, message: str = "")`, атрибуты `.reason`, `.message`.
  - `PortalClient.__init__(self, config: dict)` — читает `portalUrl`, `token`, `reg`, `wuid`, `timeout` (по умолчанию 10).
  - `PortalClient._build_body(self, cmd: str, data: dict) -> dict`
  - `PortalClient._request(self, cmd: str, data: dict) -> Any` — POST, возвращает `payload["data"]`; броски `PortalError`.
  - `PortalClient._ssl_context` — `SSLContext` без проверки сертификата.

- [ ] **Step 1: Написать падающие тесты** в `tests/test_portal.py`

```python
import json
import ssl
import unittest
from unittest import mock

from portal import PortalClient, PortalError

CONFIG = {
    "portalUrl": "https://example.test/php/apiRequest.php",
    "token": "11111111-50b3-4fec-b922-8a50a1d38366",
    "reg": 66002,
    "wuid": "marshrut-web",
    "timeout": 10,
}


class BuildBodyTest(unittest.TestCase):
    def test_builds_portal_envelope(self):
        client = PortalClient(CONFIG)
        body = client._build_body("getRoutes", {})
        self.assertEqual(body["t"], CONFIG["token"])
        self.assertEqual(body["ct"], 26)
        self.assertEqual(body["cd"], "getRoutes.php")
        self.assertEqual(body["reg"], 66002)
        self.assertEqual(body["w"], -1)
        self.assertEqual(body["data"], {"wuid": "marshrut-web"})

    def test_merges_command_params_into_data(self):
        client = PortalClient(CONFIG)
        body = client._build_body("getRouteById", {"id": 1554})
        self.assertEqual(body["data"], {"wuid": "marshrut-web", "id": 1554})


class RequestTest(unittest.TestCase):
    def _fake_response(self, payload: dict):
        resp = mock.MagicMock()
        resp.read.return_value = json.dumps(payload).encode("utf-8")
        resp.__enter__ = mock.Mock(return_value=resp)
        resp.__exit__ = mock.Mock(return_value=False)
        return resp

    def test_returns_data_on_ok(self):
        client = PortalClient(CONFIG)
        with mock.patch("portal.urllib.request.urlopen",
                        return_value=self._fake_response({"r": "ok", "data": [1, 2]})) as uo:
            self.assertEqual(client._request("getRoutes", {}), [1, 2])
        self.assertEqual(uo.call_args[0][0].get_method(), "POST")

    def test_raises_portal_error_on_fail(self):
        client = PortalClient(CONFIG)
        with mock.patch("portal.urllib.request.urlopen",
                        return_value=self._fake_response(
                            {"r": "fail", "reason": "Некорректный токен", "message": "bad"})):
            with self.assertRaises(PortalError) as ctx:
                client._request("getRoutes", {})
        self.assertEqual(ctx.exception.reason, "Некорректный токен")

    def test_raises_portal_error_on_bad_json(self):
        client = PortalClient(CONFIG)
        resp = mock.MagicMock()
        resp.read.return_value = b"<html>oops</html>"
        resp.__enter__ = mock.Mock(return_value=resp)
        resp.__exit__ = mock.Mock(return_value=False)
        with mock.patch("portal.urllib.request.urlopen", return_value=resp):
            with self.assertRaises(PortalError):
                client._request("getRoutes", {})

    def test_raises_portal_error_on_network_failure(self):
        client = PortalClient(CONFIG)
        with mock.patch("portal.urllib.request.urlopen",
                        side_effect=OSError("connection refused")):
            with self.assertRaises(PortalError):
                client._request("getRoutes", {})

    def test_disables_ssl_verification(self):
        client = PortalClient(CONFIG)
        with mock.patch("portal.urllib.request.urlopen",
                        return_value=self._fake_response({"r": "ok", "data": None})) as uo:
            client._request("getRoutes", {})
        ctx = uo.call_args.kwargs["context"]
        self.assertEqual(ctx.verify_mode, ssl.CERT_NONE)
        self.assertFalse(ctx.check_hostname)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Запустить и убедиться, что падает**

Run: `python3 -m unittest tests.test_portal -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'portal'`

- [ ] **Step 3: Реализовать `portal.py`** (импорты `json, ssl, threading, time, urllib.request, urllib.error`)

```python
class PortalError(Exception):
    def __init__(self, reason, message=""):
        super().__init__(f"{reason}: {message}" if message else str(reason))
        self.reason = reason
        self.message = message


class PortalClient:
    def __init__(self, config):
        self._portal_url = config["portalUrl"]
        self._token = config["token"]
        self._reg = config["reg"]
        self._wuid = config.get("wuid", "marshrut-web")
        self._timeout = config.get("timeout", 10)
        self._ssl_context = ssl.create_default_context()
        self._ssl_context.check_hostname = False
        self._ssl_context.verify_mode = ssl.CERT_NONE

    def _build_body(self, cmd, data):
        return {
            "t": self._token, "ct": 26, "cd": f"{cmd}.php", "reg": self._reg, "w": -1,
            "data": {"wuid": self._wuid, **data},
        }

    def _request(self, cmd, data):
        body = json.dumps(self._build_body(cmd, data)).encode("utf-8")
        url = f"{self._portal_url}?{cmd}.php"
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        try:
            with urllib.request.urlopen(req, context=self._ssl_context,
                                        timeout=self._timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise PortalError("network", str(exc)) from exc
        if not isinstance(payload, dict) or payload.get("r") != "ok":
            raise PortalError(payload.get("reason") or "portal",
                              payload.get("message") or "") \
                if isinstance(payload, dict) else PortalError("portal", "invalid payload")
        return payload.get("data")
```

(Убедиться, что ветка `fail` возвращает `PortalError`, а не «выражение-`if`»; при желании — обычным `if/raise`. Смысл: не-`ok` → `PortalError`.)

- [ ] **Step 4: Запустить тесты**

Run: `python3 -m unittest tests.test_portal -v`
Expected: PASS (9 тестов).

- [ ] **Step 5: Коммит**

```bash
git add portal.py tests/test_portal.py
git commit -m "feat(portal): portal HTTP client core with error handling"
```

---

### Task 2: Кэш с TTL и single-flight (`CachedValue`)

**Files:**
- Modify: `portal.py`
- Modify: `tests/test_portal.py`

**Interfaces:**
- Consumes: ничего.
- Produces: `class CachedValue.__init__(self, ttl: float, clock=time.monotonic)` и `CachedValue.get(self, key, producer) -> Any` — возвращает закэшированное значение, если не истекло; иначе вызывает `producer()` один раз даже при параллельных вызовах.

- [ ] **Step 1: Дописать падающие тесты** (в конец `tests/test_portal.py`)

```python
import threading
import time as _time

from portal import CachedValue


class CachedValueTest(unittest.TestCase):
    def test_caches_within_ttl(self):
        now = [100.0]
        cache = CachedValue(ttl=10, clock=lambda: now[0])
        calls = []
        val = lambda: calls.append(1) or "v"
        self.assertEqual(cache.get("k", val), "v")
        self.assertEqual(cache.get("k", val), "v")
        self.assertEqual(len(calls), 1)

    def test_refetches_after_ttl(self):
        now = [100.0]
        cache = CachedValue(ttl=10, clock=lambda: now[0])
        calls = []
        val = lambda: calls.append(1) or "v"
        cache.get("k", val)
        now[0] = 111.0
        cache.get("k", val)
        self.assertEqual(len(calls), 2)

    def test_single_flight(self):
        cache = CachedValue(ttl=10)
        calls = []
        started = threading.Barrier(2)
        def producer():
            calls.append(1)
            _time.sleep(0.2)
            return "v"
        results = []
        def worker():
            started.wait()
            results.append(cache.get("k", producer))
        threads = [threading.Thread(target=worker) for _ in range(2)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(results, ["v", "v"])
        self.assertEqual(len(calls), 1)
```

- [ ] **Step 2: Запустить и убедиться, что падает**

Run: `python3 -m unittest tests.test_portal.CachedValueTest -v`
Expected: FAIL — `ImportError: cannot import name 'CachedValue'`

- [ ] **Step 3: Реализовать `CachedValue` в `portal.py`**

```python
class CachedValue:
    def __init__(self, ttl, clock=time.monotonic):
        self._ttl = ttl
        self._clock = clock
        self._cache = {}
        self._key_locks = {}
        self._lock = threading.Lock()

    def get(self, key, producer):
        with self._lock:
            entry = self._cache.get(key)
            if entry is not None and entry[1] > self._clock():
                return entry[0]
            key_lock = self._key_locks.setdefault(key, threading.Lock())
        with key_lock:                       # single-flight per key
            with self._lock:
                entry = self._cache.get(key)
                if entry is not None and entry[1] > self._clock():
                    return entry[0]
            value = producer()
            with self._lock:
                self._cache[key] = (value, self._clock() + self._ttl)
            return value
```

- [ ] **Step 4: Запустить тесты**

Run: `python3 -m unittest tests.test_portal -v`
Expected: PASS (12 тестов).

- [ ] **Step 5: Коммит**

```bash
git add portal.py tests/test_portal.py
git commit -m "feat(portal): TTL cache with single-flight"
```

---

### Task 3: Методы `PortalClient` (`get_routes`, `get_route`, `get_stations`, `get_forecasts`)

**Files:**
- Modify: `portal.py`
- Modify: `tests/test_portal.py`

**Interfaces:**
- Consumes: `PortalClient._request`, `CachedValue`.
- Produces (используется `server.py`):
  - `get_routes(self) -> list`
  - `get_route(self, route_id: int) -> dict`
  - `get_stations(self) -> list`
  - `get_forecasts(self, sid: int) -> list`

- [ ] **Step 1: Дописать падающие тесты**

```python
class PortalClientMethodsTest(unittest.TestCase):
    def make(self):
        cfg = dict(CONFIG, cacheTtl={"routes": 21600, "route": 21600,
                                     "stations": 21600, "forecasts": 10})
        return PortalClient(cfg)

    def test_get_routes_uses_command_and_caches(self):
        client = self.make()
        client._request = mock.Mock(return_value=[{"id": 1554}])
        client.get_routes(); client.get_routes()
        client._request.assert_called_once_with("getRoutes", {})

    def test_get_route_passes_id(self):
        client = self.make()
        client._request = mock.Mock(return_value={"id": 1554, "subroutes": []})
        self.assertEqual(client.get_route(1554)["id"], 1554)
        client._request.assert_called_once_with("getRouteById", {"id": 1554})

    def test_get_stations_uses_command(self):
        client = self.make()
        client._request = mock.Mock(return_value=[{"id": 5830}])
        client.get_stations()
        client._request.assert_called_once_with("getStations", {})

    def test_get_forecasts_passes_sid_and_caches(self):
        client = self.make()
        client._request = mock.Mock(return_value=[{"arrivalTimeInSec": 575}])
        client.get_forecasts(5830); client.get_forecasts(5830)
        client._request.assert_called_once_with("getStationForecasts", {"sid": 5830})

    def test_empty_portal_data_returns_empty_list(self):
        client = self.make()
        client._request = mock.Mock(return_value=None)
        self.assertEqual(client.get_forecasts(5830), [])
        self.assertEqual(client.get_routes(), [])

    def test_empty_route_returns_empty_dict(self):
        client = self.make()
        client._request = mock.Mock(return_value=None)
        self.assertEqual(client.get_route(1554), {})
```

- [ ] **Step 2: Запустить и убедиться, что падает**

Run: `python3 -m unittest tests.test_portal.PortalClientMethodsTest -v`
Expected: FAIL — `AttributeError: 'PortalClient' object has no attribute 'get_routes'`

- [ ] **Step 3: Реализовать методы** (в `PortalClient.__init__` создать 4 кэша из `cacheTtl`, затем добавить методы с нормализацией `None → []`/`{}`)

```python
    # в __init__:
    ttl = config.get("cacheTtl", {})
    self._routes_cache = CachedValue(ttl.get("routes", 21600))
    self._route_cache = CachedValue(ttl.get("route", 21600))
    self._stations_cache = CachedValue(ttl.get("stations", 21600))
    self._forecast_cache = CachedValue(ttl.get("forecasts", 10))

    def get_routes(self):
        return self._routes_cache.get("all", lambda: self._request("getRoutes", {})) or []

    def get_route(self, route_id):
        return self._route_cache.get(route_id,
                                     lambda: self._request("getRouteById", {"id": route_id})) or {}

    def get_stations(self):
        return self._stations_cache.get("all", lambda: self._request("getStations", {})) or []

    def get_forecasts(self, sid):
        return self._forecast_cache.get(
            sid, lambda: self._request("getStationForecasts", {"sid": sid})) or []
```

- [ ] **Step 4: Запустить тесты**

Run: `python3 -m unittest tests.test_portal -v`
Expected: PASS (18 тестов).

- [ ] **Step 5: Коммит**

```bash
git add portal.py tests/test_portal.py
git commit -m "feat(portal): public fetch methods with per-category caching"
```

---

### Task 4: Хранилище избранного (`FavoritesStore`)

**Files:**
- Create: `server.py`
- Create: `tests/test_server.py`

**Interfaces:**
- Consumes: ничего.
- Produces:
  - `FavoritesStore.__init__(self, path: str)`
  - `FavoritesStore.load(self) -> dict` — `{"routes": [...], "stops": [...]}`; отсутствующий/битый файл → пустое избранное.
  - `FavoritesStore.add(self, kind: str, item: dict) -> dict` — `kind ∈ {"route","stop"}`; дубликаты по `id` игнорируются; возвращает всё избранное.
  - `FavoritesStore.remove(self, kind: str, item_id) -> dict`.
  - атомарная запись (temp-файл в том же каталоге + `os.replace`).

- [ ] **Step 1: Написать падающие тесты** `tests/test_server.py`

```python
import json
import os
import tempfile
import unittest

import server


class FavoritesStoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "favorites.json")

    def tearDown(self):
        self.dir.cleanup()

    def test_missing_file_loads_empty(self):
        self.assertEqual(server.FavoritesStore(self.path).load(),
                         {"routes": [], "stops": []})

    def test_corrupt_file_loads_empty(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        self.assertEqual(server.FavoritesStore(self.path).load(),
                         {"routes": [], "stops": []})

    def test_add_persists_across_instances(self):
        store = server.FavoritesStore(self.path)
        store.add("stop", {"id": 5830, "name": "Академия тенниса"})
        again = server.FavoritesStore(self.path).load()
        self.assertEqual(again["stops"], [{"id": 5830, "name": "Академия тенниса"}])

    def test_add_duplicate_id_ignored(self):
        store = server.FavoritesStore(self.path)
        store.add("route", {"id": 1554, "number": "87", "name": "A"})
        store.add("route", {"id": 1554, "number": "87", "name": "A"})
        self.assertEqual(len(store.load()["routes"]), 1)

    def test_remove(self):
        store = server.FavoritesStore(self.path)
        store.add("stop", {"id": 5830, "name": "X"})
        store.remove("stop", 5830)
        self.assertEqual(store.load()["stops"], [])

    def test_write_is_atomic(self):
        store = server.FavoritesStore(self.path)
        calls = []
        real_replace = os.replace
        def spy(src, dst):
            calls.append((src, dst))
            return real_replace(src, dst)
        with unittest.mock.patch("server.os.replace", side_effect=spy):
            store.add("stop", {"id": 1, "name": "n"})
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1], self.path)
        self.assertTrue(calls[0][0].startswith(self.dir.name))
        self.assertFalse(os.path.exists(calls[0][0]))  # temp-файл переименован


if __name__ == "__main__":
    unittest.main()
```

(Добавить `from unittest import mock` или `import unittest.mock` наверху файла.)

- [ ] **Step 2: Запустить и убедиться, что падает**

Run: `python3 -m unittest tests.test_server.FavoritesStoreTest -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'server'`

- [ ] **Step 3: Реализовать `FavoritesStore` в `server.py`**

```python
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
```

Импорты наверху `server.py`: `json, os, tempfile, threading`.

- [ ] **Step 4: Запустить тесты**

Run: `python3 -m unittest tests.test_server.FavoritesStoreTest -v`
Expected: PASS (6 тестов).

- [ ] **Step 5: Коммит**

```bash
git add server.py tests/test_server.py
git commit -m "feat(server): atomic favorites store"
```

---

### Task 5: Логика API (`handle_api`)

**Files:**
- Modify: `server.py`
- Modify: `tests/test_server.py`

**Interfaces:**
- Consumes: `PortalClient`-подобный объект (duck-typed: `get_routes/get_route/get_stations/get_forecasts`), `FavoritesStore`, `PortalError`, `KIND_KEYS`.
- Produces: `handle_api(path: str, params: dict, method: str, body: dict | None, portal, store) -> tuple[int, dict]` — возвращает `(http_status, payload_dict)`.

Правила:
- `GET /api/routes` + необязательный `type` → `{"routes":[...]}`; фильтр по `routeTypeId`.
- `GET /api/route` + `id` → `{"route":{...}}`; `id` не число → 400.
- `GET /api/stations` + необязательный `q` → `{"stations":[...]}`; подстрока имени, без учёта регистра.
- `GET /api/forecast` + `sid` → `{"forecasts":[...]}`; сортировка по `arrivalTimeInSec` (без ключа — в конец); `sid` не число → 400.
- `GET /api/favorites` → избранное; `POST /api/favorites` `{action,kind,id,name}` → изменение; `kind` вне `{"route","stop"}` → 400. Для `stop` при отсутствии `name` — подставить имя из `portal.get_stations()`; для `route` — из `portal.get_routes()`.
- Неизвестный `/api/*` → 404 `{"error":{"reason":"not_found",...}}`.
- `PortalError` → 502.

- [ ] **Step 1: Дописать падающие тесты**

```python
class FakePortal:
    def __init__(self):
        self.routes = [
            {"id": 1554, "number": "87", "shortName": "87",
             "name": "Академика Ландау – Горбольница № 7", "routeTypeId": 1},
            {"id": 2000, "number": "7", "name": "Трамвай", "routeTypeId": 3},
        ]
        self.stations = [
            {"id": 5830, "name": "Академия тенниса"},
            {"id": 5831, "name": "Академика Ландау"},
        ]
        self.forecasts = {5830: [
            {"routeNumber": "87", "whereGo": "X", "arrivalTimeInSec": 575},
            {"routeNumber": "87", "whereGo": "X", "arrivalTimeInSec": 21},
        ]}

    def get_routes(self):
        return self.routes

    def get_route(self, rid):
        return {"id": rid, "number": "87", "subroutes": []}

    def get_stations(self):
        return self.stations

    def get_forecasts(self, sid):
        return self.forecasts.get(sid, [])


class HandleApiTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = server.FavoritesStore(os.path.join(self.dir.name, "fav.json"))
        self.portal = FakePortal()

    def tearDown(self):
        self.dir.cleanup()

    def call(self, path, params=None, method="GET", body=None):
        return server.handle_api(path, params or {}, method, body, self.portal, self.store)

    def test_routes_all(self):
        status, payload = self.call("/api/routes")
        self.assertEqual(status, 200)
        self.assertEqual(len(payload["routes"]), 2)

    def test_routes_type_filter(self):
        status, payload = self.call("/api/routes", {"type": "3"})
        self.assertEqual([r["id"] for r in payload["routes"]], [2000])

    def test_routes_unknown_type_id_renders_other(self):
        self.portal.routes.append({"id": 1, "number": "X", "routeTypeId": 99})
        status, payload = self.call("/api/routes")
        self.assertEqual(status, 200)  # не падает на неизвестном routeTypeId

    def test_stations_search_case_insensitive(self):
        status, payload = self.call("/api/stations", {"q": "академ"})
        self.assertEqual([s["id"] for s in payload["stations"]], [5830, 5831])

    def test_forecast_sorted_by_arrival(self):
        status, payload = self.call("/api/forecast", {"sid": "5830"})
        self.assertEqual([f["arrivalTimeInSec"] for f in payload["forecasts"]], [21, 575])

    def test_forecast_empty_ok(self):
        status, payload = self.call("/api/forecast", {"sid": "999"})
        self.assertEqual((status, payload), (200, {"forecasts": []}))

    def test_route_by_id(self):
        status, payload = self.call("/api/route", {"id": "1554"})
        self.assertEqual(payload["route"]["id"], 1554)

    def test_missing_id_is_400(self):
        status, payload = self.call("/api/route", {})
        self.assertEqual(status, 400)

    def test_non_numeric_sid_is_400(self):
        status, payload = self.call("/api/forecast", {"sid": "abc"})
        self.assertEqual(status, 400)

    def test_unknown_api_path_is_404(self):
        status, payload = self.call("/api/nope")
        self.assertEqual(status, 404)

    def test_portal_error_is_502(self):
        def boom(_):
            raise server.PortalError("network", "down")
        self.portal.get_forecasts = boom
        status, payload = self.call("/api/forecast", {"sid": "5830"})
        self.assertEqual(status, 502)
        self.assertEqual(payload["error"]["reason"], "network")

    def test_favorites_add_route_and_dedupe(self):
        self.call("/api/favorites", method="POST",
                  body={"action": "add", "kind": "route", "id": 1554})
        status, payload = self.call("/api/favorites")
        self.assertEqual(len(payload["routes"]), 1)

    def test_favorites_add_stop_fills_name(self):
        self.call("/api/favorites", method="POST",
                  body={"action": "add", "kind": "stop", "id": 5830})
        status, payload = self.call("/api/favorites")
        self.assertEqual(payload["stops"], [{"id": 5830, "name": "Академия тенниса"}])

    def test_favorites_remove(self):
        self.call("/api/favorites", method="POST",
                  body={"action": "add", "kind": "stop", "id": 5830, "name": "X"})
        self.call("/api/favorites", method="POST",
                  body={"action": "remove", "kind": "stop", "id": 5830})
        status, payload = self.call("/api/favorites")
        self.assertEqual(payload["stops"], [])

    def test_favorites_bad_kind_is_400(self):
        status, payload = self.call("/api/favorites", method="POST",
                                    body={"action": "add", "kind": "planet", "id": 1})
        self.assertEqual(status, 400)
```

- [ ] **Step 2: Запустить и убедиться, что падает**

Run: `python3 -m unittest tests.test_server.HandleApiTest -v`
Expected: FAIL — `AttributeError: module 'server' has no attribute 'handle_api'`

- [ ] **Step 3: Реализовать `handle_api`** — чистый роутер, возвращающий `(status, payload)`

```python
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
                key=lambda f: f.get("arrivalTimeInSec") if isinstance(f.get("arrivalTimeInSec"), (int, float)) else float("inf"),
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
```

- [ ] **Step 4: Запустить тесты**

Run: `python3 -m unittest tests.test_server -v`
Expected: PASS (все тесты FavoritesStoreTest + HandleApiTest).

- [ ] **Step 5: Коммит**

```bash
git add server.py tests/test_server.py
git commit -m "feat(server): JSON API routing with filtering and favorites"
```

---

### Task 6: HTTP-сервер, статика и `main`

**Files:**
- Modify: `server.py`
- Modify: `tests/test_server.py`

**Interfaces:**
- Consumes: `handle_api`, `FavoritesStore`, `PortalClient`.
- Produces:
  - `load_config(path="config.json") -> dict` — читает файл, накладывает дефолты из Global Constraints.
  - `make_handler(portal, store, static_dir) -> type[BaseHTTPRequestHandler]`.
  - `serve(config) -> ThreadingHTTPServer` (не блокирует) и `main()` (`serve` + `serve_forever`).

Правила: `GET /api/...` → `handle_api`; прочие `GET` → файл из `static/` (пустой путь → `index.html`); нет файла → 404 `{"error":{...}}`.

- [ ] **Step 1: Дописать падающий тест** (реальный сокет на `127.0.0.1:0`)

```python
import threading
import urllib.request


class HttpServerTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        open(os.path.join(self.dir.name, "index.html"), "w", encoding="utf-8").write("<h1>ok</h1>")
        self.store = server.FavoritesStore(os.path.join(self.dir.name, "fav.json"))
        cfg = dict(CONFIG_HTTP, host="127.0.0.1", port=0)
        self.httpd = server.serve(cfg, portal=FakePortal(),
                                  store=self.store, static_dir=self.dir.name)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.dir.cleanup()

    def get(self, url):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{url}") as resp:
            return resp.status, resp.read().decode("utf-8")

    def test_serves_api(self):
        status, body = self.get("/api/routes")
        self.assertEqual(status, 200)
        self.assertIn("routes", body)

    def test_serves_index(self):
        status, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("<h1>ok</h1>", body)

    def test_unknown_static_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/missing.css")
        self.assertEqual(ctx.exception.code, 404)
```

Плюс `CONFIG_HTTP` рядом с `CONFIG` — словарь с `portalUrl/token/reg/wuid/cacheTtl`. Тип `serve(config, portal=None, store=None, static_dir="static") -> ThreadingHTTPServer` (инъекция для тестов).

- [ ] **Step 2: Запустить и убедиться, что падает**

Run: `python3 -m unittest tests.test_server.HttpServerTest -v`
Expected: FAIL — `AttributeError: module 'server' has no attribute 'serve'`

- [ ] **Step 3: Реализовать** `load_config`, `make_handler`, `serve`, `main`

```python
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
```

Импорты добавить: `http.server`, `mimetypes`, `urllib.parse`.

- [ ] **Step 4: Запустить все тесты**

Run: `python3 -m unittest discover -s tests -v`
Expected: PASS (все: portal + server).

- [ ] **Step 5: Коммит**

```bash
git add server.py tests/test_server.py
git commit -m "feat(server): HTTP server, static files and main entrypoint"
```

---

### Task 7: `config.json`, systemd unit, README

**Files:**
- Create: `config.json`
- Create: `systemd/marshrut.service`
- Create: `README.md`

**Interfaces:**
- Consumes: `load_config` (ключи должны совпасть со спекой).
- Produces: конфиг и инструкции деплоя.

- [ ] **Step 1: Создать `config.json`** (ровно как в спеке, раздел 9)

```json
{
  "portalUrl": "https://xn--80axnakf7a.xn--80acgfbsl1azdqr.xn--p1ai/php/apiRequest.php",
  "token": "11111111-50b3-4fec-b922-8a50a1d38366",
  "reg": 66002,
  "wuid": "marshrut-web",
  "host": "0.0.0.0",
  "port": 8000,
  "cacheTtl": { "routes": 21600, "route": 21600, "stations": 21600, "forecasts": 10 }
}
```

- [ ] **Step 2: Проверить, что сервер стартует с этим конфигом**

Run: `python3 -c "import server; print(server.load_config()['port'])"`
Expected: `8000`

- [ ] **Step 3: Создать `systemd/marshrut.service`**

```ini
[Unit]
Description=Marshrut web wrapper (Yekaterinburg transport)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/marshrut
ExecStart=/usr/bin/python3 /opt/marshrut/server.py
Restart=on-failure
RestartSec=3
User=marshrut
Group=marshrut

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 4: Создать `README.md`** — разделы: что это; требования (Python 3, без зависимостей); быстрый старт (`python3 server.py`, открыть `http://localhost:8000`); `config.json`; избранное (`favorites.json`); тесты (`python3 -m unittest discover -s tests`); деплой на Debian (`/opt/marshrut`, `systemctl enable --now marshrut`); опциональный nginx reverse-proxy на `127.0.0.1:8000`; обновление (`git pull`, `systemctl restart marshrut`).

- [ ] **Step 5: Коммит**

```bash
git add config.json systemd/marshrut.service README.md
git commit -m "chore: config, systemd unit and README"
```

---

### Task 8: Фронтенд (`static/`)

**Files:**
- Create: `static/index.html`
- Create: `static/app.js`
- Create: `static/style.css`

**Interfaces:**
- Consumes: наше HTTP API `/api/routes`, `/api/route?id=`, `/api/stations?q=`, `/api/forecast?sid=`, `/api/favorites` (GET/POST).
- Produces: экраны, описанные в спеке §8.

- [ ] **Step 1: `index.html`** — каркас: `<nav>` (Избранное / Маршруты / Остановки), `<main id="view">`, подключение `app.js` и `style.css`. Никаких CDN.

- [ ] **Step 2: `app.js`** — hash-роутер и рендер
  - Роуты: `#/` (избранное, у каждой избранной остановки живой прогноз), `#/routes` (каталог + фильтр по типу + поиск по номеру + ⭐), `#/route/:id` (переключатель направлений, список остановок, клик → `#/stop/:sid`, ⭐), `#/stops` (поиск по названию), `#/stop/:sid` (прогноз по всем маршрутам, авто-обновление ~20 с, ⭐).
  - `routeTypeId` → подпись: `1=Автобус, 2=Троллейбус, 3=Трамвай`, иначе `Другое`.
  - Строка прогноза: номер · `whereGo` · «через N мин» · ожидаемое время (`now + arrivalTimeInSec`). Формат-функция:

```javascript
function formatEta(sec) {
  if (sec === null || sec === undefined || isNaN(sec) || sec < 0) return "—";
  const min = Math.floor(sec / 60);
  if (min < 1) return "меньше минуты";
  return `через ${min} мин`;
}
```

  - Авто-обновление: `setInterval` на экране остановки; при уходе с экрана — очищать. Индикатор «обновлено в HH:MM:SS».
  - Ошибки: если ответ не `ok` — баннер; если есть предыдущие данные — оставить их с пометкой «устарело».
  - Избранное: `POST /api/favorites` `{action,kind,id}`; после ответа перерисовать ⭐.

- [ ] **Step 3: `style.css`** — адаптивная вёрстка (одна колонка на телефоне, шире на десктопе), читаемые списки, статус-баннеры.

- [ ] **Step 4: Ручная проверка (запустить сервер)**

Run: `python3 server.py` → открыть `http://localhost:8000`
Проверить: `#/routes` показывает маршруты и фильтрует по типу; `#/route/1554` показывает оба направления и остановки; клик по «Академия тенниса» → `#/stop/5830` показывает живые прибытия и обновляется; добавление ⭐ в избранное и появление его на `#/`; поиск в `#/stops`; при остановке сервера — баннер об ошибке.
Expected: все сценарии работают; фронт не показывает `NaN мин`/отрицательных минут.

- [ ] **Step 5: Коммит**

```bash
git add static/index.html static/app.js static/style.css
git commit -m "feat(web): frontend with hash routing, favorites and live forecasts"
```

---

## Self-Review (заполняется автором плана)

- **Покрытие спеки:** §3 команды → Task 1/3; §5 `portal.py` (интерфейс+кэш+SSL) → Task 1–3; §6 HTTP API (все 6 маршрутов) → Task 5; §7 избранное (атомарность, дубликаты, дополнение `name`) → Task 4/5; §8 фронтенд (все 5 экранов, авто-обновление, ошибки) → Task 8; §9 config → Task 7; §10 обработка ошибок (400/404/502, сервер не падает) → Task 1/5/6; §11 тесты → Task 1–6; §12 деплой (systemd, README, nginx) → Task 7.
- **Типы/имена:** `_build_body`/`_request`/`get_routes`/`get_route`/`get_stations`/`get_forecasts`, `CachedValue.get(key, producer)`, `FavoritesStore.load/add/remove`, `handle_api(path, params, method, body, portal, store) -> (status, payload)`, `serve(config, portal, store, static_dir)` — согласованы между задачами.
- **Review Focus:** п.1 → Task 3 (`test_empty_*`), Task 5 (`test_forecast_empty_ok`); п.2 → Task 8 (formatEta); п.3 → Task 1 (`test_raises_portal_error_on_bad_json`/`_on_network_failure`), Task 5 (`test_portal_error_is_502`); п.4 → Task 4 (`test_missing_file_loads_empty`/`test_corrupt_file_loads_empty`); п.5 → Task 5 (`test_routes_unknown_type_id_renders_other`, `test_non_numeric_sid_is_400`, `test_favorites_bad_kind_is_400`).
- **Пропорция:** план ≈ спека по объёму; тела даны только там, где алгоритм не выводится из тестов (single-flight, атомарная запись, роутер, хендлер).
