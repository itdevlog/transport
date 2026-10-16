# Local Catalog Mirror Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a local SQLite mirror of portal stop/route data so the wrapper serves the catalog without repeatedly querying the origin server.

**Architecture:** A new `catalog.py` module wraps a single SQLite file (`stops`, `routes`, `stop_routes`, `route_detail`, `meta`). `server.py` prefers the catalog for reference endpoints (routes, route detail, stations, stop-routes) and adds two endpoints (`/api/stops`, `/api/catalog`) plus `/api/refresh`. A background thread rebuilds the mirror on startup and daily; live endpoints (forecast, vehicle) remain online.

**Tech Stack:** Python standard library only (`sqlite3`, `threading`, `json`), `unittest`.

**Spec:** `docs/superpowers/specs/2026-10-16-catalog-mirror-design.md`

## Global Constraints

- Python standard library only; no third-party packages. `sqlite3` is stdlib.
- Tests run with `python3 -m unittest discover -s tests` (namespace package `tests`, no `__init__.py`); a single test runs as `python3 -m unittest tests.test_catalog.<Class>.<method> -v`.
- Portal command wrapper is unchanged: POST `<portalUrl>?<cmd>.php` with body `{t, ct:26, cd:"<cmd>.php", reg, w:-1, data:{wuid,...}}`.
- `routeTypeId`: `1=Автобус`, `2=Троллейбус`, `4=Трамвай` (no `3`).
- `catalog.db` lives next to `server.py`, like `favorites.json`; it is git-ignored.
- Keep all existing endpoints and their response shapes; existing callers must not break.
- `handle_api`, `make_handler`, `serve` gain a new optional `catalog=None` parameter (keyword-compatible with existing tests).

## Review Focus

1. **Concurrent rebuild while serving** — a reader querying during a rebuild must never see a half-written catalog or raise; a reasonable person expects the old data to keep answering until the swap completes.
2. **Malformed portal payload** — a route detail missing `subroutes`, a subroute missing `stations`, or a non-dict item must not corrupt the DB or abort with an unhandled error; a reasonable person expects the mirror to skip bad items and still build.
3. **Corrupt / empty / unreadable SQLite file** — the server must keep answering from the portal instead of crashing; a reasonable person expects a broken `catalog.db` to be non-fatal.
4. **Duplicate stop ids across subroutes** — the same stop appearing in many subroutes must map to a route once; a reasonable person expects each route listed a single time per stop.
5. **Cyrillic / case / whitespace search** — searching "площадь" must match «Площадь 1905 года» regardless of case, and a blank query must return everything; a reasonable person expects accent- and case-insensitive substring match.

---

### Task 1: Catalog skeleton (schema, counts, status)

**Files:**
- Create: `catalog.py`
- Test: `tests/test_catalog.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Catalog(path)` with `counts() -> {"stops": int, "routes": int}`, `ready() -> bool` (True when `counts()["routes"] > 0`), `status() -> {"building": bool, "lastRefresh": int|None, "counts": {...}, "lastError": str|None}`, and `SCHEMA_VERSION = 1`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_catalog.py
import os
import tempfile
import unittest

import catalog


class CatalogSkeletonTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "catalog.db")

    def tearDown(self):
        self.dir.cleanup()

    def test_empty_counts_and_not_ready(self):
        c = catalog.Catalog(self.path)
        self.assertEqual(c.counts(), {"stops": 0, "routes": 0})
        self.assertFalse(c.ready())

    def test_status_shape_on_empty(self):
        c = catalog.Catalog(self.path)
        st = c.status()
        self.assertEqual(st["building"], False)
        self.assertIsNone(st["lastRefresh"])
        self.assertEqual(st["counts"], {"stops": 0, "routes": 0})

    def test_reopen_is_idempotent(self):
        catalog.Catalog(self.path)
        c = catalog.Catalog(self.path)  # second open must not fail
        self.assertEqual(c.counts(), {"stops": 0, "routes": 0})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_catalog -v`
Expected: FAIL — `ModuleNotFoundError` / `AttributeError: module 'catalog' has no attribute 'Catalog'`.

- [ ] **Step 3: Implement `Catalog` skeleton in `catalog.py`**

`Catalog.__init__(self, path)` opens `sqlite3.connect(path)` via a private `_connect()` and calls `self._init_db()`. `_init_db()` executes the full schema from the spec §6 (`CREATE TABLE IF NOT EXISTS` for `stops`, `routes`, `stop_routes`, `route_detail`, `meta`, with the three indexes), creates `self._lock = threading.Lock()`, `self._building = False`, `self._last_error = None`, and writes `meta['schema_version'] = str(SCHEMA_VERSION)`; if an existing `schema_version` differs, drop the tables and recreate. `counts()` returns `SELECT COUNT(*)` for `stops` and `routes`; `ready()` is `counts()["routes"] > 0`; `status()` reads `meta['last_refresh']` (int or None) and returns the shape above with `lastError = self._last_error`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_catalog -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add catalog.py tests/test_catalog.py
git commit -m "feat(catalog): SQLite skeleton with schema, counts, status"
```

---

### Task 2: `rebuild_from` — write fetched data

**Files:**
- Modify: `catalog.py`
- Test: `tests/test_catalog.py`

**Interfaces:**
- Consumes: `Catalog` from Task 1.
- Produces: `Catalog.rebuild_from(stops, routes, details) -> {"stops": int, "routes": int}` where `stops`/`routes` are the portal lists and `details` is a `dict[int, dict]` mapping route id → `getRouteById` payload. Writes everything in one transaction, replaces prior rows, sets `meta['last_refresh']`, and derives `stop_routes` from `details[*].subroutes[*].stations[*].id`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_catalog.py
class CatalogRebuildFromTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.c = catalog.Catalog(os.path.join(self.dir.name, "catalog.db"))
        self.stops = [
            {"id": 5830, "name": "Академия тенниса", "lat": 56.77, "lng": 60.52, "direction": -29,
             "adress": "", "description": "", "shortname": "", "tags": []},
            {"id": 5831, "name": "Академика Ландау", "lat": 56.78, "lng": 60.53, "direction": 12,
             "adress": "", "description": "", "shortname": "", "tags": []},
        ]
        self.routes = [
            {"id": 1554, "number": "87", "name": "Академика Ландау – Горбольница № 7",
             "shortName": "А-87", "routeTypeId": 1, "companyName": "ЕМУП Гортранс",
             "fromStation": "Академика Ландау", "toStation": "Горбольница № 7", "price": "36"},
        ]
        self.details = {
            1554: {"subroutes": [
                {"id": 1555, "stations": [{"id": 5830}, {"id": 5831}, {"id": 5830}]},
                {"id": 1556, "stations": [{"id": 5831}]},
            ]}
        }

    def tearDown(self):
        self.dir.cleanup()

    def test_rebuild_from_counts_and_last_refresh(self):
        result = self.c.rebuild_from(self.stops, self.routes, self.details)
        self.assertEqual(result, {"stops": 2, "routes": 1})
        self.assertEqual(self.c.counts(), {"stops": 2, "routes": 1})
        self.assertTrue(self.c.ready())
        self.assertIsNotNone(self.c.status()["lastRefresh"])

    def test_rebuild_replaces_previous(self):
        self.c.rebuild_from(self.stops, self.routes, self.details)
        self.c.rebuild_from(self.stops[:1], self.routes, {})
        self.assertEqual(self.c.counts(), {"stops": 1, "routes": 1})

    def test_malformed_detail_is_skipped(self):
        result = self.c.rebuild_from(self.stops, self.routes,
                                     {1554: {"subroutes": [{"stations": None}, "junk"]}})
        self.assertEqual(result, {"stops": 2, "routes": 1})  # builds without raising
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_catalog -v`
Expected: FAIL — `AttributeError: 'Catalog' object has no attribute 'rebuild_from'`.

- [ ] **Step 3: Implement `rebuild_from`**

Insert `stops` and `routes` row-by-row with `INSERT OR REPLACE`; for each `(rid, detail)` iterate `detail.get("subroutes") or []`, skip non-dict subroutes and non-list `stations`, collect `st["id"]` for dict stations, and insert `(stop_id, route_id)` into `stop_routes` with `INSERT OR IGNORE` (dedupe). Store `json.dumps(detail, ensure_ascii=False)` in `route_detail`. Wrap all of it in a single `with conn:` transaction; clear the four data tables first. Set `meta['last_refresh'] = str(int(time.time()))` inside the same transaction. Read `stops`/`routes` fields with `.get(..., default)` so missing keys never raise.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_catalog -v`
Expected: PASS (all Task 1 + Task 2 tests).

- [ ] **Step 5: Commit**

```bash
git add catalog.py tests/test_catalog.py
git commit -m "feat(catalog): rebuild_from writes stops, routes, links, details"
```

---

### Task 3: `rebuild(portal)` — fetch then write

**Files:**
- Modify: `catalog.py`
- Test: `tests/test_catalog.py`

**Interfaces:**
- Consumes: `Catalog.rebuild_from` (Task 2); `PortalClient.get_stations() -> list[dict]`, `get_routes() -> list[dict]`, `get_route(id) -> dict`.
- Produces: `Catalog.rebuild(portal) -> {"stops": int, "routes": int}` — calls the three portal methods, builds `details = {r["id"]: portal.get_route(r["id"]) for r in routes}`, then delegates to `rebuild_from`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_catalog.py
class _FakePortal:
    def __init__(self):
        self.calls = []
        self.stations = [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}]
        self.routes = [{"id": 10, "number": "1", "routeTypeId": 1}]
        self.detail = {10: {"subroutes": [{"stations": [{"id": 1}, {"id": 2}]}]}}

    def get_stations(self):
        return self.stations

    def get_routes(self):
        return self.routes

    def get_route(self, rid):
        self.calls.append(rid)
        return self.detail.get(rid, {})


class CatalogRebuildTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.c = catalog.Catalog(os.path.join(self.dir.name, "catalog.db"))

    def tearDown(self):
        self.dir.cleanup()

    def test_rebuild_fetches_all_details_once(self):
        p = _FakePortal()
        result = self.c.rebuild(p)
        self.assertEqual(result, {"stops": 2, "routes": 1})
        self.assertEqual(p.calls, [10])
        self.assertEqual(self.c.counts(), {"stops": 2, "routes": 1})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_catalog -v`
Expected: FAIL — `AttributeError: 'Catalog' object has no attribute 'rebuild'`.

- [ ] **Step 3: Implement `rebuild(portal)`**

Fetch, then delegate. Do not catch `PortalError` here — the caller (`refresh_async`, Task 5) stores the error. Set `self._last_error = None` on success.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_catalog -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add catalog.py tests/test_catalog.py
git commit -m "feat(catalog): rebuild(portal) fetches and writes the mirror"
```

---

### Task 4: Catalog queries

**Files:**
- Modify: `catalog.py`
- Test: `tests/test_catalog.py`

**Interfaces:**
- Consumes: data written by `rebuild_from`/`rebuild`.
- Produces:
  - `all_routes(route_type=None) -> list[dict]` — rows as `{id, number, name, shortName, routeTypeId, company, fromStation, toStation, price}`, filtered by `routeTypeId` when `route_type` given.
  - `get_route(route_id) -> dict` — parsed `route_detail.payload`, or `{}`.
  - `search_stations(q) -> list[dict]` — stations as `{id, name, description, adress, direction, lat, lng, shortname, tags:[]}`, case-insensitive substring on `name`; empty `q` returns all.
  - `routes_for_stop(sid) -> list[dict]` — route dicts (same shape as `all_routes`) serving the stop, no duplicates.
  - `stops_with_routes(q) -> list[dict]` — `{id, name, lat, lng, direction, routes: [route dicts]}`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_catalog.py
class CatalogQueriesTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.c = catalog.Catalog(os.path.join(self.dir.name, "catalog.db"))
        self.c.rebuild_from(
            [{"id": 513, "name": "Площадь 1905 года", "lat": 56.8, "lng": 60.5, "direction": 71},
             {"id": 5830, "name": "Академия тенниса", "lat": 56.7, "lng": 60.5, "direction": -29}],
            [{"id": 1554, "number": "87", "name": "Академика Ландау – Горбольница № 7",
              "shortName": "А-87", "routeTypeId": 1},
             {"id": 2000, "number": "7", "name": "Трамвай", "shortName": "Тм-7", "routeTypeId": 4}],
            {1554: {"subroutes": [{"stations": [{"id": 513}, {"id": 5830}, {"id": 513}]}]},
             2000: {"subroutes": [{"stations": [{"id": 513}]}]}},
        )

    def tearDown(self):
        self.dir.cleanup()

    def test_search_is_case_insensitive_cyrillic(self):
        names = [s["name"] for s in self.c.search_stations("площадь")]
        self.assertEqual(names, ["Площадь 1905 года"])

    def test_search_blank_returns_all(self):
        self.assertEqual(len(self.c.search_stations("")), 2)

    def test_routes_for_stop_dedup_and_type_filter(self):
        routes = self.c.routes_for_stop(513)
        self.assertEqual(sorted(r["number"] for r in routes), ["7", "87"])
        self.assertEqual([r["number"] for r in self.c.all_routes(4)], ["7"])
        self.assertEqual(self.c.all_routes(1)[0]["number"], "87")

    def test_get_route_returns_payload(self):
        self.assertEqual(self.c.get_route(1554)["subroutes"][0]["stations"][1]["id"], 5830)
        self.assertEqual(self.c.get_route(999), {})

    def test_stops_with_routes_shape(self):
        stops = {s["id"]: s for s in self.c.stops_with_routes("академия")}
        self.assertEqual(list(stops), [5830])
        self.assertEqual(stops[5830]["routes"][0]["routeTypeId"], 1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_catalog -v`
Expected: FAIL — `AttributeError` on the query methods.

- [ ] **Step 3: Implement the five query methods**

Open a fresh read connection per call. `search_stations` uses `WHERE name LIKE ?` with `%q%` (SQLite `LIKE` is case-insensitive for ASCII; for Cyrillic use Python-side `.lower()` filtering after `SELECT *` to be safe). Map columns back to the portal field names (`short_name`→`shortname`, `route_type_id`→`routeTypeId`, etc.). `routes_for_stop` joins `stop_routes` and `routes`. `stops_with_routes` runs the station search, then `routes_for_stop` per matched stop.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_catalog -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add catalog.py tests/test_catalog.py
git commit -m "feat(catalog): search, routes-for-stop and stops-with-routes queries"
```

---

### Task 5: Refresh orchestration (single-flight, schedule)

**Files:**
- Modify: `catalog.py`
- Test: `tests/test_catalog.py`

**Interfaces:**
- Consumes: `Catalog.rebuild` (Task 3).
- Produces: `should_refresh(now, interval) -> bool` (True when `lastRefresh is None` or `now - lastRefresh >= interval`); `refresh_async(portal, now=None) -> str` returning `"started"` when it launches a thread, `"building"` when a rebuild is already running; `is_building() -> bool`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_catalog.py
class CatalogRefreshTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.c = catalog.Catalog(os.path.join(self.dir.name, "catalog.db"))

    def tearDown(self):
        self.dir.cleanup()

    def test_should_refresh_when_never_refreshed(self):
        self.assertTrue(self.c.should_refresh(1000, 86400))

    def test_should_refresh_respects_interval(self):
        self.c.rebuild_from([{"id": 1, "name": "A"}], [], {})
        self.assertFalse(self.c.should_refresh(self.c.status()["lastRefresh"] + 1, 86400))

    def test_refresh_async_records_error_and_keeps_old_data(self):
        self.c.rebuild_from([{"id": 1, "name": "A"}], [{"id": 9, "number": "9"}], {})
        self.c.refresh_async(_BadPortal())  # raises inside the thread
        import time
        for _ in range(50):
            if not self.c.is_building():
                break
            time.sleep(0.02)
        self.assertEqual(self.c.counts()["stops"], 1)  # old data intact
        self.assertIsNotNone(self.c.status()["lastError"])


class _BadPortal:
    def get_stations(self):
        raise RuntimeError("boom")

    def get_routes(self):
        raise RuntimeError("boom")

    def get_route(self, rid):
        raise RuntimeError("boom")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_catalog -v`
Expected: FAIL — `AttributeError: should_refresh`.

- [ ] **Step 3: Implement `should_refresh`, `refresh_async`, `is_building`**

`refresh_async` sets `self._building` True under a lock and returns `"building"` if already set; otherwise starts a `threading.Thread(daemon=True)` whose target runs `try: self.rebuild(portal) except Exception as exc: self._last_error = str(exc)` and finally clears `self._building`. `should_refresh` compares `now or time.time()` against `status()["lastRefresh"]`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_catalog -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add catalog.py tests/test_catalog.py
git commit -m "feat(catalog): scheduled single-flight background refresh"
```

---

### Task 6: Server integration (reference endpoints + new endpoints)

**Files:**
- Modify: `server.py` (`handle_api`, add `_catalog_ready` helper)
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes: `Catalog.all_routes`, `.get_route`, `.search_stations`, `.routes_for_stop`, `.stops_with_routes`, `.ready`, `.status`, `.refresh_async`; existing `PortalClient`/`FavoritesStore`.
- Produces: `handle_api(path, params, method, body, portal, store, catalog=None)`; new routes `GET /api/stops?q=`, `POST /api/refresh`, `GET /api/catalog`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_server.py
class CatalogIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = server.FavoritesStore(os.path.join(self.dir.name, "favorites.json"))
        self.portal = FakePortal()

    def tearDown(self):
        self.dir.cleanup()

    def _catalog(self):
        c = catalog.Catalog(os.path.join(self.dir.name, "catalog.db"))
        c.rebuild_from(
            [{"id": 5830, "name": "Академия тенниса", "lat": 56.7, "lng": 60.5, "direction": -29}],
            [{"id": 1554, "number": "87", "name": "n", "shortName": "А-87", "routeTypeId": 1}],
            {1554: {"subroutes": [{"stations": [{"id": 5830}]}]}},
        )
        return c

    def call(self, path, params=None, method="GET", body=None, catalog=None):
        return server.handle_api(path, params or {}, method, body, self.portal, self.store, catalog)

    def test_stops_endpoint_from_catalog(self):
        status, payload = self.call("/api/stops", {"q": "тенниса"}, catalog=self._catalog())
        self.assertEqual(status, 200)
        self.assertEqual(payload["stops"][0]["id"], 5830)
        self.assertEqual(payload["stops"][0]["routes"][0]["number"], "87")

    def test_reference_endpoints_fall_back_to_portal(self):
        status, payload = self.call("/api/stop-routes", {"sid": "5830"}, catalog=None)
        self.assertEqual(status, 200)
        self.assertEqual(payload["routes"][0]["routeTypeId"], 1)  # FakePortal path

    def test_reference_endpoints_prefer_catalog(self):
        status, payload = self.call("/api/routes", catalog=self._catalog())
        self.assertEqual([r["number"] for r in payload["routes"]], ["87"])

    def test_catalog_status_and_refresh(self):
        c = self._catalog()
        status, payload = self.call("/api/catalog", catalog=c)
        self.assertTrue(payload["enabled"])
        status, payload = self.call("/api/refresh", method="POST", catalog=c)
        self.assertIn(payload["status"], ("started", "building"))
        status, payload = self.call("/api/refresh", method="POST", catalog=None)
        self.assertEqual(payload["status"], "disabled")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_server -v`
Expected: FAIL — `/api/stops` returns 404; `handle_api` has no `catalog` parameter.

- [ ] **Step 3: Implement integration**

Add `catalog=None` to `handle_api`. Add helper `_catalog_ready(catalog)` = `catalog is not None and catalog.ready()`. For `/api/routes`, `/api/route`, `/api/stations`, `/api/stop-routes`: use the catalog when ready, else the existing portal path. Add `/api/stops` (catalog when ready; else portal `get_stations` filtered with `routes: []`), `/api/refresh` (POST → `catalog.refresh_async(portal)` or `disabled`), `/api/catalog` (`{enabled: False}` when `catalog is None`, else `{enabled: True, **catalog.status()}`). Keep the existing `PortalError`→502 and catch-all→500 wrappers.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest discover -s tests`
Expected: PASS (existing + new server tests).

- [ ] **Step 5: Commit**

```bash
git add server.py tests/test_server.py
git commit -m "feat(server): serve reference endpoints from catalog; add stops/refresh/catalog"
```

---

### Task 7: Wiring, config, and docs

**Files:**
- Modify: `server.py` (`DEFAULTS`, `make_handler`, `serve`), `config.json`, `.gitignore`, `README.md`, `DEPLOY.md`
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes: `Catalog` (Tasks 1–5), `handle_api` (Task 6).
- Produces: `make_handler(portal, store, static_dir, catalog=None)`; `serve(config, portal=None, store=None, static_dir="static", catalog=None)`; when `config["catalog"]["enabled"]`, `serve` builds a `Catalog` at `config["catalog"]["path"]` (resolved next to `server.py`) and starts a daemon watchdog thread that calls `catalog.refresh_async(portal)` when `should_refresh(time.time(), refreshSeconds)` or on first run.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_server.py
class CatalogWiringTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        (open(os.path.join(self.dir.name, "index.html"), "w")).write("<h1>ok</h1>")

    def tearDown(self):
        self.dir.cleanup()

    def test_serve_accepts_catalog_and_serves_from_it(self):
        c = catalog.Catalog(os.path.join(self.dir.name, "catalog.db"))
        c.rebuild_from([{"id": 1, "name": "A"}], [{"id": 9, "number": "9", "routeTypeId": 1}],
                       {9: {"subroutes": [{"stations": [{"id": 1}]}]}})
        cfg = dict(CONFIG_HTTP, host="127.0.0.1", port=0)
        httpd = server.serve(cfg, portal=FakePortal(), store=server.FavoritesStore(
            os.path.join(self.dir.name, "f.json")), static_dir=self.dir.name, catalog=c)
        try:
            port = httpd.server_address[1]
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/routes") as r:
                body = json.loads(r.read())
            self.assertEqual([x["number"] for x in body["routes"]], ["9"])
        finally:
            httpd.server_close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_server -v`
Expected: FAIL — `serve() got an unexpected keyword argument 'catalog'`.

- [ ] **Step 3: Implement wiring and docs**

Add `catalog` default block to `DEFAULTS` (`{"enabled": True, "path": "catalog.db", "refreshSeconds": 86400}`). Thread `catalog` through `make_handler` and `serve`; in `serve`, if `catalog is None and config["catalog"]["enabled"]`, construct `Catalog(os.path.join(os.path.dirname(__file__), config["catalog"]["path"]))` and start the watchdog daemon thread. Add `catalog.db` to `.gitignore`; add the `catalog` block to `config.json`; document the mirror, `/api/refresh`, and the first-run background build in `README.md`; note `catalog.db` in `ReadWritePaths` in `DEPLOY.md`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest discover -s tests`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add server.py config.json .gitignore README.md DEPLOY.md tests/test_server.py
git commit -m "feat(server): wire catalog into serve/main; config, gitignore, docs"
```

---

## Self-Review

- **Spec coverage:** §5–7 → Tasks 1–5; §8 → Task 6; §9 → Tasks 6–7; §10 → tests in Tasks 1–7; §11 → Task 7; §12/§13 explicitly out of scope.
- **Review Focus coverage:** 1→Task 2 (`test_rebuild_replaces_previous`) + Task 6 (read during/after); 2→Task 2 `test_malformed_detail_is_skipped`; 3→Task 1 `test_reopen_is_idempotent` + Task 6 fallback test; 4→Task 2 dedup assertion; 5→Task 4 `test_search_is_case_insensitive_cyrillic` + `test_search_blank_returns_all`.
- **Type consistency:** `rebuild_from(stops, routes, details)` and `rebuild(portal)` both return `{"stops","routes"}`; `routes_for_stop`/`all_routes` return the same route dict shape; `handle_api(..., catalog=None)` matches `make_handler`/`serve`.
