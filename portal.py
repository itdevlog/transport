"""Клиент транспортного портала Екатеринбурга (только стандартная библиотека)."""

import json
import ssl
import time
import urllib.error
import urllib.request

DEFAULT_WUID = "marshrut-web"


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
