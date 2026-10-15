import json
import os
import tempfile
import unittest
import unittest.mock

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


if __name__ == "__main__":
    unittest.main()
