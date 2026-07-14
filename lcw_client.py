"""Small no-cache LCW client with conservative retries (v3.2.6)."""

from __future__ import annotations

import time
from typing import Any

import requests


class LiveCoinWatchError(RuntimeError):
    """Raised when Live Coin Watch cannot provide a valid response."""


class LiveCoinWatchClient:
    BASE_URL = "https://api.livecoinwatch.com"
    TRANSIENT_STATUS = {429, 500, 502, 503, 504}

    def __init__(self, api_key: str, currency: str = "USD", timeout: int = 30) -> None:
        if not api_key:
            raise ValueError("LCW_API_KEY fehlt.")
        self.currency = currency
        self.timeout = timeout
        self.headers = {
            "accept": "application/json",
            "content-type": "application/json",
            "cache-control": "no-cache",
            "pragma": "no-cache",
            "x-api-key": api_key,
            "user-agent": "crypto-signal-monitor/v3.2.6-reliable",
        }

    @staticmethod
    def _retry_delay(response: requests.Response | None, attempt: int) -> float:
        if response is not None:
            raw = response.headers.get("Retry-After")
            try:
                if raw is not None:
                    return min(20.0, max(1.0, float(raw)))
            except ValueError:
                pass
        return min(8.0, 1.25 * (2**attempt))

    def _post(self, endpoint: str, payload: dict[str, Any]) -> Any:
        url = f"{self.BASE_URL}{endpoint}"
        last_error: Exception | None = None
        for attempt in range(4):
            response: requests.Response | None = None
            try:
                response = requests.post(
                    url,
                    json=payload,
                    headers=self.headers,
                    timeout=self.timeout,
                )
                if response.status_code in self.TRANSIENT_STATUS:
                    raise LiveCoinWatchError(
                        f"vorübergehender HTTP-Status {response.status_code}"
                    )
                response.raise_for_status()
                data = response.json()
                if isinstance(data, dict) and data.get("error"):
                    raise LiveCoinWatchError(str(data["error"]))
                return data
            except (requests.RequestException, ValueError, LiveCoinWatchError) as exc:
                last_error = exc
                if attempt < 3:
                    time.sleep(self._retry_delay(response, attempt))
        raise LiveCoinWatchError(f"Fehler bei {endpoint}: {last_error}")

    def get_coins(self, codes: list[str]) -> dict[str, dict[str, Any]]:
        unique_codes = list(dict.fromkeys(code.upper() for code in codes))
        if not unique_codes:
            return {}
        data = self._post(
            "/coins/map",
            {
                "codes": unique_codes,
                "currency": self.currency,
                "sort": "rank",
                "order": "ascending",
                "offset": 0,
                "limit": 0,
                "meta": False,
            },
        )
        if not isinstance(data, list):
            raise LiveCoinWatchError("Unerwartete Antwort von /coins/map.")
        result: dict[str, dict[str, Any]] = {}
        for row in data:
            if isinstance(row, dict) and row.get("code") and row.get("rate") is not None:
                result[str(row["code"]).upper()] = row
        return result

    def get_history(self, code: str, start_ms: int, end_ms: int) -> list[dict[str, Any]]:
        data = self._post(
            "/coins/single/history",
            {
                "currency": self.currency,
                "code": code.upper(),
                "start": start_ms,
                "end": end_ms,
                "meta": False,
            },
        )
        if not isinstance(data, dict) or not isinstance(data.get("history"), list):
            raise LiveCoinWatchError(f"Keine Historie für {code} erhalten.")
        history = data["history"]
        if not history:
            raise LiveCoinWatchError(f"Leere Historie für {code} erhalten.")
        return history
