import json
import os
import sqlite3
import tempfile
import threading
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


class ProfilesMutationTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = profiles.ProfilesStore(os.path.join(self.dir.name, "p.db"))
        self.uid = "c" * 32

    def tearDown(self):
        self.dir.cleanup()

    def test_add_and_dedupe(self):
        self.store.add(self.uid, "stop", {"id": 5830, "name": "Академия тенниса"})
        self.store.add(self.uid, "stop", {"id": 5830, "name": "dup"})
        self.assertEqual(len(self.store.get(self.uid)["stops"]), 1)

    def test_remove(self):
        self.store.add(self.uid, "stop", {"id": 5830, "name": "A"})
        self.store.remove(self.uid, "stop", 5830)
        self.assertEqual(self.store.get(self.uid)["stops"], [])

    def test_set_note_persists_and_clears(self):
        self.store.add(self.uid, "stop", {"id": 5830, "name": "A"})
        self.store.set_note(self.uid, "stop", 5830, "в школу")
        self.assertEqual(self.store.get(self.uid)["stops"][0]["note"], "в школу")
        self.store.set_note(self.uid, "stop", 5830, "")
        self.assertNotIn("note", self.store.get(self.uid)["stops"][0])

    def test_set_dir_label_and_clear(self):
        self.store.add(self.uid, "stop", {"id": 5830, "name": "A"})
        self.store.set_dir_label(self.uid, "stop", 5830, "Академика Ландау", "домой")
        self.assertEqual(self.store.get(self.uid)["stops"][0]["dirs"],
                         {"Академика Ландау": "домой"})
        self.store.set_dir_label(self.uid, "stop", 5830, "Академика Ландау", "")
        self.assertNotIn("dirs", self.store.get(self.uid)["stops"][0])

    def test_concurrent_adds_do_not_lose_updates(self):
        def worker(i):
            self.store.add(self.uid, "stop", {"id": 1000 + i, "name": "s%d" % i})

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(self.store.get(self.uid)["stops"]), 20)
