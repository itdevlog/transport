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
