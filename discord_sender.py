"""Discord webhook sender with line-safe splitting."""
from __future__ import annotations

import json
import time
from urllib.error import HTTPError
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
    return chunks


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
        payload = {"content": chunk, "username": username}
        if avatar_url:
            payload["avatar_url"] = avatar_url
        request = Request(
            webhook_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": "cf/3.6.2"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                if response.status not in (200, 204):
                    raise DiscordSendError(f"Discord antwortete mit HTTP {response.status}")
        except HTTPError as exc:
            body = exc.read(300).decode("utf-8", "replace")
            raise DiscordSendError(f"Discord antwortete mit HTTP {exc.code}: {body}") from exc
        if index + 1 < len(chunks):
            time.sleep(0.5)

# Package revision: v3.6.2-top5-context-r1
