"""Клиент транспортного портала Екатеринбурга (только стандартная библиотека)."""

import json
import ssl
import threading
import time
import urllib.error
import urllib.request

DEFAULT_WUID = "marshrut-web"


class CachedValue:
    """Кэш одного значения с TTL и защитой от параллельного дублирования."""

    def __init__(self, ttl, clock=time.monotonic):
        self._ttl = ttl
        self._clock = clock
        self._cache = {}
        self._key_locks = {}
        self._lock = threading.Lock()

    def get(self, key, producer):
        with self._lock:
            entry = self._cache.get(key)
            if entry is not None and entry[1] > self._clock():
                return entry[0]
            key_lock = self._key_locks.setdefault(key, threading.Lock())
        with key_lock:  # single-flight per key
            with self._lock:
                entry = self._cache.get(key)
                if entry is not None and entry[1] > self._clock():
                    return entry[0]
            value = producer()
            with self._lock:
                self._cache[key] = (value, self._clock() + self._ttl)
            return value


class PortalError(Exception):
    """Доменная ошибка обращения к порталу (сеть, таймаут, ответ ``r:"fail"``)."""

    def __init__(self, reason, message=""):
        super().__init__(f"{reason}: {message}" if message else str(reason))
        self.reason = reason
        self.message = message


class PortalClient:
    """Тонкая обёртка над ``apiRequest.php``."""

    def __init__(self, config):
        self._portal_url = config["portalUrl"]
        self._token = config["token"]
        self._reg = config["reg"]
        self._wuid = config.get("wuid", DEFAULT_WUID)
        self._timeout = config.get("timeout", 10)
        self._ssl_context = ssl.create_default_context()
        self._ssl_context.check_hostname = False
        self._ssl_context.verify_mode = ssl.CERT_NONE

        ttl = config.get("cacheTtl", {})
        self._routes_cache = CachedValue(ttl.get("routes", 21600))
        self._route_cache = CachedValue(ttl.get("route", 21600))
        self._stations_cache = CachedValue(ttl.get("stations", 21600))
        self._forecast_cache = CachedValue(ttl.get("forecasts", 10))

    def get_routes(self):
        return self._routes_cache.get("all", lambda: self._request("getRoutes", {})) or []

    def get_route(self, route_id):
        return self._route_cache.get(
            route_id, lambda: self._request("getRouteById", {"id": route_id})
        ) or {}

    def get_stations(self):
        return self._stations_cache.get("all", lambda: self._request("getStations", {})) or []

    def get_forecasts(self, sid):
        return self._forecast_cache.get(
            sid, lambda: self._request("getStationForecasts", {"sid": sid})
        ) or []

    def _build_body(self, cmd, data):
        return {
            "t": self._token,
            "ct": 26,
            "cd": f"{cmd}.php",
            "reg": self._reg,
            "w": -1,
            "data": {"wuid": self._wuid, **data},
        }

    def _request(self, cmd, data):
        body = json.dumps(self._build_body(cmd, data)).encode("utf-8")
        url = f"{self._portal_url}?{cmd}.php"
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        try:
            with urllib.request.urlopen(
                req, context=self._ssl_context, timeout=self._timeout
            ) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise PortalError("network", str(exc)) from exc

        if not isinstance(payload, dict):
            raise PortalError("portal", "invalid payload")
        if payload.get("r") != "ok":
            raise PortalError(
                payload.get("reason") or "portal",
                payload.get("message") or "",
            )
        return payload.get("data")
