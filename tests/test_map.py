import os
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(BASE, "static")


def _read(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as fh:
        return fh.read()


class MapLibreVendorTest(unittest.TestCase):
    def test_vendor_files_present(self):
        for name in ("maplibre-gl.js", "maplibre-gl.css", "LICENSE.txt"):
            path = os.path.join(STATIC, "vendor", "maplibre", name)
            self.assertTrue(os.path.isfile(path), name + " missing")
            self.assertGreater(os.path.getsize(path), 100)

    def test_leaflet_removed(self):
        self.assertFalse(os.path.exists(os.path.join(STATIC, "vendor", "leaflet")))

    def test_maplibre_version(self):
        self.assertIn("5.24.0", _read(os.path.join("vendor", "maplibre", "maplibre-gl.js")))


class MapViewTest(unittest.TestCase):
    def test_nav_has_map_link(self):
        self.assertIn('href="#/map"', _read("index.html"))

    def test_app_uses_openfreemap_with_compact_attribution(self):
        app = _read("app.js")
        self.assertIn("maplibregl.Map", app)
        self.assertIn("tiles.openfreemap.org/styles/liberty", app)
        self.assertIn("AttributionControl({ compact: true })", app)
        self.assertNotIn("leaflet", app.lower())
        self.assertIn('parts[0] === "map"', app)

    def test_app_has_geolocation_button(self):
        app = _read("app.js")
        self.assertIn("navigator.geolocation", app)
        self.assertIn("map-geo", app)
        # Вместо точки — стрелка, повёрнутая по heading (если браузер его отдал).
        self.assertIn("map-arrow", app)
        self.assertIn("pos.coords.heading", app)
        self.assertIn("accuracyCircle", app)
        # Кнопка «Моё место» — стрелка-навигатор, а не кружок.
        self.assertIn("GEO_ARROW_SVG", app)
        self.assertIn("geoButton.innerHTML", app)

    def test_stop_card_is_bottom_sheet(self):
        app = _read("app.js")
        self.assertIn("map-sheet", app)
        self.assertIn("openSheet(", app)
        self.assertIn("closeSheet()", app)
        self.assertIn("queryRenderedFeatures", app)
        css = _read("style.css")
        self.assertIn(".map-sheet.open", css)
        self.assertIn("translateY(100%)", css)

    def test_service_worker_cache_bumped(self):
        self.assertIn("marshrut-v7", _read("sw.js"))
