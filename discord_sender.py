"""Discord webhook sender."""

from __future__ import annotations

import requests


class DiscordSendError(RuntimeError):
    pass


def send_discord(webhook_url: str, content: str, username: str, timeout: int = 30) -> None:
    if not webhook_url:
        raise ValueError("DISCORD_WEBHOOK_URL fehlt.")
    if len(content) > 2000:
        raise ValueError(f"Discord-Nachricht ist zu lang: {len(content)} Zeichen.")

    response = requests.post(
        webhook_url,
        json={"content": content, "username": username},
        timeout=timeout,
    )
    if response.status_code not in (200, 204):
        raise DiscordSendError(
            f"Discord antwortete mit HTTP {response.status_code}: {response.text[:300]}"
        )
