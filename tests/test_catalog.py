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
