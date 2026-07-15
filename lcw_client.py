"""Rate-paced Live Coin Watch client for crypto-signal-monitor v3.2.7."""

from __future__ import annotations

import threading
import time
from typing import Any

import requests


class LiveCoinWatchError(RuntimeError):
    """Raised when Live Coin Watch cannot provide a valid response."""


class LiveCoinWatchClient:
    BASE_URL = "https://api.livecoinwatch.com"
    TRANSIENT_STATUS = {429, 500, 502, 503, 504}

    def __init__(
        self,
        api_key: str,
        currency: str = "USD",
        timeout: int = 25,
        request_interval_seconds: float = 1.55,
    ) -> None:
        if not api_key:
            raise ValueError("LCW_API_KEY fehlt.")
        self.currency = currency
        self.timeout = timeout
        self.request_interval_seconds = max(0.0, float(request_interval_seconds))
        self.headers = {
            "accept": "application/json",
            "content-type": "application/json",
            "cache-control": "no-cache",
            "pragma": "no-cache",
            "x-api-key": api_key,
            "user-agent": "crypto-signal-monitor/v3.2.7",
        }
        self._session = requests.Session()
        self._lock = threading.Lock()
        self._last_request_started = 0.0

    def _wait_for_slot(self) -> None:
        """Keep requests below LCW's observed short-term throttle."""
        with self._lock:
            now = time.monotonic()
            wait = self.request_interval_seconds - (now - self._last_request_started)
            if wait > 0:
                time.sleep(wait)
            self._last_request_started = time.monotonic()

    @staticmethod
    def _retry_delay(response: requests.Response | None, attempt: int) -> float:
        if response is not None:
            raw = response.headers.get("Retry-After")
            try:
                if raw is not None:
                    return min(45.0, max(3.0, float(raw)))
            except ValueError:
                pass
            if response.status_code == 429:
                return (12.0, 24.0, 36.0)[min(attempt, 2)]
        return min(12.0, 2.0 * (2**attempt))

    def _post(self, endpoint: str, payload: dict[str, Any], *, attempts: int) -> Any:
        url = f"{self.BASE_URL}{endpoint}"
        last_error: Exception | None = None
        for attempt in range(max(1, attempts)):
            response: requests.Response | None = None
            try:
                self._wait_for_slot()
                response = self._session.post(
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
                if attempt + 1 < attempts:
                    delay = self._retry_delay(response, attempt)
                    print(
                        f"LCW-Wiederholung in {delay:.0f}s: {endpoint} ({exc})",
                        flush=True,
                    )
                    time.sleep(delay)
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
            attempts=4,
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
                "start": int(start_ms),
                "end": int(end_ms),
                "meta": False,
            },
            attempts=4,
        )
        if not isinstance(data, dict) or not isinstance(data.get("history"), list):
            raise LiveCoinWatchError(f"Keine Historie für {code} erhalten.")
        history = data["history"]
        if not history:
            raise LiveCoinWatchError(f"Leere Historie für {code} erhalten.")
        return history

    def get_history_chunked(
        self,
        code: str,
        start_ms: int,
        end_ms: int,
        *,
        chunk_days: int = 100,
    ) -> tuple[list[dict[str, Any]], int]:
        """Load dense long history in chunks and merge duplicate boundaries."""
        if end_ms <= start_ms:
            raise ValueError("Ungültiger Historienzeitraum.")
        chunk_ms = max(7, int(chunk_days)) * 86_400_000
        cursor = int(start_ms)
        merged: dict[int, dict[str, Any]] = {}
        requests_used = 0
        while cursor < end_ms:
            chunk_end = min(cursor + chunk_ms, int(end_ms))
            rows = self.get_history(code, cursor, chunk_end)
            requests_used += 1
            for row in rows:
                if not isinstance(row, dict):
                    continue
                timestamp = row.get("date")
                if timestamp is None:
                    timestamp = row.get("timestamp")
                try:
                    key = int(timestamp)
                except (TypeError, ValueError):
                    continue
                merged[key] = row
            cursor = chunk_end
        return [merged[key] for key in sorted(merged)], requests_used
