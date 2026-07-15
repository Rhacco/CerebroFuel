"""Rate-aware Live Coin Watch client for crypto-signal-monitor v3.2.7."""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from pathlib import Path
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
        request_interval_seconds: float = 0.45,
        burst_limit: int = 30,
        burst_window_seconds: float = 60.0,
        rate_state_path: str | Path | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("LCW_API_KEY fehlt.")
        self.currency = currency
        self.timeout = timeout
        self.request_interval_seconds = max(0.0, float(request_interval_seconds))
        self.burst_limit = max(1, int(burst_limit))
        self.burst_window_seconds = max(5.0, float(burst_window_seconds))
        self.rate_state_path = Path(rate_state_path) if rate_state_path else None
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
        self._request_starts: deque[float] = deque()
        self._last_request_started = 0.0
        self._cooldown_until = 0.0
        self.requests_started = 0
        self.requests_succeeded = 0
        self._load_rate_state()

    def _load_rate_state(self) -> None:
        if self.rate_state_path is None:
            return
        try:
            raw = json.loads(self.rate_state_path.read_text(encoding="utf-8"))
            now = time.time()
            timestamps = [
                float(value)
                for value in raw.get("request_starts", [])
                if now - float(value) < self.burst_window_seconds
            ]
            self._request_starts.extend(sorted(timestamps))
            self._last_request_started = float(raw.get("last_request_started", 0.0))
            self._cooldown_until = float(raw.get("cooldown_until", 0.0))
        except (FileNotFoundError, OSError, ValueError, TypeError, json.JSONDecodeError):
            return

    def _save_rate_state(self) -> None:
        if self.rate_state_path is None:
            return
        try:
            self.rate_state_path.parent.mkdir(parents=True, exist_ok=True)
            now = time.time()
            cutoff = now - self.burst_window_seconds
            valid = [value for value in self._request_starts if value >= cutoff]
            temporary = self.rate_state_path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(
                    {
                        "request_starts": valid,
                        "last_request_started": self._last_request_started,
                        "cooldown_until": self._cooldown_until,
                    }
                ),
                encoding="utf-8",
            )
            temporary.replace(self.rate_state_path)
        except OSError:
            pass

    def _wait_for_slot(self) -> None:
        """Allow short normal runs to finish quickly while preventing request bursts."""
        with self._lock:
            while True:
                now = time.time()
                while self._request_starts and now - self._request_starts[0] >= self.burst_window_seconds:
                    self._request_starts.popleft()

                waits = [0.0]
                if self._cooldown_until > now:
                    waits.append(self._cooldown_until - now)
                if self._last_request_started > 0:
                    waits.append(self.request_interval_seconds - (now - self._last_request_started))
                if len(self._request_starts) >= self.burst_limit:
                    waits.append(
                        self.burst_window_seconds - (now - self._request_starts[0]) + 0.15
                    )
                wait = max(waits)
                if wait <= 0:
                    started = time.time()
                    self._last_request_started = started
                    self._request_starts.append(started)
                    self.requests_started += 1
                    self._save_rate_state()
                    return
                time.sleep(min(wait, 5.0))

    def _set_cooldown(self, seconds: float) -> None:
        with self._lock:
            self._cooldown_until = max(self._cooldown_until, time.time() + max(0.0, seconds))
            self._save_rate_state()

    @staticmethod
    def _retry_delay(response: requests.Response | None, attempt: int) -> float:
        if response is not None:
            raw = response.headers.get("Retry-After")
            try:
                if raw is not None:
                    return min(75.0, max(5.0, float(raw)))
            except ValueError:
                pass
            if response.status_code == 429:
                return (18.0, 30.0, 45.0, 60.0)[min(attempt, 3)]
        return min(20.0, 2.5 * (2**attempt))

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
                self.requests_succeeded += 1
                return data
            except (requests.RequestException, ValueError, LiveCoinWatchError) as exc:
                last_error = exc
                if attempt + 1 < attempts:
                    delay = self._retry_delay(response, attempt)
                    if response is not None and response.status_code == 429:
                        self._set_cooldown(delay)
                        print(
                            f"LCW-Drosselung erkannt; automatische Pause {delay:.0f}s.",
                            flush=True,
                        )
                    else:
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
            attempts=5,
        )
        if not isinstance(data, list):
            raise LiveCoinWatchError("Unerwartete Antwort von /coins/map.")
        result: dict[str, dict[str, Any]] = {}
        for row in data:
            if isinstance(row, dict) and row.get("code") and row.get("rate") is not None:
                result[str(row["code"]).upper()] = row
        return result

    def get_history(
        self,
        code: str,
        start_ms: int,
        end_ms: int,
        *,
        allow_empty: bool = False,
    ) -> list[dict[str, Any]]:
        data = self._post(
            "/coins/single/history",
            {
                "currency": self.currency,
                "code": code.upper(),
                "start": int(start_ms),
                "end": int(end_ms),
                "meta": False,
            },
            attempts=5,
        )
        if not isinstance(data, dict) or not isinstance(data.get("history"), list):
            raise LiveCoinWatchError(f"Keine Historie für {code} erhalten.")
        history = data["history"]
        if not history and not allow_empty:
            raise LiveCoinWatchError(f"Leere Historie für {code} erhalten.")
        return history

    def get_history_chunked(
        self,
        code: str,
        start_ms: int,
        end_ms: int,
        *,
        chunk_days: int = 100,
    ) -> tuple[list[dict[str, Any]], int, str | None]:
        """Load newest chunks first and use all available partial history.

        Empty older chunks are normal for recently listed coins and are silently
        ignored. If a later chunk fails after usable data was already received,
        that partial history is returned instead of discarding the coin.
        """
        if end_ms <= start_ms:
            raise ValueError("Ungültiger Historienzeitraum.")
        chunk_ms = max(14, int(chunk_days)) * 86_400_000
        cursor_end = int(end_ms)
        merged: dict[int, dict[str, Any]] = {}
        requests_used = 0
        partial_note: str | None = None
        found_data = False

        while cursor_end > start_ms:
            cursor_start = max(int(start_ms), cursor_end - chunk_ms)
            try:
                rows = self.get_history(
                    code,
                    cursor_start,
                    cursor_end,
                    allow_empty=True,
                )
                requests_used += 1
            except Exception as exc:
                if merged:
                    partial_note = f"älterer Teil nicht geladen: {exc}"
                    break
                raise

            if not rows:
                if found_data:
                    # We reached the time before the coin existed on LCW.
                    break
                # No data even in the newest requested block: valid empty result.
                return [], requests_used, None

            found_data = True
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
            cursor_end = cursor_start

        return [merged[key] for key in sorted(merged)], requests_used, partial_note
