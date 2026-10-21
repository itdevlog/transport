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
    def test_nav_is_icon_bar_with_map_home(self):
        html = _read("index.html")
        self.assertIn('href="#/"', html)
        self.assertIn('href="#/favorites"', html)
        self.assertIn('data-nav="map"', html)
        self.assertIn("<svg", html)
        self.assertIn('class="nav-label"', html)
        app = _read("app.js")
        # Карта — главный раздел, избранное переехало на #/favorites.
        self.assertIn('if (parts[0] === "") return renderMap(view);', app)
        self.assertIn('if (parts[0] === "favorites") return renderFavorites(view);', app)
        self.assertIn("markNav(parts)", app)

    def test_app_uses_osm_raster_tiles_with_compact_attribution(self):
        app = _read("app.js")
        self.assertIn("maplibregl.Map", app)
        # Векторные тайлы OpenFreeMap (Cloudflare) в части сетей обрываются,
        # поэтому подложка — растровые тайлы OSM.
        self.assertIn("tile.openstreetmap.org/{z}/{x}/{y}.png", app)
        self.assertIn('type: "raster"', app)
        self.assertIn("AttributionControl({ compact: true })", app)
        self.assertNotIn("openfreemap", app.lower())
        # «i» не должна стоять вплотную к кнопке «Моё место».
        self.assertIn('"bottom-left"', app)
        self.assertNotIn("leaflet", app.lower())
        self.assertIn('parts[0] === "map"', app)

    def test_map_controls_are_touch_friendly(self):
        css = _read("style.css")
        self.assertIn(".map .maplibregl-ctrl-group button", css)
        self.assertIn("width: 44px", css)
        self.assertIn("height: 44px", css)
        self.assertIn(".map-geo", css)

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

    def test_map_layers_survive_early_load_event(self):
        app = _read("app.js")
        # "load" может пройти, пока ждём /api/stations: слушатель ставится
        # сразу после создания карты, а слои добавляются по флагу.
        self.assertIn("let styleLoaded = false;", app)
        self.assertIn('if (styleLoaded) onLoad();', app)
        self.assertIn('map.once("load", onLoad);', app)
        self.assertNotIn("map.loaded()", app)
        self.assertLess(app.index('map.on("load"'), app.index("await loadMapStops()"))

    def test_stop_card_is_bottom_sheet(self):
        app = _read("app.js")
        self.assertIn("map-sheet", app)
        self.assertIn("openSheet(", app)
        self.assertIn("closeSheet()", app)
        self.assertIn("queryRenderedFeatures", app)
        # Лист живёт поверх окна и умеет разворачиваться почти на весь экран.
        self.assertIn("applyMode(", app)
        self.assertIn("fullHeight", app)
        css = _read("style.css")
        self.assertIn(".map-sheet.open", css)
        self.assertIn("translateY(100%)", css)
        self.assertIn("position: fixed", css)
        self.assertIn(".map-sheet.full", css)
        self.assertIn("92vh", css)

    def test_sheet_close_is_contrasting(self):
        css = _read("style.css")
        start = css.index(".map-sheet-close")
        block = css[start:start + 400]
        self.assertIn("background: var(--accent)", block)
        self.assertIn("color: #fff", block)

    def test_stop_links_open_on_map(self):
        app = _read("app.js")
        self.assertIn('"#/map/"', app)
        self.assertIn("renderMap(view, focusId)", app)
        self.assertIn("/api/stop?sid=", app)
        # Страница остановки берёт настоящее название, а не id.
        self.assertIn("heading.textContent = stop.name", app)

    def test_map_fills_the_screen(self):
        app = _read("app.js")
        css = _read("style.css")
        # Карта — во весь экран между шапкой и нижним меню, без скролла страницы.
        self.assertIn('view.classList.toggle("view-map", isMap)', app)
        self.assertIn('document.body.classList.toggle("map-mode", isMap)', app)
        self.assertIn("syncMapChrome", app)
        self.assertIn("map-hint", app)
        self.assertIn("body.map-mode", css)
        self.assertIn("#view.view-map", css)
        self.assertIn("100dvh", css)
        self.assertIn("--topbar-h", css)

    def test_service_worker_cache_bumped(self):
        self.assertIn("marshrut-v14", _read("sw.js"))
