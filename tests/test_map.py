import os
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(BASE, "static")


def _read(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as fh:
        return fh.read()


class LeafletVendorTest(unittest.TestCase):
    def test_vendor_files_present(self):
        for name in ("leaflet.js", "leaflet.css", "LICENSE"):
            path = os.path.join(STATIC, "vendor", "leaflet", name)
            self.assertTrue(os.path.isfile(path), name + " missing")
            self.assertGreater(os.path.getsize(path), 100)

    def test_leaflet_version(self):
        self.assertIn("Leaflet 1.9.4", _read(os.path.join("vendor", "leaflet", "leaflet.js")))


class MapViewTest(unittest.TestCase):
    def test_nav_has_map_link(self):
        self.assertIn('href="#/map"', _read("index.html"))

    def test_app_uses_osm_tiles_with_attribution(self):
        app = _read("app.js")
        self.assertIn("tile.openstreetmap.org", app)
        self.assertIn("OpenStreetMap", app)
        self.assertIn('parts[0] === "map"', app)

    def test_service_worker_cache_bumped(self):
        self.assertIn("marshrut-v3", _read("sw.js"))
