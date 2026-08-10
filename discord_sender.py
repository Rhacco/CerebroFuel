# r2
"""Discord webhook sender: every call creates a new message."""
from __future__ import annotations

import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class DiscordSendError(RuntimeError):
    pass


def split_report(content: str, limit: int = 2000) -> list[str]:
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    chunks: list[str] = []
    current: list[str] = []
    current_length = 0
    for line in lines:
        if len(line) > limit:
            raise ValueError(f"Eine Discord-Zeile ist zu lang: {len(line)} Zeichen.")
        added = len(line) if not current else len(line) + 1
        if current and current_length + added > limit:
            chunks.append("\n".join(current))
            current = [line]
            current_length = len(line)
        else:
            current.append(line)
            current_length += added
    if current:
        chunks.append("\n".join(current))
    if not chunks:
        raise ValueError("Discord-Bericht ist leer.")
    return chunks


def _retry_after_seconds(exc: HTTPError, body: str, fallback: float) -> float:
    header = exc.headers.get("Retry-After") if exc.headers is not None else None
    try:
        if header is not None:
            return max(0.0, min(120.0, float(header)))
    except (TypeError, ValueError):
        pass
    try:
        payload = json.loads(body)
        value = payload.get("retry_after") if isinstance(payload, dict) else None
        if value is not None:
            return max(0.0, min(120.0, float(value)))
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    return fallback


def send_discord(
    webhook_url: str,
    content: str,
    username: str,
    avatar_url: str = "",
    timeout: int = 30,
) -> None:
    if not webhook_url:
        raise ValueError("DISCORD_WEBHOOK_URL fehlt.")
    chunks = split_report(content)
    for index, chunk in enumerate(chunks):
        payload = {
            "content": chunk,
            "username": username,
            "allowed_mentions": {"parse": []},
        }
        if avatar_url:
            payload["avatar_url"] = avatar_url
        data = json.dumps(payload).encode("utf-8")
        last_error = ""
        for attempt in range(1, 4):
            request = Request(
                webhook_url,
                data=data,
                headers={"Content-Type": "application/json", "User-Agent": "cf/7.1.0"},
                method="POST",
            )
            try:
                with urlopen(request, timeout=timeout) as response:
                    if response.status not in (200, 204):
                        raise DiscordSendError(
                            f"Discord antwortete mit HTTP {response.status}"
                        )
                last_error = ""
                break
            except HTTPError as exc:
                body = exc.read(300).decode("utf-8", "replace")
                last_error = f"Discord antwortete mit HTTP {exc.code}: {body}"
                if exc.code == 429 and attempt < 3:
                    time.sleep(_retry_after_seconds(exc, body, float(attempt)))
                    continue
                if 500 <= exc.code <= 599 and attempt < 3:
                    time.sleep(float(attempt))
                    continue
                raise DiscordSendError(last_error) from exc
            except (URLError, TimeoutError, OSError) as exc:
                reason = getattr(exc, "reason", exc)
                last_error = f"Discord-Verbindung fehlgeschlagen: {reason}"
                if attempt < 3:
                    time.sleep(float(attempt))
                    continue
                raise DiscordSendError(last_error) from exc
        if last_error:
            raise DiscordSendError(last_error)
        if index + 1 < len(chunks):
            time.sleep(0.5)
