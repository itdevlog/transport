import json
import os
import sqlite3
import tempfile
import unittest

import profiles


class ProfilesStoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "profiles.db")
        self.store = profiles.ProfilesStore(self.path)

    def tearDown(self):
        self.dir.cleanup()

    def test_missing_profile_is_empty(self):
        self.assertEqual(self.store.get("a" * 32), {"routes": [], "stops": []})

    def test_save_and_get_roundtrip(self):
        data = {"routes": [{"id": 1554, "number": "87", "name": "R"}], "stops": []}
        self.store.save("a" * 32, data)
        self.assertEqual(self.store.get("a" * 32), data)

    def test_profiles_isolated_by_uid(self):
        self.store.save("a" * 32, {"routes": [{"id": 1}], "stops": []})
        self.store.save("b" * 32, {"routes": [], "stops": [{"id": 2}]})
        self.assertEqual(self.store.get("a" * 32)["stops"], [])
        self.assertEqual(self.store.get("b" * 32)["routes"], [])

    def test_corrupt_row_returns_empty(self):
        self.store.save("a" * 32, {"routes": [], "stops": []})
        con = sqlite3.connect(self.path)
        con.execute("UPDATE profiles SET data = ? WHERE uid = ?", ("{not json", "a" * 32))
        con.commit()
        con.close()
        self.assertEqual(self.store.get("a" * 32), {"routes": [], "stops": []})

    def test_wrong_shape_row_returns_empty(self):
        self.store.save("a" * 32, {"routes": [], "stops": []})
        con = sqlite3.connect(self.path)
        con.execute("UPDATE profiles SET data = ? WHERE uid = ?", ("[1,2,3]", "a" * 32))
        con.commit()
        con.close()
        self.assertEqual(self.store.get("a" * 32), {"routes": [], "stops": []})

    def test_upsert_overwrites(self):
        self.store.save("a" * 32, {"routes": [], "stops": [{"id": 1}]})
        self.store.save("a" * 32, {"routes": [], "stops": []})
        self.assertEqual(self.store.get("a" * 32)["stops"], [])
