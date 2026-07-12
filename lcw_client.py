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
        self.session = requests.Session()
        self.session.headers.update(
            {
                "content-type": "application/json",
                "x-api-key": api_key,
                "user-agent": "crypto-signal-monitor/1.0",
            }
        )

    def _post(self, endpoint: str, payload: dict[str, Any]) -> Any:
        url = f"{self.BASE_URL}{endpoint}"
        last_error: Exception | None = None

        for attempt in range(3):
            try:
                response = self.session.post(url, json=payload, timeout=self.timeout)
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

    def get_coin(self, code: str) -> dict[str, Any]:
        data = self._post(
            "/coins/single",
            {"currency": self.currency, "code": code.upper(), "meta": True},
        )
        if not isinstance(data, dict) or data.get("rate") is None:
            raise LiveCoinWatchError(f"Keine aktuellen Daten für {code} erhalten.")
        return data

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
