"""Discord webhook sender with managed-message cleanup for CF v3.9.3."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen


STATE_VERSION = "discord-messages-v393-r1"


class DiscordSendError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


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


def _endpoint(
    webhook_url: str,
    *,
    message_id: str | None = None,
    wait: bool = False,
) -> str:
    parts = urlsplit(webhook_url.strip())
    if parts.scheme != "https" or not parts.netloc or not parts.path:
        raise ValueError("Discord-Webhook-URL muss eine vollstaendige HTTPS-URL sein.")
    path = parts.path.rstrip("/")
    if message_id is not None:
        path += "/messages/" + quote(str(message_id), safe="")
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() != "wait"
    ]
    if wait:
        query.append(("wait", "true"))
    return urlunsplit((parts.scheme, parts.netloc, path, urlencode(query), ""))


def _webhook_key(webhook_url: str) -> str:
    normalized = _endpoint(webhook_url)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _load_ids(path: Path, webhook_key: str) -> list[str]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError, TypeError, json.JSONDecodeError):
        return []
    if (
        not isinstance(raw, Mapping)
        or raw.get("version") != STATE_VERSION
        or raw.get("webhook_key") != webhook_key
    ):
        return []
    ids = raw.get("message_ids")
    if not isinstance(ids, list):
        return []
    return [
        str(value)
        for value in ids
        if str(value).isdigit() and len(str(value)) <= 32
    ]


def _save_ids(path: Path, webhook_key: str, message_ids: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(
            {
                "version": STATE_VERSION,
                "webhook_key": webhook_key,
                "message_ids": list(dict.fromkeys(message_ids)),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    temporary.replace(path)


def _request_json(
    method: str,
    url: str,
    payload: Mapping[str, Any] | None,
    *,
    timeout: int,
    expected: set[int],
) -> Mapping[str, Any]:
    request = Request(
        url,
        data=(
            json.dumps(dict(payload), ensure_ascii=False).encode("utf-8")
            if payload is not None
            else None
        ),
        headers={
            "Content-Type": "application/json",
            "User-Agent": "cf/3.9.3",
        },
        method=method,
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            status = int(response.status)
            body = response.read()
    except HTTPError as exc:
        body = exc.read(300).decode("utf-8", "replace")
        raise DiscordSendError(
            f"Discord antwortete mit HTTP {exc.code}: {body}",
            status=exc.code,
        ) from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise DiscordSendError(f"Discord-Aufruf fehlgeschlagen: {exc}") from exc
    if status not in expected:
        raise DiscordSendError(
            f"Discord antwortete unerwartet mit HTTP {status}",
            status=status,
        )
    if not body:
        return {}
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise DiscordSendError("Discord-Antwort war kein gueltiges JSON") from exc
    if not isinstance(parsed, Mapping):
        raise DiscordSendError("Discord-Antwort war kein JSON-Objekt")
    return parsed


def _create_message(
    webhook_url: str,
    content: str,
    username: str,
    avatar_url: str,
    timeout: int,
) -> str:
    payload: dict[str, Any] = {
        "content": content,
        "username": username,
        "allowed_mentions": {"parse": []},
    }
    if avatar_url:
        payload["avatar_url"] = avatar_url
    result = _request_json(
        "POST",
        _endpoint(webhook_url, wait=True),
        payload,
        timeout=timeout,
        expected={200},
    )
    message_id = str(result.get("id") or "")
    if not message_id.isdigit():
        raise DiscordSendError("Discord lieferte keine gueltige Nachrichten-ID")
    return message_id


def _edit_message(
    webhook_url: str,
    message_id: str,
    content: str,
    timeout: int,
) -> bool:
    try:
        _request_json(
            "PATCH",
            _endpoint(webhook_url, message_id=message_id),
            {"content": content, "allowed_mentions": {"parse": []}},
            timeout=timeout,
            expected={200},
        )
    except DiscordSendError as exc:
        if exc.status == 404:
            return False
        raise
    return True


def _delete_message(webhook_url: str, message_id: str, timeout: int) -> None:
    try:
        _request_json(
            "DELETE",
            _endpoint(webhook_url, message_id=message_id),
            None,
            timeout=timeout,
            expected={200, 204},
        )
    except DiscordSendError as exc:
        if exc.status != 404:
            raise


def send_discord(
    webhook_url: str,
    content: str,
    username: str,
    avatar_url: str = "",
    timeout: int = 30,
    state_path: Path | None = None,
) -> list[str]:
    """Send a report and, with state, keep only its current webhook messages.

    Existing managed messages are edited in place.  Extra chunks from an older
    report are deleted only after every current chunk has been accepted.  IDs
    created by earlier versions cannot be discovered safely and are therefore
    never guessed or deleted.
    """
    if not webhook_url:
        raise ValueError("DISCORD_WEBHOOK_URL fehlt.")
    chunks = split_report(content)
    if state_path is None:
        created: list[str] = []
        for index, chunk in enumerate(chunks):
            created.append(
                _create_message(webhook_url, chunk, username, avatar_url, timeout)
            )
            if index + 1 < len(chunks):
                time.sleep(0.35)
        return created

    webhook_key = _webhook_key(webhook_url)
    old_ids = _load_ids(state_path, webhook_key)
    current_ids: list[str] = []
    for index, chunk in enumerate(chunks):
        message_id = old_ids[index] if index < len(old_ids) else ""
        if not message_id or not _edit_message(
            webhook_url,
            message_id,
            chunk,
            timeout,
        ):
            message_id = _create_message(
                webhook_url,
                chunk,
                username,
                avatar_url,
                timeout,
            )
        current_ids.append(message_id)
        # Persist each newly created ID before any obsolete message is removed.
        untouched = old_ids[index + 1 :]
        _save_ids(state_path, webhook_key, current_ids + untouched)
        if index + 1 < len(chunks):
            time.sleep(0.35)

    obsolete = [message_id for message_id in old_ids if message_id not in current_ids]
    for index, message_id in enumerate(obsolete):
        remaining = obsolete[index:]
        try:
            _delete_message(webhook_url, message_id, timeout)
        except DiscordSendError:
            _save_ids(state_path, webhook_key, current_ids + remaining)
            raise
        _save_ids(state_path, webhook_key, current_ids + obsolete[index + 1 :])
        if index + 1 < len(obsolete):
            time.sleep(0.35)

    _save_ids(state_path, webhook_key, current_ids)
    return current_ids


# Package revision: v3.9.3-lighter-top-pool-r1
