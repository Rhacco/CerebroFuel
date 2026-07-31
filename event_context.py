"""Verified critical-event context for CF v3.8.1.

Automatic facts come only from official public schedules/status pages. Project-
specific events such as token unlocks are accepted only from a local or remote
JSON feed that explicitly marks the item verified and supplies an allowed HTTPS
source URL. Events never create a Long/Short direction; they only add visibility
and neutral risk controls.
"""
from __future__ import annotations

import json
import math
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

CACHE_VERSION = "event-cache-v381-r1"
USER_AGENT = "crypto-signal-monitor/3.8.1"
MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}
KIND_CODES = {
    "FOMC": "FED",
    "CPI": "CPI",
    "NFP": "NFP",
    "PPI": "PPI",
    "GDP": "GDP",
    "PCE": "PCE",
    "EXPIRY": "EXP",
    "ETF": "ETF",
    "UNLOCK": "U",
    "UPGRADE": "UPG",
    "GOVERNANCE": "GOV",
    "SUPPLY": "SUP",
    "NEWS": "N",
    "NETWORK": "NET",
}
DEFAULT_PRIORITIES = {
    "NETWORK": 100,
    "FOMC": 100,
    "CPI": 96,
    "NFP": 96,
    "UNLOCK": 94,
    "ETF": 92,
    "SUPPLY": 90,
    "UPGRADE": 88,
    "GOVERNANCE": 86,
    "PCE": 84,
    "GDP": 82,
    "EXPIRY": 80,
    "PPI": 72,
    "NEWS": 70,
}


@dataclass(frozen=True)
class CriticalEvent:
    symbol: str
    kind: str
    title: str
    starts_at: str | None
    ends_at: str | None
    exact_time: bool
    priority: int
    source_name: str
    source_url: str
    active: bool = False


@dataclass(frozen=True)
class EventMark:
    symbol: str
    code: str
    kind: str
    title: str
    starts_at: str | None
    ends_at: str | None
    priority: int
    risk: int
    active: bool
    block_new: bool
    leverage_cap: int | None
    source_name: str
    source_url: str


@dataclass
class EventSnapshot:
    marks: dict[str, EventMark]
    events: list[CriticalEvent]
    diagnostics: list[str]
    generated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "marks": {symbol: asdict(mark) for symbol, mark in self.marks.items()},
            "events": [asdict(event) for event in self.events],
            "diagnostics": list(self.diagnostics),
        }


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data and data.strip():
            self.parts.append(data.strip())

    def text(self) -> str:
        return " ".join(self.parts)


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value else None


def _request_text(url: str, *, timeout: float, retries: int = 2) -> str:
    request = Request(
        url,
        headers={
            "Accept": "application/json,text/calendar,text/html;q=0.9,*/*;q=0.8",
            "User-Agent": USER_AGENT,
        },
    )
    last: Exception | None = None
    for attempt in range(max(1, retries)):
        try:
            with urlopen(request, timeout=timeout) as response:
                raw = response.read()
            return raw.decode("utf-8", errors="replace")
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            last = exc
            if attempt + 1 < retries:
                time.sleep(0.35 * (attempt + 1))
    raise RuntimeError(f"Abruf fehlgeschlagen: {url}: {last}")


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _save_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def _event_from_dict(raw: Mapping[str, Any]) -> CriticalEvent | None:
    symbol = str(raw.get("symbol") or "").upper().strip()
    kind = str(raw.get("kind") or raw.get("type") or "").upper().strip()
    title = str(raw.get("title") or kind).strip()
    source_url = str(raw.get("source_url") or "").strip()
    source_name = str(raw.get("source_name") or urlparse(source_url).netloc or "Quelle").strip()
    if not symbol or kind not in KIND_CODES or not title or not source_url:
        return None
    return CriticalEvent(
        symbol=symbol,
        kind=kind,
        title=title,
        starts_at=_iso(_parse_iso(raw.get("starts_at"))),
        ends_at=_iso(_parse_iso(raw.get("ends_at"))),
        exact_time=bool(raw.get("exact_time", True)),
        priority=max(1, min(100, int(raw.get("priority", DEFAULT_PRIORITIES.get(kind, 70))))),
        source_name=source_name,
        source_url=source_url,
        active=bool(raw.get("active", False)),
    )


def _unfold_ical(text: str) -> list[str]:
    rows: list[str] = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line.startswith((" ", "\t")) and rows:
            rows[-1] += line[1:]
        else:
            rows.append(line)
    return rows


def _ical_datetime(key: str, value: str) -> tuple[datetime | None, bool]:
    params = key.split(";")[1:]
    tzid = None
    date_only = False
    for param in params:
        if param.upper().startswith("TZID="):
            tzid = param.split("=", 1)[1]
        if param.upper() == "VALUE=DATE":
            date_only = True
    raw = value.strip()
    if date_only or re.fullmatch(r"\d{8}", raw):
        try:
            parsed = datetime.strptime(raw[:8], "%Y%m%d").replace(tzinfo=ZoneInfo(tzid or "America/New_York"))
            return parsed, False
        except (ValueError, KeyError):
            return None, False
    utc = raw.endswith("Z")
    raw = raw[:-1] if utc else raw
    fmt = "%Y%m%dT%H%M%S" if len(raw) >= 15 else "%Y%m%dT%H%M"
    try:
        parsed = datetime.strptime(raw, fmt)
    except ValueError:
        return None, True
    if utc:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        try:
            parsed = parsed.replace(tzinfo=ZoneInfo(tzid or "America/New_York"))
        except KeyError:
            parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed, True


def _parse_bls_ics(text: str) -> list[CriticalEvent]:
    events: list[CriticalEvent] = []
    current: dict[str, str] | None = None
    for line in _unfold_ical(text):
        if line == "BEGIN:VEVENT":
            current = {}
            continue
        if line == "END:VEVENT":
            if current:
                summary = current.get("SUMMARY", "").replace("\\,", ",").replace("\\n", " ").strip()
                kind = (
                    "CPI" if "Consumer Price Index" in summary else
                    "NFP" if "Employment Situation" in summary else
                    "PPI" if "Producer Price Index" in summary else ""
                )
                dt_key = next((key for key in current if key.startswith("DTSTART")), "")
                starts, exact = _ical_datetime(dt_key, current.get(dt_key, "")) if dt_key else (None, False)
                if kind and starts:
                    events.append(CriticalEvent(
                        symbol="BTC",
                        kind=kind,
                        title=summary,
                        starts_at=_iso(starts),
                        ends_at=None,
                        exact_time=exact,
                        priority=DEFAULT_PRIORITIES[kind],
                        source_name="U.S. Bureau of Labor Statistics",
                        source_url="https://www.bls.gov/schedule/news_release/bls.ics",
                    ))
            current = None
            continue
        if current is not None and ":" in line:
            key, value = line.split(":", 1)
            current[key] = value
    return events


def _html_text(text: str) -> str:
    parser = _TextParser()
    parser.feed(text)
    value = parser.text()
    value = re.sub(r"\bN\s+ews\b", "News", value, flags=re.IGNORECASE)
    value = re.sub(r"\bD\s+ata\b", "Data", value, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", value).strip()


def _parse_bea_schedule(text: str, year: int) -> list[CriticalEvent]:
    flat = _html_text(text)
    detected_year = re.search(r"\bYear\s+(20\d{2})\b", flat, flags=re.IGNORECASE)
    if detected_year:
        year = int(detected_year.group(1))
    month_pattern = "|".join(name.title() for name in MONTHS)
    pattern = re.compile(
        rf"\b({month_pattern})\s+(\d{{1,2}})\s+(\d{{1,2}}:\d{{2}})\s+(AM|PM)\s+(?:News|Data)\s+"
        rf"(.+?)(?=\s+(?:{month_pattern})\s+\d{{1,2}}\s+\d{{1,2}}:\d{{2}}\s+(?:AM|PM)\s+(?:News|Data)|\s+To Be Announced|$)",
        flags=re.IGNORECASE,
    )
    result: list[CriticalEvent] = []
    eastern = ZoneInfo("America/New_York")
    for match in pattern.finditer(flat):
        month_name, day_text, clock, ampm, title = match.groups()
        title = title.strip(" |-")
        lowered = title.lower()
        kind = "GDP" if lowered.startswith("gdp") else "PCE" if lowered.startswith("personal income and outlays") else ""
        if not kind:
            continue
        try:
            local = datetime.strptime(
                f"{year}-{MONTHS[month_name.lower()]:02d}-{int(day_text):02d} {clock} {ampm.upper()}",
                "%Y-%m-%d %I:%M %p",
            ).replace(tzinfo=eastern)
        except (ValueError, KeyError):
            continue
        result.append(CriticalEvent(
            symbol="BTC",
            kind=kind,
            title=title,
            starts_at=_iso(local),
            ends_at=None,
            exact_time=True,
            priority=DEFAULT_PRIORITIES[kind],
            source_name="U.S. Bureau of Economic Analysis",
            source_url="https://www.bea.gov/news/schedule",
        ))
    return result


def _parse_fomc_calendar(text: str, years: Iterable[int]) -> list[CriticalEvent]:
    flat = _html_text(text)
    result: list[CriticalEvent] = []
    eastern = ZoneInfo("America/New_York")
    headings = list(re.finditer(r"\b(20\d{2})\s+FOMC Meetings\b", flat))
    blocks: dict[int, str] = {}
    for index, heading in enumerate(headings):
        year = int(heading.group(1))
        end = headings[index + 1].start() if index + 1 < len(headings) else len(flat)
        blocks[year] = flat[heading.end():end]
    for year in years:
        block = blocks.get(year, "")
        if not block:
            continue
        for match in re.finditer(
            r"\b(January|February|March|April|May|June|July|August|September|October|November|December|Jan/Feb|Apr/May|Oct/Nov)\s+(\d{1,2})-(\d{1,2})\*?",
            block,
            flags=re.IGNORECASE,
        ):
            label, first_day, second_day = match.groups()
            labels = label.split("/")
            end_month_name = labels[-1]
            month = MONTHS.get({"jan": "january", "feb": "february", "apr": "april", "oct": "october", "nov": "november"}.get(end_month_name.lower(), end_month_name.lower()))
            if not month:
                continue
            try:
                local = datetime(year, month, int(second_day), 14, 0, tzinfo=eastern)
            except ValueError:
                continue
            result.append(CriticalEvent(
                symbol="BTC",
                kind="FOMC",
                title=f"FOMC meeting {label} {first_day}-{second_day}, {year}",
                starts_at=_iso(local),
                ends_at=None,
                exact_time=True,
                priority=DEFAULT_PRIORITIES["FOMC"],
                source_name="Federal Reserve Board",
                source_url="https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
            ))
    return result


def _parse_status_events(text: str, *, symbol: str, source_name: str, source_url: str, scheduled: bool) -> list[CriticalEvent]:
    try:
        payload = json.loads(text)
    except (ValueError, TypeError, json.JSONDecodeError):
        return []
    key = "scheduled_maintenances" if scheduled else "incidents"
    rows = payload.get(key) if isinstance(payload, Mapping) else None
    if not isinstance(rows, list):
        return []
    result: list[CriticalEvent] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        title = str(row.get("name") or ("Scheduled maintenance" if scheduled else "Network incident")).strip()
        starts = _parse_iso(row.get("scheduled_for") if scheduled else row.get("created_at"))
        ends = _parse_iso(row.get("scheduled_until") if scheduled else row.get("resolved_at"))
        impact = str(row.get("impact") or "minor").lower()
        priority = 100 if not scheduled else (94 if impact in {"major", "critical"} else 86)
        result.append(CriticalEvent(
            symbol=symbol,
            kind="UPGRADE" if scheduled else "NETWORK",
            title=title,
            starts_at=_iso(starts),
            ends_at=_iso(ends),
            exact_time=starts is not None,
            priority=priority,
            source_name=source_name,
            source_url=source_url,
            active=not scheduled,
        ))
    return result


def _host_allowed(url: str, allowlist: Iterable[str]) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme != "https" or not parsed.hostname:
        return False
    host = parsed.hostname.lower().rstrip(".")
    return any(host == allowed or host.endswith("." + allowed) for allowed in allowlist)


def _verified_feed_events(raw: Any, allowlist: Iterable[str], symbols: set[str]) -> list[CriticalEvent]:
    rows = raw.get("events") if isinstance(raw, Mapping) else raw
    if not isinstance(rows, list):
        return []
    result: list[CriticalEvent] = []
    for item in rows:
        if not isinstance(item, Mapping) or item.get("verified") is not True:
            continue
        event = _event_from_dict(item)
        if event is None or event.symbol not in symbols:
            continue
        if not _host_allowed(event.source_url, allowlist):
            continue
        if not event.active and _parse_iso(event.starts_at) is None:
            continue
        result.append(event)
    return result


def _dedupe(events: Iterable[CriticalEvent]) -> list[CriticalEvent]:
    best: dict[tuple[str, str, str | None, str], CriticalEvent] = {}
    for event in events:
        key = (event.symbol, event.kind, event.starts_at, event.title.lower())
        old = best.get(key)
        if old is None or event.priority > old.priority:
            best[key] = event
    return list(best.values())


def _display_horizon_days(kind: str, config: Mapping[str, Any]) -> int:
    if kind == "UNLOCK":
        return max(1, int(config.get("unlock_display_days", 14)))
    if kind in {"UPGRADE", "GOVERNANCE", "SUPPLY", "ETF", "EXPIRY", "NEWS"}:
        return max(1, int(config.get("coin_event_display_days", 7)))
    return max(1, int(config.get("macro_display_days", 7)))


def _event_timing(event: CriticalEvent, now: datetime, config: Mapping[str, Any]) -> tuple[bool, bool, int | None, int, float | None]:
    starts = _parse_iso(event.starts_at)
    ends = _parse_iso(event.ends_at)
    post_minutes = max(0, int(config.get("post_event_block_minutes", 10)))
    active = bool(event.active)
    hours: float | None = None
    if starts:
        if event.exact_time:
            hours = (starts - now).total_seconds() / 3600.0
        else:
            # Date-only facts remain visible for the complete local calendar
            # day. Do not invent a clock time by treating midnight as exact.
            zone = ZoneInfo(str(config.get("timezone_name", "Europe/Berlin")))
            days = (starts.astimezone(zone).date() - now.astimezone(zone).date()).days
            hours = float(days * 24)
        if ends:
            active = active or starts <= now <= ends
        elif not event.active and event.exact_time:
            active = starts <= now <= starts + timedelta(minutes=post_minutes)
    if ends and now > ends + timedelta(minutes=post_minutes):
        active = False

    if event.kind == "NETWORK":
        return active, active, 10 if active else None, 100 if active else 0, hours

    block_new = False
    leverage_cap: int | None = None
    risk = 0
    if event.kind == "UNLOCK":
        if hours is None:
            risk = 20
        elif hours > 24 * 7:
            risk = 18
        elif hours > 48:
            risk = 32
        elif hours > 24:
            risk = 45
            leverage_cap = 20
        elif hours > 0.25:
            risk = 62
            leverage_cap = 15
        elif event.exact_time and hours >= -(post_minutes / 60.0):
            risk = 88
            block_new = True
            leverage_cap = 10
        elif not event.exact_time and hours == 0:
            # The date is verified but the clock is not: show U0D, reduce risk,
            # and avoid pretending that a specific minute is known.
            risk = 68
            leverage_cap = 15
        else:
            risk = 0
    else:
        if active:
            risk = 95
            block_new = True
            leverage_cap = 10
        elif hours is None:
            risk = 20
        elif hours > 6:
            risk = 20
        elif hours > 1:
            risk = 45
            leverage_cap = 20
        elif hours > 0.25:
            risk = 72
            leverage_cap = 15
        elif hours >= 0:
            risk = 95
            block_new = True
            leverage_cap = 10
        elif hours >= -(post_minutes / 60.0):
            risk = 95
            block_new = True
            leverage_cap = 10
    return active, block_new, leverage_cap, risk, hours


def _event_code(event: CriticalEvent, now: datetime, timezone_name: str, active: bool) -> str:
    base = KIND_CODES[event.kind]
    if active:
        return base + "!"
    starts = _parse_iso(event.starts_at)
    if starts is None:
        return base
    zone = ZoneInfo(timezone_name)
    local_now = now.astimezone(zone)
    local_start = starts.astimezone(zone)
    days = (local_start.date() - local_now.date()).days
    if days <= 0:
        if not event.exact_time:
            return base + "0D"
        return f"{base}@{local_start:%H}" if local_start.minute == 0 else f"{base}@{local_start:%H:%M}"
    return f"{base}{days}D"


def _pick_marks(events: list[CriticalEvent], symbols: set[str], now: datetime, timezone_name: str, config: Mapping[str, Any]) -> dict[str, EventMark]:
    eligible: dict[str, list[tuple[CriticalEvent, bool, bool, int | None, int, float | None]]] = {symbol: [] for symbol in symbols}
    for event in events:
        if event.symbol not in symbols:
            continue
        active, block_new, leverage_cap, risk, hours = _event_timing(event, now, config)
        starts = _parse_iso(event.starts_at)
        if not active:
            if starts is None:
                continue
            if hours is not None and hours < -max(1, int(config.get("post_event_block_minutes", 10))) / 60.0:
                continue
            horizon = _display_horizon_days(event.kind, config)
            if hours is not None and hours > horizon * 24:
                continue
        eligible[event.symbol].append((event, active, block_new, leverage_cap, risk, hours))

    marks: dict[str, EventMark] = {}
    for symbol, rows in eligible.items():
        if not rows:
            continue
        def ranking(row: tuple[CriticalEvent, bool, bool, int | None, int, float | None]) -> tuple[int, float, int, int]:
            event, active, block_new, _, risk, hours = row
            # Balance importance and proximity. A near CPI/GDP release may
            # outrank a distant FOMC date, while a minor PPI date does not hide
            # a materially more important meeting several days later.
            proximity_bonus = (
                max(0.0, 28.0 - min(max(hours, 0.0), 168.0) / 6.0)
                if hours is not None else 0.0
            )
            relevance = float(event.priority + risk) + proximity_bonus
            return (1 if active else 0, relevance, risk, event.priority)
        event, active, block_new, leverage_cap, risk, _ = max(rows, key=ranking)
        marks[symbol] = EventMark(
            symbol=symbol,
            code=_event_code(event, now, timezone_name, active),
            kind=event.kind,
            title=event.title,
            starts_at=event.starts_at,
            ends_at=event.ends_at,
            priority=event.priority,
            risk=risk,
            active=active,
            block_new=block_new,
            leverage_cap=leverage_cap,
            source_name=event.source_name,
            source_url=event.source_url,
        )
    return marks


def load_critical_events(
    config: Mapping[str, Any],
    *,
    now: datetime,
    cache_path: Path,
    local_feed_path: Path | None = None,
) -> EventSnapshot:
    section = config.get("events") if isinstance(config, Mapping) else None
    section = section if isinstance(section, Mapping) else {}
    if not bool(section.get("enabled", True)):
        return EventSnapshot({}, [], [], _iso(now) or "")

    symbols = {str(value).upper() for value in config.get("candidate_symbols", [])}
    timeout = max(3.0, min(20.0, float(section.get("timeout_seconds", 8.0))))
    timezone_name = str(config.get("timezone", "Europe/Berlin"))
    diagnostics: list[str] = []
    cached = _load_json(cache_path)
    if cached.get("version") != CACHE_VERSION:
        cached = {"version": CACHE_VERSION}

    schedule_refresh = max(15, int(section.get("schedule_refresh_minutes", 60))) * 60
    max_stale = max(1, int(section.get("schedule_cache_max_stale_hours", 48))) * 3600
    fetched_at = int(cached.get("schedule_fetched_at") or 0)
    schedule_raw = cached.get("schedule_events") if isinstance(cached.get("schedule_events"), list) else []
    schedule_events = [event for event in (_event_from_dict(item) for item in schedule_raw if isinstance(item, Mapping)) if event]

    refresh_schedule = int(now.timestamp()) - fetched_at >= schedule_refresh or not schedule_events
    if refresh_schedule:
        sources = {
            "bls": str(section.get("bls_ics_url", "https://www.bls.gov/schedule/news_release/bls.ics")),
            "bea": str(section.get("bea_schedule_url", "https://www.bea.gov/news/schedule")),
            "fomc": str(section.get("fomc_calendar_url", "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm")),
        }
        fetched: dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {pool.submit(_request_text, url, timeout=timeout): name for name, url in sources.items()}
            for future in as_completed(futures):
                name = futures[future]
                try:
                    fetched[name] = future.result()
                except Exception as exc:
                    diagnostics.append(f"{name}-Kalender nicht aktualisiert: {exc}")
        parsers = {
            "bls": lambda value: _parse_bls_ics(value),
            "bea": lambda value: _parse_bea_schedule(value, now.year),
            "fomc": lambda value: _parse_fomc_calendar(value, (now.year, now.year + 1)),
        }
        source_names = {
            "bls": "U.S. Bureau of Labor Statistics",
            "bea": "U.S. Bureau of Economic Analysis",
            "fomc": "Federal Reserve Board",
        }
        fresh: list[CriticalEvent] = []
        replaced_sources: set[str] = set()
        for name, value in fetched.items():
            parsed = parsers[name](value)
            if parsed:
                fresh.extend(parsed)
                replaced_sources.add(source_names[name])
            else:
                diagnostics.append(f"{name}-Kalender gelesen, aber keine passenden Termine erkannt")
        if fresh:
            # A temporary failure of one official source must not erase its
            # still-valid cached dates merely because another source succeeded.
            retained = [
                event for event in schedule_events
                if event.source_name not in replaced_sources
            ]
            schedule_events = _dedupe([*retained, *fresh])
            cached["schedule_fetched_at"] = int(now.timestamp())
            cached["schedule_events"] = [asdict(event) for event in schedule_events]
        elif fetched_at and int(now.timestamp()) - fetched_at > max_stale:
            schedule_events = []
            diagnostics.append("Offizieller Termin-Cache zu alt; keine Termine angezeigt")

    status_sources = [
        ("SOL", "Solana Status", "https://status.solana.com/api/v2/incidents/unresolved.json", False),
        ("SOL", "Solana Status", "https://status.solana.com/api/v2/scheduled-maintenances/upcoming.json", True),
        ("HYPE", "Hyperliquid Status", "https://hyperliquid.statuspage.io/api/v2/incidents/unresolved.json", False),
        ("HYPE", "Hyperliquid Status", "https://hyperliquid.statuspage.io/api/v2/scheduled-maintenances/upcoming.json", True),
    ]
    status_events: list[CriticalEvent] = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {
            pool.submit(_request_text, url, timeout=timeout): (symbol, source_name, url, scheduled)
            for symbol, source_name, url, scheduled in status_sources
            if symbol in symbols
        }
        for future in as_completed(futures):
            symbol, source_name, url, scheduled = futures[future]
            try:
                status_events.extend(_parse_status_events(
                    future.result(),
                    symbol=symbol,
                    source_name=source_name,
                    source_url=url,
                    scheduled=scheduled,
                ))
            except Exception as exc:
                diagnostics.append(f"{source_name} nicht aktualisiert: {exc}")

    allowlist = {
        str(value).lower().strip()
        for value in section.get("verified_source_domains", [])
        if str(value).strip()
    }
    feed_events: list[CriticalEvent] = []
    if local_feed_path is not None:
        feed_events.extend(_verified_feed_events(_load_json(local_feed_path), allowlist, symbols))
    inline = os.getenv("CRYPTO_EVENTS_JSON", "").strip()
    if inline:
        try:
            feed_events.extend(_verified_feed_events(json.loads(inline), allowlist, symbols))
        except (ValueError, TypeError, json.JSONDecodeError):
            diagnostics.append("CRYPTO_EVENTS_JSON ist ungültig")
    feed_url = os.getenv("CRYPTO_EVENTS_URL", "").strip() or str(section.get("verified_feed_url", "")).strip()
    if feed_url:
        if _host_allowed(feed_url, allowlist):
            try:
                feed_events.extend(_verified_feed_events(json.loads(_request_text(feed_url, timeout=timeout)), allowlist, symbols))
            except Exception as exc:
                diagnostics.append(f"Verifizierter Ereignisfeed nicht aktualisiert: {exc}")
        else:
            diagnostics.append("Ereignisfeed-Domain nicht freigegeben")

    all_events = _dedupe([*schedule_events, *status_events, *feed_events])
    timing_section = dict(section)
    timing_section["timezone_name"] = timezone_name
    marks = _pick_marks(all_events, symbols, now, timezone_name, timing_section)
    cached["version"] = CACHE_VERSION
    cached["updated_at"] = int(now.timestamp())
    try:
        _save_json(cache_path, cached)
    except OSError as exc:
        diagnostics.append(f"Ereignis-Cache nicht gespeichert: {exc}")
    return EventSnapshot(marks, all_events, diagnostics, _iso(now) or "")


# Package revision: v3.8.1-events-trend-dip-r1
