import json
import mimetypes
import os
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(BASE, "static")


def _read(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as fh:
        return fh.read()


class ManifestTest(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(STATIC, "manifest.webmanifest")
        self.assertTrue(os.path.isfile(self.path), "manifest.webmanifest missing")
        with open(self.path, encoding="utf-8") as fh:
            self.manifest = json.load(fh)

    def test_required_fields(self):
        m = self.manifest
        self.assertEqual(m["short_name"], "Маршрут")
        self.assertTrue(m["name"])
        self.assertEqual(m["start_url"], "/")
        self.assertEqual(m["display"], "standalone")
        self.assertEqual(m["theme_color"], "#c8102e")
        self.assertEqual(m["lang"], "ru")
        self.assertIn("background_color", m)

    def test_icons_192_and_512_maskable(self):
        sizes = {i["sizes"] for i in self.manifest["icons"]}
        self.assertIn("192x192", sizes)
        self.assertIn("512x512", sizes)
        self.assertTrue(
            any("maskable" in i.get("purpose", "") for i in self.manifest["icons"])
        )

    def test_icon_files_exist(self):
        for icon in self.manifest["icons"]:
            path = os.path.join(STATIC, os.path.basename(icon["src"]))
            self.assertTrue(os.path.isfile(path), f"missing icon {icon['src']}")

    def test_webmanifest_mime_type(self):
        import server  # noqa: F401  (side effect: registers the mime type)

        self.assertEqual(
            mimetypes.guess_type("manifest.webmanifest")[0],
            "application/manifest+json",
        )


class ServiceWorkerTest(unittest.TestCase):
    def test_sw_exists_nonempty(self):
        path = os.path.join(STATIC, "sw.js")
        self.assertTrue(os.path.isfile(path), "sw.js missing")
        self.assertGreater(os.path.getsize(path), 0)

    def test_sw_leaves_api_requests_to_network(self):
        self.assertIn("/api/", _read("sw.js"))

    def test_index_links_manifest_and_theme(self):
        html = _read("index.html")
        self.assertIn('rel="manifest"', html)
        self.assertIn('name="theme-color"', html)
        self.assertIn("apple-touch-icon", html)

    def test_app_registers_service_worker(self):
        js = _read("app.js")
        self.assertIn("serviceWorker", js)
        self.assertIn("register", js)


class ApplePwaTest(unittest.TestCase):
    def test_apple_touch_icon_is_png(self):
        path = os.path.join(STATIC, "apple-touch-icon.png")
        self.assertTrue(os.path.isfile(path), "apple-touch-icon.png missing")
        with open(path, "rb") as fh:
            self.assertEqual(fh.read(8), b"\x89PNG\r\n\x1a\n")

    def test_png_icons_have_expected_sizes(self):
        import struct

        for name, size in (("icon-192.png", 192), ("icon-512.png", 512)):
            with open(os.path.join(STATIC, name), "rb") as fh:
                head = fh.read(24)
            width, height = struct.unpack(">II", head[16:24])
            self.assertEqual((width, height), (size, size))

    def test_index_has_apple_meta_and_png_icon(self):
        html = _read("index.html")
        self.assertIn("apple-mobile-web-app-capable", html)
        self.assertIn("apple-mobile-web-app-title", html)
        # Современный аналог: без него Chrome ругается на deprecated-мету.
        self.assertIn("mobile-web-app-capable", html)
        self.assertIn("apple-touch-icon.png", html)

    def test_manifest_includes_png_icons(self):
        with open(os.path.join(STATIC, "manifest.webmanifest"), encoding="utf-8") as fh:
            m = json.load(fh)
        pngs = {i["sizes"] for i in m["icons"] if i["type"] == "image/png"}
        self.assertIn("192x192", pngs)
        self.assertIn("512x512", pngs)


if __name__ == "__main__":
    unittest.main()
