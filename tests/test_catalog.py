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
