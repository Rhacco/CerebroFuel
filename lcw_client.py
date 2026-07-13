"""Small client for the official Live Coin Watch API."""

from __future__ import annotations

import time
from typing import Any

import requests


class LiveCoinWatchError(RuntimeError):
    """Raised when Live Coin Watch cannot provide a valid response."""


class LiveCoinWatchClient:
    BASE_URL = "https://api.livecoinwatch.com"

    def __init__(self, api_key: str, currency: str = "USD", timeout: int = 30) -> None:
        if not api_key:
            raise ValueError("LCW_API_KEY fehlt.")
        self.currency = currency
        self.timeout = timeout
        self.headers = {
            "accept": "application/json",
            "content-type": "application/json",
            "x-api-key": api_key,
            "user-agent": "crypto-signal-monitor/2.0",
        }

    def _post(self, endpoint: str, payload: dict[str, Any]) -> Any:
        url = f"{self.BASE_URL}{endpoint}"
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = requests.post(
                    url,
                    json=payload,
                    headers=self.headers,
                    timeout=self.timeout,
                )
                response.raise_for_status()
                data = response.json()
                if isinstance(data, dict) and data.get("error"):
                    raise LiveCoinWatchError(str(data["error"]))
                return data
            except (requests.RequestException, ValueError, LiveCoinWatchError) as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(2**attempt)
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
        return data["history"]
