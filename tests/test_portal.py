import json
import ssl
import threading
import time as _time
import unittest
from unittest import mock

from portal import CachedValue, PortalClient, PortalError

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


class CachedValueTest(unittest.TestCase):
    def test_caches_within_ttl(self):
        now = [100.0]
        cache = CachedValue(ttl=10, clock=lambda: now[0])
        calls = []
        val = lambda: calls.append(1) or "v"
        self.assertEqual(cache.get("k", val), "v")
        self.assertEqual(cache.get("k", val), "v")
        self.assertEqual(len(calls), 1)

    def test_refetches_after_ttl(self):
        now = [100.0]
        cache = CachedValue(ttl=10, clock=lambda: now[0])
        calls = []
        val = lambda: calls.append(1) or "v"
        cache.get("k", val)
        now[0] = 111.0
        cache.get("k", val)
        self.assertEqual(len(calls), 2)

    def test_single_flight(self):
        cache = CachedValue(ttl=10)
        calls = []
        started = threading.Barrier(2)
        def producer():
            calls.append(1)
            _time.sleep(0.2)
            return "v"
        results = []
        def worker():
            started.wait()
            results.append(cache.get("k", producer))
        threads = [threading.Thread(target=worker) for _ in range(2)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(results, ["v", "v"])
        self.assertEqual(len(calls), 1)


class PortalClientMethodsTest(unittest.TestCase):
    def make(self):
        cfg = dict(CONFIG, cacheTtl={"routes": 21600, "route": 21600,
                                     "stations": 21600, "forecasts": 10})
        return PortalClient(cfg)

    def test_get_routes_uses_command_and_caches(self):
        client = self.make()
        client._request = mock.Mock(return_value=[{"id": 1554}])
        client.get_routes(); client.get_routes()
        client._request.assert_called_once_with("getRoutes", {})

    def test_get_route_passes_id(self):
        client = self.make()
        client._request = mock.Mock(return_value={"id": 1554, "subroutes": []})
        self.assertEqual(client.get_route(1554)["id"], 1554)
        client._request.assert_called_once_with("getRouteById", {"id": 1554})

    def test_get_stations_uses_command(self):
        client = self.make()
        client._request = mock.Mock(return_value=[{"id": 5830}])
        client.get_stations()
        client._request.assert_called_once_with("getStations", {})

    def test_get_forecasts_passes_sid_and_caches(self):
        client = self.make()
        client._request = mock.Mock(return_value=[{"arrivalTimeInSec": 575}])
        client.get_forecasts(5830); client.get_forecasts(5830)
        client._request.assert_called_once_with("getStationForecasts", {"sid": 5830})

    def test_get_station_routes_passes_sid_and_caches(self):
        client = self.make()
        client._request = mock.Mock(return_value=[{"routeTypeId": 1}])
        client.get_station_routes(5830); client.get_station_routes(5830)
        client._request.assert_called_once_with("getStationRoutes", {"sid": 5830})

    def test_get_station_routes_empty_returns_list(self):
        client = self.make()
        client._request = mock.Mock(return_value=None)
        self.assertEqual(client.get_station_routes(5830), [])

    def test_get_vehicle_passes_device_code_and_caches(self):
        client = self.make()
        client._request = mock.Mock(return_value={"deviceCode": "862059066943146",
                                                  "vehicle": {"id": 1}})
        client.get_vehicle("862059066943146"); client.get_vehicle("862059066943146")
        client._request.assert_called_once_with("getVehicleInfo",
                                                {"deviceCode": "862059066943146"})

    def test_get_vehicle_forecasts_passes_device_code_and_caches(self):
        client = self.make()
        client._request = mock.Mock(return_value=[{"arrt": 99}])
        client.get_vehicle_forecasts("862059066943146"); client.get_vehicle_forecasts("862059066943146")
        client._request.assert_called_once_with("getVehicleForecasts",
                                                {"deviceCode": "862059066943146"})

    def test_empty_vehicle_returns_empty_dict(self):
        client = self.make()
        client._request = mock.Mock(return_value=None)
        self.assertEqual(client.get_vehicle("X"), {})

    def test_empty_vehicle_forecasts_returns_list(self):
        client = self.make()
        client._request = mock.Mock(return_value=None)
        self.assertEqual(client.get_vehicle_forecasts("X"), [])

    def test_empty_portal_data_returns_empty_list(self):
        client = self.make()
        client._request = mock.Mock(return_value=None)
        self.assertEqual(client.get_forecasts(5830), [])
        self.assertEqual(client.get_routes(), [])

    def test_empty_route_returns_empty_dict(self):
        client = self.make()
        client._request = mock.Mock(return_value=None)
        self.assertEqual(client.get_route(1554), {})


if __name__ == "__main__":
    unittest.main()
