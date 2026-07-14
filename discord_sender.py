"""Discord webhook sender with line-safe splitting (v3.2.5 quality refresh)."""

from __future__ import annotations

import time

import requests


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


def send_discord(webhook_url: str, content: str, username: str, timeout: int = 30) -> None:
    if not webhook_url:
        raise ValueError("DISCORD_WEBHOOK_URL fehlt.")
    chunks = split_report(content)
    for index, chunk in enumerate(chunks):
        response = requests.post(
            webhook_url,
            json={"content": chunk, "username": username},
            timeout=timeout,
        )
        if response.status_code not in (200, 204):
            raise DiscordSendError(
                f"Discord antwortete mit HTTP {response.status_code}: {response.text[:300]}"
            )
        if index + 1 < len(chunks):
            time.sleep(0.5)

