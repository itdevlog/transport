import json
import os
import re
import tempfile
import threading
import time
import unittest
import unittest.mock
import urllib.error
import urllib.request

import catalog
import profiles
import server

CONFIG_HTTP = {
    "portalUrl": "https://example.test/php/apiRequest.php",
    "token": "11111111-50b3-4fec-b922-8a50a1d38366",
    "reg": 66002,
    "wuid": "marshrut-web",
    "cacheTtl": {"routes": 21600, "route": 21600, "stations": 21600, "forecasts": 10},
}


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

    def test_wrong_shape_file_loads_empty(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("[1, 2, 3]")
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

    def test_set_note_persists(self):
        store = server.FavoritesStore(self.path)
        store.add("stop", {"id": 5830, "name": "Академия тенниса"})
        store.set_note("stop", 5830, "в школу")
        again = server.FavoritesStore(self.path).load()
        self.assertEqual(again["stops"],
                         [{"id": 5830, "name": "Академия тенниса", "note": "в школу"}])

    def test_set_note_empty_clears(self):
        store = server.FavoritesStore(self.path)
        store.add("stop", {"id": 5830, "name": "X"})
        store.set_note("stop", 5830, "на работу")
        store.set_note("stop", 5830, "")
        again = server.FavoritesStore(self.path).load()
        self.assertNotIn("note", again["stops"][0])

    def test_set_note_unknown_id_is_noop(self):
        store = server.FavoritesStore(self.path)
        store.add("stop", {"id": 5830, "name": "X"})
        store.set_note("stop", 999, "в школу")
        self.assertNotIn("note", store.load()["stops"][0])

    def test_set_dir_label_persists(self):
        store = server.FavoritesStore(self.path)
        store.add("stop", {"id": 5830, "name": "X"})
        store.set_dir_label("stop", 5830, "Академика Ландау", "в школу")
        again = server.FavoritesStore(self.path).load()
        self.assertEqual(again["stops"][0]["dirs"], {"Академика Ландау": "в школу"})

    def test_set_dir_label_overwrites(self):
        store = server.FavoritesStore(self.path)
        store.add("stop", {"id": 5830, "name": "X"})
        store.set_dir_label("stop", 5830, "A", "в школу")
        store.set_dir_label("stop", 5830, "A", "домой")
        self.assertEqual(store.load()["stops"][0]["dirs"], {"A": "домой"})

    def test_set_dir_label_empty_removes_key(self):
        store = server.FavoritesStore(self.path)
        store.add("stop", {"id": 5830, "name": "X"})
        store.set_dir_label("stop", 5830, "A", "в школу")
        store.set_dir_label("stop", 5830, "A", "")
        self.assertNotIn("dirs", store.load()["stops"][0])

    def test_set_dir_label_unknown_id_is_noop(self):
        store = server.FavoritesStore(self.path)
        store.add("stop", {"id": 5830, "name": "X"})
        store.set_dir_label("stop", 999, "A", "в школу")
        self.assertNotIn("dirs", store.load()["stops"][0])

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
        self.vehicle = {"deviceCode": "X", "vehicle": {
            "id": 2628, "gosNumber": "КР 583 66", "garageNumber": "кр583",
            "brand": "ПАЗ", "model": "320415-04 «Vector Next»",
            "vehicleClass": {"id": 4, "name": "СК", "shortName": "СК"}}}
        self.vehicle_forecasts = [
            {"stname": "Академика Вонсовского", "arrt": 99},
            {"stname": "Михеева", "arrt": 151},
        ]

    def get_routes(self):
        return self.routes

    def get_route(self, rid):
        return {"id": rid, "number": "87", "subroutes": []}

    def get_stations(self):
        return self.stations

    def get_forecasts(self, sid):
        return self.forecasts.get(sid, [])

    def get_station_routes(self, sid):
        if sid == 5830:
            return [{"id": 1554, "number": "87", "shortName": "87",
                     "name": "Академика Ландау – Горбольница № 7", "routeTypeId": 1}]
        return []

    def get_vehicle(self, device_code):
        return self.vehicle

    def get_vehicle_forecasts(self, device_code):
        return self.vehicle_forecasts


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

    def test_internal_error_is_500(self):
        def boom(_):
            raise ValueError("kaboom")
        self.portal.get_forecasts = boom
        status, payload = self.call("/api/forecast", {"sid": "5830"})
        self.assertEqual(status, 500)
        self.assertEqual(payload["error"]["reason"], "internal")

    def test_store_os_error_is_500(self):
        self.store = unittest.mock.Mock()
        self.store.load.side_effect = OSError("disk full")
        status, payload = self.call("/api/favorites")
        self.assertEqual(status, 500)
        self.assertEqual(payload["error"]["reason"], "internal")

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

    def test_favorites_note_set_and_read(self):
        self.call("/api/favorites", method="POST",
                  body={"action": "add", "kind": "stop", "id": 5830})
        status, payload = self.call("/api/favorites", method="POST",
                                    body={"action": "note", "kind": "stop", "id": 5830,
                                          "note": "в школу"})
        self.assertEqual(status, 200)
        self.assertEqual(payload["stops"][0]["note"], "в школу")

    def test_favorites_note_trims_spaces(self):
        self.call("/api/favorites", method="POST",
                  body={"action": "add", "kind": "stop", "id": 5830})
        self.call("/api/favorites", method="POST",
                  body={"action": "note", "kind": "stop", "id": 5830, "note": "  в школу  "})
        status, payload = self.call("/api/favorites")
        self.assertEqual(payload["stops"][0]["note"], "в школу")

    def test_favorites_note_without_id_is_400(self):
        status, payload = self.call("/api/favorites", method="POST",
                                    body={"action": "note", "kind": "stop", "note": "x"})
        self.assertEqual(status, 400)

    def test_favorites_note_too_long_is_400(self):
        self.call("/api/favorites", method="POST",
                  body={"action": "add", "kind": "stop", "id": 5830})
        status, payload = self.call("/api/favorites", method="POST",
                                    body={"action": "note", "kind": "stop", "id": 5830,
                                          "note": "x" * 61})
        self.assertEqual(status, 400)

    def test_favorites_note_empty_clears(self):
        self.call("/api/favorites", method="POST",
                  body={"action": "add", "kind": "stop", "id": 5830})
        self.call("/api/favorites", method="POST",
                  body={"action": "note", "kind": "stop", "id": 5830, "note": "в школу"})
        status, payload = self.call("/api/favorites", method="POST",
                                    body={"action": "note", "kind": "stop", "id": 5830,
                                          "note": ""})
        self.assertEqual(status, 200)
        self.assertNotIn("note", payload["stops"][0])


    def test_stop_routes_ok(self):
        status, payload = self.call("/api/stop-routes", {"sid": "5830"})
        self.assertEqual(status, 200)
        self.assertEqual([r["id"] for r in payload["routes"]], [1554])

    def test_stop_routes_requires_sid(self):
        status, _ = self.call("/api/stop-routes", {})
        self.assertEqual(status, 400)

    def test_vehicle_ok(self):
        status, payload = self.call("/api/vehicle", {"deviceCode": "X"})
        self.assertEqual(status, 200)
        self.assertEqual(payload["vehicle"]["vehicle"]["gosNumber"], "КР 583 66")
        self.assertEqual(payload["vehicle"]["vehicle"]["vehicleClass"]["name"], "СК")
        self.assertEqual(payload["forecasts"][0]["stname"], "Академика Вонсовского")

    def test_vehicle_requires_device_code(self):
        status, _ = self.call("/api/vehicle", {})
        self.assertEqual(status, 400)

    def test_favorites_dir_set_and_read(self):
        self.call("/api/favorites", method="POST",
                  body={"action": "add", "kind": "stop", "id": 5830})
        status, payload = self.call("/api/favorites", method="POST",
                                    body={"action": "dir", "kind": "stop", "id": 5830,
                                          "where": "Академика Ландау", "label": "в школу"})
        self.assertEqual(status, 200)
        self.assertEqual(payload["stops"][0]["dirs"], {"Академика Ландау": "в школу"})

    def test_favorites_dir_without_where_is_400(self):
        self.call("/api/favorites", method="POST",
                  body={"action": "add", "kind": "stop", "id": 5830})
        status, _ = self.call("/api/favorites", method="POST",
                              body={"action": "dir", "kind": "stop", "id": 5830,
                                    "label": "в школу"})
        self.assertEqual(status, 400)

    def test_favorites_dir_label_too_long_is_400(self):
        self.call("/api/favorites", method="POST",
                  body={"action": "add", "kind": "stop", "id": 5830})
        status, _ = self.call("/api/favorites", method="POST",
                              body={"action": "dir", "kind": "stop", "id": 5830,
                                    "where": "A", "label": "x" * 61})
        self.assertEqual(status, 400)

    def test_favorites_dir_empty_clears(self):
        self.call("/api/favorites", method="POST",
                  body={"action": "add", "kind": "stop", "id": 5830})
        self.call("/api/favorites", method="POST",
                  body={"action": "dir", "kind": "stop", "id": 5830,
                        "where": "A", "label": "в школу"})
        status, payload = self.call("/api/favorites", method="POST",
                                    body={"action": "dir", "kind": "stop", "id": 5830,
                                          "where": "A", "label": ""})
        self.assertEqual(status, 200)
        self.assertNotIn("dirs", payload["stops"][0])


class HttpServerTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        with open(os.path.join(self.dir.name, "index.html"), "w", encoding="utf-8") as fh:
            fh.write("<h1>ok</h1>")
        self.store = server.FavoritesStore(os.path.join(self.dir.name, "fav.json"))
        cfg = dict(CONFIG_HTTP, host="127.0.0.1", port=0)
        self.httpd = server.serve(cfg, portal=FakePortal(),
                                  store=self.store, static_dir=self.dir.name)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()
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


class FavoritesCookieTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        with open(os.path.join(self.dir.name, "index.html"), "w", encoding="utf-8") as fh:
            fh.write("<h1>ok</h1>")
        self.store = server.FavoritesStore(os.path.join(self.dir.name, "fav.json"))
        self.profiles = profiles.ProfilesStore(os.path.join(self.dir.name, "profiles.db"))
        cfg = dict(CONFIG_HTTP, host="127.0.0.1", port=0)
        self.httpd = server.serve(cfg, portal=FakePortal(), store=self.store,
                                  static_dir=self.dir.name, profiles=self.profiles)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()
        self.dir.cleanup()

    def _request(self, method, url, cookie=None, payload=None):
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if cookie is not None:
            headers["Cookie"] = cookie
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{url}", data=data,
                                     headers=headers, method=method)
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, resp.read().decode("utf-8"), resp.headers.get("Set-Cookie")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8"), exc.headers.get("Set-Cookie")

    def test_first_request_sets_cookie_and_empty_profile(self):
        status, body, set_cookie = self._request("GET", "/api/favorites")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"routes": [], "stops": []})
        self.assertIsNotNone(set_cookie)
        self.assertIn("marshrut_uid=", set_cookie)
        self.assertIn("HttpOnly", set_cookie)
        self.assertIn("SameSite=Lax", set_cookie)
        self.assertIn("Max-Age=31536000", set_cookie)

    def test_same_cookie_returns_same_profile(self):
        _, _, set_cookie = self._request("GET", "/api/favorites")
        c1 = set_cookie.split(";")[0]
        status, _, _ = self._request(
            "POST", "/api/favorites", cookie=c1,
            payload={"action": "add", "kind": "stop", "id": 5830, "name": "A"})
        self.assertEqual(status, 200)
        status, body, _ = self._request("GET", "/api/favorites", cookie=c1)
        self.assertEqual(status, 200)
        self.assertEqual(len(json.loads(body)["stops"]), 1)
        c2 = "marshrut_uid=" + "a" * 32
        status, body, _ = self._request("GET", "/api/favorites", cookie=c2)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"routes": [], "stops": []})

    def test_garbage_cookie_is_replaced(self):
        status, _, set_cookie = self._request(
            "GET", "/api/favorites", cookie="marshrut_uid=nothex")
        self.assertEqual(status, 200)
        self.assertIsNotNone(set_cookie)
        self.assertIn("marshrut_uid=", set_cookie)
        uid = set_cookie.split("marshrut_uid=")[1].split(";")[0]
        self.assertRegex(uid, r"^[0-9a-f]{32}$")


class MigrationTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.legacy = os.path.join(self.dir.name, "favorites.json")
        self.db = os.path.join(self.dir.name, "profiles.db")

    def test_legacy_adopted_once_and_renamed(self):
        store = server.FavoritesStore(self.legacy)
        store.add("stop", {"id": 5830, "name": "Академия тенниса"})
        pstore = profiles.ProfilesStore(self.db)
        server._maybe_migrate(pstore, store, "a" * 32)
        self.assertEqual([s["id"] for s in pstore.get("a" * 32)["stops"]], [5830])
        self.assertFalse(os.path.exists(self.legacy))
        self.assertTrue(os.path.exists(self.legacy + ".migrated"))
        server._maybe_migrate(pstore, store, "b" * 32)
        self.assertEqual(pstore.get("b" * 32)["stops"], [])

    def test_corrupt_legacy_is_ignored(self):
        with open(self.legacy, "w", encoding="utf-8") as fh:
            fh.write("not json")
        store = server.FavoritesStore(self.legacy)
        pstore = profiles.ProfilesStore(self.db)
        server._maybe_migrate(pstore, store, "c" * 32)
        self.assertEqual(pstore.get("c" * 32)["stops"], [])
        self.assertTrue(os.path.exists(self.legacy + ".migrated"))


class LoadConfigTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = os.path.join(self.dir.name, "config.json")

    def _write(self, name, data):
        with open(os.path.join(self.dir.name, name), "w", encoding="utf-8") as fh:
            json.dump(data, fh)

    def test_defaults_when_no_files(self):
        config = server.load_config(self.path)
        self.assertEqual(config["port"], 8000)
        self.assertEqual(config["host"], "0.0.0.0")

    def test_file_overrides_defaults(self):
        self._write("config.json", {"port": 9000})
        config = server.load_config(self.path)
        self.assertEqual(config["port"], 9000)
        self.assertEqual(config["host"], "0.0.0.0")

    def test_local_overrides_file(self):
        self._write("config.json", {"port": 9000, "host": "0.0.0.0"})
        self._write("config.local.json", {"host": "127.0.0.1"})
        config = server.load_config(self.path)
        self.assertEqual(config["port"], 9000)
        self.assertEqual(config["host"], "127.0.0.1")

    def test_missing_local_is_fine(self):
        self._write("config.json", {"port": 8091})
        config = server.load_config(self.path)
        self.assertEqual(config["port"], 8091)

    def test_corrupt_local_is_ignored(self):
        self._write("config.json", {"port": 8091})
        with open(os.path.join(self.dir.name, "config.local.json"), "w", encoding="utf-8") as fh:
            fh.write("{ not json")
        config = server.load_config(self.path)
        self.assertEqual(config["port"], 8091)

    def test_wrong_shape_file_is_ignored(self):
        self._write("config.json", [1, 2, 3])
        config = server.load_config(self.path)
        self.assertEqual(config["port"], 8000)


class CatalogIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = server.FavoritesStore(os.path.join(self.dir.name, "favorites.json"))
        self.portal = FakePortal()
        self._catalogs = []

    def tearDown(self):
        # /api/refresh запускает фоновую пересборку в daemon-потоке; дождаться её,
        # иначе поток создаёт файлы SQLite, пока TemporaryDirectory их удаляет.
        deadline = time.time() + 5
        while time.time() < deadline and any(c.is_building() for c in self._catalogs):
            time.sleep(0.02)
        self.dir.cleanup()

    def _catalog(self):
        c = catalog.Catalog(os.path.join(self.dir.name, "catalog.db"))
        c.rebuild_from(
            [{"id": 5830, "name": "Академия тенниса", "lat": 56.7, "lng": 60.5, "direction": -29}],
            [{"id": 1554, "number": "87", "name": "n", "shortName": "А-87", "routeTypeId": 1}],
            {1554: {"subroutes": [{"stations": [{"id": 5830}]}]}},
        )
        self._catalogs.append(c)
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


if __name__ == "__main__":
    unittest.main()


class CatalogWiringTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        with open(os.path.join(self.dir.name, "index.html"), "w") as fh:
            fh.write("<h1>ok</h1>")

    def tearDown(self):
        self.dir.cleanup()

    def test_serve_accepts_catalog_and_serves_from_it(self):
        c = catalog.Catalog(os.path.join(self.dir.name, "catalog.db"))
        c.rebuild_from([{"id": 1, "name": "A"}], [{"id": 9, "number": "9", "routeTypeId": 1}],
                       {9: {"subroutes": [{"stations": [{"id": 1}]}]}})
        cfg = dict(CONFIG_HTTP, host="127.0.0.1", port=0)
        httpd = server.serve(cfg, portal=FakePortal(), store=server.FavoritesStore(
            os.path.join(self.dir.name, "f.json")), static_dir=self.dir.name, catalog=c)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            port = httpd.server_address[1]
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/routes", timeout=5) as r:
                body = json.loads(r.read())
            self.assertEqual([x["number"] for x in body["routes"]], ["9"])
        finally:
            httpd.shutdown()
            httpd.server_close()


class CatalogConfiguredStartupTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        with open(os.path.join(self.dir.name, "index.html"), "w") as fh:
            fh.write("<h1>ok</h1>")

    def tearDown(self):
        self.dir.cleanup()

    def _serve_enabled(self, db_path):
        cfg = dict(CONFIG_HTTP, host="127.0.0.1", port=0, catalog={
            "enabled": True, "path": db_path, "refreshSeconds": 86400})
        return server.serve(cfg, portal=FakePortal(),
                            store=server.FavoritesStore(os.path.join(self.dir.name, "f.json")),
                            static_dir=self.dir.name)

    def _get(self, httpd, path):
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            port = httpd.server_address[1]
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
                return r.status, json.loads(r.read())
        finally:
            httpd.shutdown()
            httpd.server_close()

    def test_serve_builds_catalog_when_enabled(self):
        httpd = self._serve_enabled(os.path.join(self.dir.name, "catalog.db"))
        status, body = self._get(httpd, "/api/catalog")
        self.assertEqual(status, 200)
        self.assertTrue(body["enabled"])

    def test_serve_survives_corrupt_db_and_uses_portal(self):
        path = os.path.join(self.dir.name, "catalog.db")
        with open(path, "wb") as fh:
            fh.write(b"not a database")
        httpd = self._serve_enabled(path)
        status, body = self._get(httpd, "/api/stations")
        self.assertEqual(status, 200)
        self.assertTrue(any(s["name"] == "Академия тенниса" for s in body["stations"]))

    def test_reference_endpoints_fall_back_on_db_error(self):
        path = os.path.join(self.dir.name, "catalog.db")
        c = catalog.Catalog(path)
        c.rebuild_from([{"id": 1, "name": "A"}],
                       [{"id": 9, "number": "9", "routeTypeId": 1}],
                       {9: {"subroutes": [{"stations": [{"id": 1}]}]}})
        with open(path, "wb") as fh:
            fh.write(b"corrupted")
        store = server.FavoritesStore(os.path.join(self.dir.name, "f.json"))
        status, body = server.handle_api("/api/routes", {}, "GET", None, FakePortal(), store, c)
        self.assertEqual(status, 200)
        self.assertEqual([r["number"] for r in body["routes"]], ["87", "7"])

    def test_watchdog_sleep_caps_retry_interval(self):
        self.assertEqual(server._watchdog_sleep(86400), 300)
        self.assertEqual(server._watchdog_sleep(60), 60)
        self.assertEqual(server._watchdog_sleep(0), 300)


class ProfilesConfiguredStartupTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        with open(os.path.join(self.dir.name, "index.html"), "w") as fh:
            fh.write("<h1>ok</h1>")

    def tearDown(self):
        self.dir.cleanup()

    def _serve(self, db_path):
        cfg = dict(CONFIG_HTTP, host="127.0.0.1", port=0,
                   profiles={"path": db_path})
        return server.serve(cfg, portal=FakePortal(),
                            store=server.FavoritesStore(os.path.join(self.dir.name, "f.json")),
                            static_dir=self.dir.name)

    def _get(self, httpd, path):
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            port = httpd.server_address[1]
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
                return r.status, json.loads(r.read()), r.headers
        finally:
            httpd.shutdown()
            httpd.server_close()

    def test_serve_creates_profiles_store(self):
        db = os.path.join(self.dir.name, "profiles.db")
        httpd = self._serve(db)
        status, body, headers = self._get(httpd, "/api/favorites")
        self.assertEqual(status, 200)
        self.assertEqual(body, {"routes": [], "stops": []})
        self.assertIn("marshrut_uid=", headers.get("Set-Cookie", ""))
        self.assertTrue(os.path.exists(db))

    def test_corrupt_profiles_db_falls_back(self):
        db = os.path.join(self.dir.name, "profiles.db")
        with open(db, "wb") as fh:
            fh.write(b"not a database")
        httpd = self._serve(db)
        status, body, _ = self._get(httpd, "/api/favorites")
        self.assertEqual(status, 200)
        self.assertEqual(body, {"routes": [], "stops": []})
