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


if __name__ == "__main__":
    unittest.main()
