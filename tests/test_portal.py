import json
import ssl
import unittest
from unittest import mock

from portal import PortalClient, PortalError

CONFIG = {
    "portalUrl": "https://example.test/php/apiRequest.php",
    "token": "11111111-50b3-4fec-b922-8a50a1d38366",
    "reg": 66002,
    "wuid": "marshrut-web",
    "timeout": 10,
}


class BuildBodyTest(unittest.TestCase):
    def test_builds_portal_envelope(self):
        client = PortalClient(CONFIG)
        body = client._build_body("getRoutes", {})
        self.assertEqual(body["t"], CONFIG["token"])
        self.assertEqual(body["ct"], 26)
        self.assertEqual(body["cd"], "getRoutes.php")
        self.assertEqual(body["reg"], 66002)
        self.assertEqual(body["w"], -1)
        self.assertEqual(body["data"], {"wuid": "marshrut-web"})

    def test_merges_command_params_into_data(self):
        client = PortalClient(CONFIG)
        body = client._build_body("getRouteById", {"id": 1554})
        self.assertEqual(body["data"], {"wuid": "marshrut-web", "id": 1554})


class RequestTest(unittest.TestCase):
    def _fake_response(self, payload: dict):
        resp = mock.MagicMock()
        resp.read.return_value = json.dumps(payload).encode("utf-8")
        resp.__enter__ = mock.Mock(return_value=resp)
        resp.__exit__ = mock.Mock(return_value=False)
        return resp

    def test_returns_data_on_ok(self):
        client = PortalClient(CONFIG)
        with mock.patch("portal.urllib.request.urlopen",
                        return_value=self._fake_response({"r": "ok", "data": [1, 2]})) as uo:
            self.assertEqual(client._request("getRoutes", {}), [1, 2])
        self.assertEqual(uo.call_args[0][0].get_method(), "POST")

    def test_raises_portal_error_on_fail(self):
        client = PortalClient(CONFIG)
        with mock.patch("portal.urllib.request.urlopen",
                        return_value=self._fake_response(
                            {"r": "fail", "reason": "Некорректный токен", "message": "bad"})):
            with self.assertRaises(PortalError) as ctx:
                client._request("getRoutes", {})
        self.assertEqual(ctx.exception.reason, "Некорректный токен")

    def test_raises_portal_error_on_bad_json(self):
        client = PortalClient(CONFIG)
        resp = mock.MagicMock()
        resp.read.return_value = b"<html>oops</html>"
        resp.__enter__ = mock.Mock(return_value=resp)
        resp.__exit__ = mock.Mock(return_value=False)
        with mock.patch("portal.urllib.request.urlopen", return_value=resp):
            with self.assertRaises(PortalError):
                client._request("getRoutes", {})

    def test_raises_portal_error_on_network_failure(self):
        client = PortalClient(CONFIG)
        with mock.patch("portal.urllib.request.urlopen",
                        side_effect=OSError("connection refused")):
            with self.assertRaises(PortalError):
                client._request("getRoutes", {})

    def test_disables_ssl_verification(self):
        client = PortalClient(CONFIG)
        with mock.patch("portal.urllib.request.urlopen",
                        return_value=self._fake_response({"r": "ok", "data": None})) as uo:
            client._request("getRoutes", {})
        ctx = uo.call_args.kwargs["context"]
        self.assertEqual(ctx.verify_mode, ssl.CERT_NONE)
        self.assertFalse(ctx.check_hostname)


if __name__ == "__main__":
    unittest.main()
