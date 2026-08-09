# r3
"""Verified scheduled and externally confirmed event context for CF v6.1.0.

Automatic facts come only from official public schedules/status pages. Project-
specific events such as token unlocks are accepted only from a local or remote
JSON feed that explicitly marks the item verified and supplies an allowed HTTPS
source URL. Events never create a Long/Short direction; they only add visibility
and neutral risk controls.
"""
from __future__ import annotations

import calendar
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

CACHE_VERSION = "event-cache-v610-r2"
USER_AGENT = "crypto-signal-monitor/6.1.0"
MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}


US_MACRO_KINDS = {
    "FOMC", "BEIGE", "CPI", "NFP", "PPI", "JOLTS", "ECI",
    "PRODUCTIVITY", "IMPORT_PRICES", "GDP", "PCE", "TRADE",
    "RETAIL", "DURABLE", "HOUSING_STARTS", "NEW_HOME_SALES",
    "FACTORY_ORDERS", "CONSTRUCTION", "BUSINESS_INVENTORIES",
    "ADVANCE_INDICATORS", "CLAIMS", "ADP",
    "CONSUMER_CONFIDENCE", "MICHIGAN", "ISM_MANUFACTURING",
    "ISM_SERVICES",
}

KIND_CODES = {
    "FOMC": "FED",
    "BEIGE": "BB",
    "CPI": "CPI",
    "NFP": "NFP",
    "PPI": "PPI",
    "JOLTS": "JOLTS",
    "ECI": "ECI",
    "PRODUCTIVITY": "PROD",
    "IMPORT_PRICES": "IMP",
    "GDP": "GDP",
    "PCE": "PCE",
    "TRADE": "TRD",
    "RETAIL": "RET",
    "DURABLE": "DUR",
    "HOUSING_STARTS": "HOU",
    "NEW_HOME_SALES": "NHS",
    "FACTORY_ORDERS": "FAC",
    "CONSTRUCTION": "CON",
    "BUSINESS_INVENTORIES": "INV",
    "ADVANCE_INDICATORS": "AEI",
    "CLAIMS": "CLM",
    "ADP": "ADP",
    "CONSUMER_CONFIDENCE": "CONF",
    "MICHIGAN": "MICH",
    "ISM_MANUFACTURING": "ISMM",
    "ISM_SERVICES": "ISMS",
    "EXPIRY": "EXP",
    "ETF": "ETF",
    "ETF_FLOW": "E",
    "UNLOCK": "U",
    "UPGRADE": "UPG",
    "MAINTENANCE": "MNT",
    "GOVERNANCE": "GOV",
    "SUPPLY": "SUP",
    "NEWS": "N",
    "NETWORK": "NET",
    "SECURITY": "SEC",
}
DEFAULT_PRIORITIES = {
    "NETWORK": 100,
    "FOMC": 90,
    "BEIGE": 80,
    "CPI": 90,
    "NFP": 90,
    "PPI": 80,
    "JOLTS": 80,
    "ECI": 80,
    "PRODUCTIVITY": 80,
    "IMPORT_PRICES": 80,
    "GDP": 90,
    "PCE": 90,
    "TRADE": 80,
    "RETAIL": 80,
    "DURABLE": 80,
    "HOUSING_STARTS": 80,
    "NEW_HOME_SALES": 80,
    "FACTORY_ORDERS": 80,
    "CONSTRUCTION": 80,
    "BUSINESS_INVENTORIES": 80,
    "ADVANCE_INDICATORS": 80,
    "CLAIMS": 80,
    "ADP": 80,
    "CONSUMER_CONFIDENCE": 80,
    "MICHIGAN": 80,
    "ISM_MANUFACTURING": 80,
    "ISM_SERVICES": 80,
    "UNLOCK": 94,
    "ETF": 92,
    "ETF_FLOW": 91,
    "SUPPLY": 90,
    "UPGRADE": 88,
    "MAINTENANCE": 84,
    "GOVERNANCE": 86,
    "EXPIRY": 80,
    "NEWS": 70,
    "SECURITY": 100,
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
    display_marks: dict[str, EventMark] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "marks": {symbol: asdict(mark) for symbol, mark in self.marks.items()},
            "display_marks": {
                symbol: asdict(mark) for symbol, mark in self.display_marks.items()
            },
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


def _event_from_dict(
    raw: Mapping[str, Any],
    symbol_aliases: Mapping[str, str] | None = None,
) -> CriticalEvent | None:
    symbol = str(raw.get("symbol") or "").upper().strip()
    if symbol_aliases:
        symbol = str(symbol_aliases.get(symbol, symbol)).upper().strip()
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
    # This parser is intentionally BLS-specific. BLS states that all wall
    # times in its release calendar are Eastern Time, while the ICS may carry
    # Outlook/Windows TZIDs that Python's IANA-only ZoneInfo cannot resolve.
    # Respect an explicit trailing Z; otherwise interpret the wall clock as ET.
    date_only = any(
        param.upper() == "VALUE=DATE"
        for param in key.split(";")[1:]
    )
    raw = value.strip()
    eastern = ZoneInfo("America/New_York")
    if date_only or re.fullmatch(r"\d{8}", raw):
        try:
            return datetime.strptime(raw[:8], "%Y%m%d").replace(tzinfo=eastern), False
        except ValueError:
            return None, False
    utc = raw.endswith("Z")
    raw = raw[:-1] if utc else raw
    fmt = "%Y%m%dT%H%M%S" if len(raw) >= 15 else "%Y%m%dT%H%M"
    try:
        parsed = datetime.strptime(raw, fmt)
    except ValueError:
        return None, True
    return parsed.replace(tzinfo=timezone.utc if utc else eastern), True


def _macro_kind_from_bls_summary(summary: str) -> str:
    """Map only broadly market-moving national BLS releases."""
    normalized = re.sub(r"\s+", " ", summary).strip().lower()
    patterns = (
        ("consumer price index", "CPI"),
        ("employment situation", "NFP"),
        ("producer price index", "PPI"),
        ("job openings and labor turnover survey", "JOLTS"),
        ("employment cost index", "ECI"),
        ("productivity and costs", "PRODUCTIVITY"),
        ("u.s. import and export price indexes", "IMPORT_PRICES"),
        ("import and export price indexes", "IMPORT_PRICES"),
    )
    return next((kind for phrase, kind in patterns if phrase in normalized), "")


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
                kind = _macro_kind_from_bls_summary(summary)
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



def _parse_bls_html_schedule(text: str) -> list[CriticalEvent]:
    """Secondary official BLS schedule parser used to cross-cover the ICS feed."""
    flat = _html_text(text)
    weekdays = r"Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday"
    months = r"January|February|March|April|May|June|July|August|September|October|November|December"
    pattern = re.compile(
        rf"(?:{weekdays}),?\s+({months})\s+(\d{{1,2}}),\s+(20\d{{2}})\s+"
        rf"(\d{{1,2}}:\d{{2}})\s+(AM|PM)\s+(.+?)"
        rf"(?=\s+(?:{weekdays}),?\s+(?:{months})\s+\d{{1,2}},\s+20\d{{2}}\s+\d{{1,2}}:\d{{2}}\s+(?:AM|PM)|\s+NOTE:|$)",
        flags=re.IGNORECASE,
    )
    eastern = ZoneInfo("America/New_York")
    result: list[CriticalEvent] = []
    for month_name, day_text, year_text, clock, ampm, title in pattern.findall(flat):
        title = re.sub(r"\s+", " ", title).strip(" |-.")
        kind = _macro_kind_from_bls_summary(title)
        if not kind:
            continue
        try:
            local = datetime.strptime(
                f"{year_text}-{MONTHS[month_name.lower()]:02d}-{int(day_text):02d} {clock} {ampm.upper()}",
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
            source_name="U.S. Bureau of Labor Statistics",
            source_url=f"https://www.bls.gov/schedule/{local.year}/{local.month:02d}_sched_list.htm",
        ))
    return result

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
        kind = (
            "GDP" if lowered.startswith("gdp") else
            "PCE" if lowered.startswith("personal income and outlays") else
            "TRADE" if lowered.startswith("u.s. international trade in goods and services") else
            ""
        )
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
                source_name="Federal Reserve Board - FOMC",
                source_url="https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
            ))
    return result


def _parse_fed_month_calendar(text: str, *, year: int, month: int) -> list[CriticalEvent]:
    """Parse timed FOMC-minutes and Beige Book releases from a Fed month page."""
    flat = _html_text(text)
    eastern = ZoneInfo("America/New_York")
    result: list[CriticalEvent] = []
    patterns = (
        (
            "FOMC",
            "FOMC Minutes",
            re.compile(
                r"(\d{1,2}:\d{2})\s*(a\.m\.|p\.m\.)\s+FOMC Minutes\s+"
                r"Meeting of\s+[A-Za-z]+\s+\d{1,2}-\d{1,2}\s+(\d{1,2})(?=\s|$)",
                flags=re.IGNORECASE,
            ),
        ),
        (
            "BEIGE",
            "Beige Book",
            re.compile(
                r"(\d{1,2}:\d{2})\s*(a\.m\.|p\.m\.)\s+Beige Book\s+(\d{1,2})(?=\s|$)",
                flags=re.IGNORECASE,
            ),
        ),
    )
    for kind, title, pattern in patterns:
        for clock, ampm, day_text in pattern.findall(flat):
            try:
                local = datetime.strptime(
                    f"{year}-{month:02d}-{int(day_text):02d} {clock} {ampm.replace('.', '').upper()}",
                    "%Y-%m-%d %I:%M %p",
                ).replace(tzinfo=eastern)
            except ValueError:
                continue
            result.append(CriticalEvent(
                symbol="BTC",
                kind=kind,
                title=title,
                starts_at=_iso(local),
                ends_at=None,
                exact_time=True,
                priority=DEFAULT_PRIORITIES[kind],
                source_name="Federal Reserve Board Calendar",
                source_url=f"https://www.federalreserve.gov/newsevents/{year}-{local:%B}.htm".lower(),
            ))
    return result


def _parse_census_schedule(text: str) -> list[CriticalEvent]:
    """Parse selected market-moving releases from the official Census calendar."""
    flat = _html_text(text)
    month_pattern = "|".join(name.title() for name in MONTHS)
    releases = (
        (r"Advance Monthly Sales for Retail and Food Services", "RETAIL", "Advance Retail Sales"),
        (
            r"Advance Report on Durable Goods(?:--|—|-)Manufacturers['’] Shipments, Inventories, and Orders",
            "DURABLE",
            "Advance Durable Goods",
        ),
        (
            r"New Residential Construction(?: \(Building Permits, Housing Starts, and Housing Completions\))?",
            "HOUSING_STARTS",
            "New Residential Construction",
        ),
        (r"New Residential Sales", "NEW_HOME_SALES", "New Residential Sales"),
        (
            r"Full Report - Manufacturers['’] Shipments, Inventories and Orders",
            "FACTORY_ORDERS",
            "Manufacturers' Orders",
        ),
        (
            r"Construction Spending \(Construction Put in Place\)",
            "CONSTRUCTION",
            "Construction Spending",
        ),
        (
            r"Manufacturing and Trade: Inventories and Sales",
            "BUSINESS_INVENTORIES",
            "Business Inventories",
        ),
        (
            r"Advance Economic Indicators Report \(International Trade, Retail, & Wholesale\)",
            "ADVANCE_INDICATORS",
            "Advance Economic Indicators",
        ),
        (
            r"U\.S\. International Trade in Goods and Services",
            "TRADE",
            "U.S. International Trade in Goods and Services",
        ),
    )
    eastern = ZoneInfo("America/New_York")
    result: list[CriticalEvent] = []
    for title_pattern, kind, title in releases:
        pattern = re.compile(
            rf"{title_pattern}\s+({month_pattern})\s+(\d{{1,2}}),?\s+(20\d{{2}})\s+"
            rf"(\d{{1,2}}:\d{{2}})\s+(AM|PM)",
            flags=re.IGNORECASE,
        )
        for month_name, day_text, year_text, clock, ampm in pattern.findall(flat):
            try:
                local = datetime.strptime(
                    f"{year_text}-{MONTHS[month_name.lower()]:02d}-{int(day_text):02d} {clock} {ampm.upper()}",
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
                source_name="U.S. Census Bureau",
                source_url="https://www.census.gov/economic-indicators/calendar-listview.html",
            ))
    return result


def _parse_ism_schedule(text: str) -> list[CriticalEvent]:
    """Parse the official annual ISM Manufacturing/Services release table."""
    flat = _html_text(text)
    eastern = ZoneInfo("America/New_York")
    result: list[CriticalEvent] = []
    month_pattern = "|".join(name.title() for name in MONTHS)
    pattern = re.compile(
        rf"\b({month_pattern})\s+(20\d{{2}})\s+(\d{{1,2}})\s+(\d{{1,2}})(?=\s|$)",
        flags=re.IGNORECASE,
    )
    for month_name, year_text, manufacturing_day, services_day in pattern.findall(flat):
        month = MONTHS.get(month_name.lower())
        if not month:
            continue
        for kind, day_text, title in (
            ("ISM_MANUFACTURING", manufacturing_day, "ISM Manufacturing PMI"),
            ("ISM_SERVICES", services_day, "ISM Services PMI"),
        ):
            try:
                local = datetime(
                    int(year_text), month, int(day_text), 10, 0, tzinfo=eastern
                )
            except ValueError:
                continue
            result.append(CriticalEvent(
                symbol="BTC",
                kind=kind,
                title=title,
                starts_at=_iso(local),
                ends_at=None,
                exact_time=True,
                priority=DEFAULT_PRIORITIES[kind],
                source_name="Institute for Supply Management",
                source_url=(
                    "https://www.ismworld.org/supply-management-news-and-reports/"
                    "reports/rob-report-calendar/"
                ),
            ))
    return result




def _parse_adp_schedule(text: str) -> list[CriticalEvent]:
    """Parse the official monthly ADP report dates (monthly report only)."""
    flat = _html_text(text)
    match = re.search(
        r"Upcoming Reports:\s*(.+?)(?=Upcoming reports \(weekly NER pulse\)|Technical Notes|$)",
        flat,
        flags=re.IGNORECASE,
    )
    if not match:
        return []
    eastern = ZoneInfo("America/New_York")
    result: list[CriticalEvent] = []
    month_pattern = "|".join(name.title() for name in MONTHS)
    for month_name, day_text, year_text in re.findall(
        rf"\b({month_pattern})\s+(\d{{1,2}}),\s+(20\d{{2}})\b",
        match.group(1),
        flags=re.IGNORECASE,
    ):
        try:
            local = datetime(
                int(year_text), MONTHS[month_name.lower()], int(day_text),
                8, 15, tzinfo=eastern,
            )
        except (ValueError, KeyError):
            continue
        result.append(CriticalEvent(
            symbol="BTC",
            kind="ADP",
            title="ADP National Employment Report",
            starts_at=_iso(local),
            ends_at=None,
            exact_time=True,
            priority=DEFAULT_PRIORITIES["ADP"],
            source_name="ADP Research Institute",
            source_url="https://adpemploymentreport.com/",
        ))
    return result


def _parse_fred_adp_schedule(text: str) -> list[CriticalEvent]:
    """Parse the St. Louis Fed's official ADP release calendar fallback."""
    flat = _html_text(text)
    central = ZoneInfo("America/Chicago")
    month_pattern = "|".join(name.title() for name in MONTHS)
    pattern = re.compile(
        rf"(?:Monday|Tuesday|Wednesday|Thursday|Friday)\s+"
        rf"({month_pattern})\s+(\d{{1,2}}),\s+(20\d{{2}})"
        rf"(?:\s+Updated)?\s+(\d{{1,2}}):(\d{{2}})\s*(am|pm)\s+"
        rf"ADP National Employment Report",
        flags=re.IGNORECASE,
    )
    result: list[CriticalEvent] = []
    for month_name, day_text, year_text, hour_text, minute_text, ampm in pattern.findall(flat):
        hour = int(hour_text) % 12 + (12 if ampm.lower() == "pm" else 0)
        try:
            local = datetime(
                int(year_text), MONTHS[month_name.lower()], int(day_text),
                hour, int(minute_text), tzinfo=central,
            )
        except (ValueError, KeyError):
            continue
        result.append(CriticalEvent(
            symbol="BTC",
            kind="ADP",
            title="ADP National Employment Report",
            starts_at=_iso(local),
            ends_at=None,
            exact_time=True,
            priority=DEFAULT_PRIORITIES["ADP"],
            source_name="Federal Reserve Bank of St. Louis FRED",
            source_url="https://fred.stlouisfed.org/releases/calendar?rid=194&view=year",
        ))
    return result


def _parse_michigan_schedule(text: str) -> list[CriticalEvent]:
    """Parse the explicitly stated next University of Michigan release."""
    flat = _html_text(text)
    month_pattern = "|".join(name.title() for name in MONTHS)
    match = re.search(
        rf"Next data release:\s*(?:[A-Za-z]+,\s*)?({month_pattern})\s+(\d{{1,2}}),\s+"
        rf"(20\d{{2}}).*?\bat\s+(\d{{1,2}})(?::(\d{{2}}))?\s*(am|pm)\s*ET\b",
        flat,
        flags=re.IGNORECASE,
    )
    if not match:
        return []
    month_name, day_text, year_text, hour_text, minute_text, ampm = match.groups()
    hour = int(hour_text) % 12 + (12 if ampm.lower() == "pm" else 0)
    try:
        local = datetime(
            int(year_text), MONTHS[month_name.lower()], int(day_text),
            hour, int(minute_text or 0), tzinfo=ZoneInfo("America/New_York"),
        )
    except (ValueError, KeyError):
        return []
    return [CriticalEvent(
        symbol="BTC",
        kind="MICHIGAN",
        title="University of Michigan Consumer Sentiment",
        starts_at=_iso(local),
        ends_at=None,
        exact_time=True,
        priority=DEFAULT_PRIORITIES["MICHIGAN"],
        source_name="University of Michigan Surveys of Consumers",
        source_url="https://www.sca.isr.umich.edu/",
    )]


def _parse_consumer_confidence_schedule(
    text: str,
    *,
    now: datetime,
) -> list[CriticalEvent]:
    """Parse The Conference Board's explicit next Consumer Confidence date."""
    flat = _html_text(text)
    month_pattern = "|".join(name.title() for name in MONTHS)
    match = re.search(
        rf"The next release is\s+(?:[A-Za-z]+,\s*)?({month_pattern})\s+"
        rf"(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(20\d{{2}}))?\s+at\s+"
        rf"(\d{{1,2}})(?::(\d{{2}}))?\s*(AM|PM)\s*ET\b",
        flat,
        flags=re.IGNORECASE,
    )
    if not match:
        return []
    month_name, day_text, year_text, hour_text, minute_text, ampm = match.groups()
    eastern = ZoneInfo("America/New_York")
    local_now = now.astimezone(eastern)
    year = int(year_text) if year_text else local_now.year
    hour = int(hour_text) % 12 + (12 if ampm.lower() == "pm" else 0)
    try:
        local = datetime(
            year, MONTHS[month_name.lower()], int(day_text), hour,
            int(minute_text or 0), tzinfo=eastern,
        )
    except (ValueError, KeyError):
        return []
    if not year_text and local < local_now - timedelta(days=45):
        local = local.replace(year=local.year + 1)
    return [CriticalEvent(
        symbol="BTC",
        kind="CONSUMER_CONFIDENCE",
        title="Conference Board Consumer Confidence",
        starts_at=_iso(local),
        ends_at=None,
        exact_time=True,
        priority=DEFAULT_PRIORITIES["CONSUMER_CONFIDENCE"],
        source_name="The Conference Board",
        source_url="https://www.conference-board.org/topics/consumer-confidence/",
    )]


def _observed_fixed_holiday(year: int, month: int, day: int) -> datetime.date:
    value = datetime(year, month, day).date()
    if value.weekday() == 5:
        return value - timedelta(days=1)
    if value.weekday() == 6:
        return value + timedelta(days=1)
    return value


def _claims_release_holidays(year: int) -> set[datetime.date]:
    thanksgiving = datetime(year, 11, 1).date()
    thanksgiving += timedelta(days=(3 - thanksgiving.weekday()) % 7 + 21)
    return {
        _observed_fixed_holiday(year, 1, 1),
        _observed_fixed_holiday(year, 6, 19),
        _observed_fixed_holiday(year, 7, 4),
        thanksgiving,
        _observed_fixed_holiday(year, 12, 25),
    }


def _scheduled_claims(now: datetime, count: int = 10) -> list[CriticalEvent]:
    """Create the official weekly 08:30 ET initial-claims schedule."""
    eastern = ZoneInfo("America/New_York")
    local_now = now.astimezone(eastern)
    cursor = local_now.date()
    result: list[CriticalEvent] = []
    checked: set[datetime.date] = set()
    while len(result) < count and len(checked) < count * 3:
        days_to_thursday = (3 - cursor.weekday()) % 7
        nominal = cursor + timedelta(days=days_to_thursday)
        if nominal in checked:
            nominal += timedelta(days=7)
        checked.add(nominal)
        release_day = nominal
        if nominal in _claims_release_holidays(nominal.year):
            release_day = nominal - timedelta(days=1)
            while release_day.weekday() >= 5:
                release_day -= timedelta(days=1)
        local = datetime(
            release_day.year, release_day.month, release_day.day,
            8, 30, tzinfo=eastern,
        )
        if local >= local_now - timedelta(minutes=15):
            result.append(CriticalEvent(
                symbol="BTC",
                kind="CLAIMS",
                title="U.S. Initial Unemployment Claims",
                starts_at=_iso(local),
                ends_at=None,
                exact_time=True,
                priority=DEFAULT_PRIORITIES["CLAIMS"],
                source_name="U.S. Department of Labor",
                source_url="https://www.dol.gov/newsroom/releases/eta",
            ))
        cursor = nominal + timedelta(days=1)
    return result

def _parse_deribit_expiries(
    text: str,
    *,
    currency: str,
    now: datetime,
    config: Mapping[str, Any],
) -> list[CriticalEvent]:
    """Create factual large-options-expiry events from Deribit public OI."""
    try:
        payload = json.loads(text)
    except (ValueError, TypeError, json.JSONDecodeError):
        return []
    rows = payload.get("result") if isinstance(payload, Mapping) else None
    if not isinstance(rows, list):
        return []
    groups: dict[datetime, float] = {}
    total = 0.0
    pattern = re.compile(rf"^{re.escape(currency)}-(\d{{1,2}}[A-Z]{{3}}\d{{2}})-", re.IGNORECASE)
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        match = pattern.match(str(row.get("instrument_name") or ""))
        if not match:
            continue
        try:
            expiry = datetime.strptime(match.group(1).upper(), "%d%b%y").replace(
                hour=8, tzinfo=timezone.utc
            )
        except ValueError:
            continue
        open_interest = max(0.0, float(row.get("open_interest") or 0.0))
        underlying = max(0.0, float(row.get("underlying_price") or 0.0))
        notional = open_interest * underlying
        if notional <= 0:
            continue
        groups[expiry] = groups.get(expiry, 0.0) + notional
        total += notional
    if total <= 0:
        return []
    thresholds = config.get("deribit_expiry_min_notional_usd")
    thresholds = thresholds if isinstance(thresholds, Mapping) else {}
    minimum = float(thresholds.get(currency, 500_000_000 if currency == "BTC" else 250_000_000))
    minimum_share = float(config.get("deribit_expiry_min_share_pct", 8.0))
    horizon_days = max(1, int(config.get("coin_event_display_days", 7)))
    result: list[CriticalEvent] = []
    for expiry, notional in sorted(groups.items()):
        hours = (expiry - now).total_seconds() / 3600.0
        if hours < -1 or hours > horizon_days * 24:
            continue
        share = notional / total * 100.0
        if notional < minimum or share < minimum_share:
            continue
        result.append(CriticalEvent(
            symbol=currency,
            kind="EXPIRY",
            title=f"Deribit {currency} options expiry: ${notional/1_000_000:.0f}m OI ({share:.1f}%)",
            starts_at=_iso(expiry),
            ends_at=None,
            exact_time=True,
            priority=min(92, 78 + int(min(14.0, share / 2.0))),
            source_name="Deribit public API",
            source_url=f"https://www.deribit.com/api/v2/public/get_book_summary_by_currency?currency={currency}&kind=option",
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


def _verified_feed_events(
    raw: Any,
    allowlist: Iterable[str],
    symbols: set[str],
    symbol_aliases: Mapping[str, str],
) -> list[CriticalEvent]:
    rows = raw.get("events") if isinstance(raw, Mapping) else raw
    if not isinstance(rows, list):
        return []
    result: list[CriticalEvent] = []
    for item in rows:
        if not isinstance(item, Mapping) or item.get("verified") is not True:
            continue
        event = _event_from_dict(item, symbol_aliases)
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
        # Official macro calendars can overlap (for example BLS ICS + BLS HTML
        # or ADP + FRED). Same release kind at the same instant is one event.
        title_key = "" if event.kind in US_MACRO_KINDS else event.title.lower()
        key = (event.symbol, event.kind, event.starts_at, title_key)
        old = best.get(key)
        if old is None or event.priority > old.priority:
            best[key] = event
    return list(best.values())


def _display_horizon_days(kind: str, config: Mapping[str, Any]) -> int:
    if kind == "UNLOCK":
        return max(1, int(config.get("unlock_display_days", 14)))
    if kind in {"UPGRADE", "MAINTENANCE", "GOVERNANCE", "SUPPLY", "ETF", "ETF_FLOW", "EXPIRY", "NEWS", "SECURITY"}:
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
            zone = ZoneInfo(str(config.get("timezone_name", "Europe/Berlin")))
            days = (starts.astimezone(zone).date() - now.astimezone(zone).date()).days
            hours = float(days * 24)
        if ends:
            active = active or starts <= now <= ends
        elif not event.active and event.exact_time:
            active = starts <= now <= starts + timedelta(minutes=post_minutes)
    if event.kind == "ETF_FLOW" and ends is not None:
        active = bool(starts and starts <= now <= ends)
    elif ends and now > ends + timedelta(minutes=post_minutes):
        active = False

    # Date-only unlock/supply facts are active for their full verified calendar day.
    # Their exact minute is unknown, so they reduce risk but never pretend to know
    # a precise release time or impose a hard block.
    if event.kind in {"UNLOCK", "SUPPLY"}:
        if not event.exact_time and hours == 0:
            active = True
            return active, False, 15, 68, hours
        if hours is None:
            return active, False, None, 20, hours
        if hours > 24 * 7:
            return active, False, None, 18, hours
        if hours > 48:
            return active, False, None, 32, hours
        if hours > 24:
            return active, False, 20, 45, hours
        if hours > 0.25:
            return active, False, 15, 62, hours
        if event.exact_time and hours >= -(post_minutes / 60.0):
            return active, False, 10, 88, hours
        return active, False, None, 0, hours

    if event.kind in {"NETWORK", "SECURITY"}:
        return active, active, 10 if active else None, 100 if active else 0, hours

    # Fresh verified headlines are visibility signals, not automatic trade bans.
    if event.kind == "NEWS":
        return active, False, None, 10 if active else 0, hours
    if event.kind == "ETF_FLOW":
        return active, False, None, 10 if active else 0, hours

    # Governance, upgrades, maintenance and ETF decisions can matter, but an
    # already-published headline must not freeze trading. Scheduled exact-time
    # events receive only bounded leverage/risk controls near the appointment.
    if event.kind in {"GOVERNANCE", "UPGRADE", "MAINTENANCE", "ETF"}:
        if active and (hours is None or hours <= 0):
            return active, False, None, 20, hours
        if hours is None or hours > 6:
            return active, False, None, 20, hours
        if hours > 1:
            return active, False, 20, 45, hours
        if hours > 0.25:
            return active, False, 15, 72, hours
        if hours >= -(post_minutes / 60.0):
            return active, False, 12, 82, hours
        return active, False, None, 0, hours

    block_new = False
    leverage_cap: int | None = None
    risk = 0
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
    elif hours >= -(post_minutes / 60.0):
        risk = 95
        block_new = True
        leverage_cap = 10
    return active, block_new, leverage_cap, risk, hours


def _event_code(event: CriticalEvent, now: datetime, timezone_name: str, active: bool) -> str:
    base = KIND_CODES[event.kind]
    if event.kind == "ETF_FLOW":
        match = re.search(r"([+-])\s*(\d+(?:\.\d+)?)\s*M\b", event.title, flags=re.IGNORECASE)
        if match:
            amount = max(1, int(round(float(match.group(2)))))
            return f"E{match.group(1)}{amount}M"
        return "ETF"
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


def _eligible_event_rows(
    events: list[CriticalEvent],
    symbols: set[str],
    now: datetime,
    config: Mapping[str, Any],
) -> dict[str, list[tuple[CriticalEvent, bool, bool, int | None, int, float | None]]]:
    eligible: dict[str, list[tuple[CriticalEvent, bool, bool, int | None, int, float | None]]] = {
        symbol: [] for symbol in symbols
    }
    for event in events:
        if event.symbol not in symbols:
            continue
        active, block_new, leverage_cap, risk, hours = _event_timing(event, now, config)
        starts = _parse_iso(event.starts_at)
        if event.kind == "ETF_FLOW" and not active:
            continue
        if not active:
            if starts is None:
                continue
            if hours is not None and hours < -max(
                1, int(config.get("post_event_block_minutes", 10))
            ) / 60.0:
                continue
            horizon = _display_horizon_days(event.kind, config)
            if hours is not None and hours > horizon * 24:
                continue
        eligible[event.symbol].append(
            (event, active, block_new, leverage_cap, risk, hours)
        )
    return eligible


def _mark_from_row(
    row: tuple[CriticalEvent, bool, bool, int | None, int, float | None],
    *,
    now: datetime,
    timezone_name: str,
) -> EventMark:
    event, active, block_new, leverage_cap, risk, _ = row
    return EventMark(
        symbol=event.symbol,
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


def _pick_marks(
    events: list[CriticalEvent],
    symbols: set[str],
    now: datetime,
    timezone_name: str,
    config: Mapping[str, Any],
) -> dict[str, EventMark]:
    """Pick risk marks; US macro selection is chronological, never priority-filtered."""
    eligible = _eligible_event_rows(events, symbols, now, config)
    marks: dict[str, EventMark] = {}
    for symbol, rows in eligible.items():
        if not rows:
            continue

        def ranking(
            row: tuple[CriticalEvent, bool, bool, int | None, int, float | None]
        ) -> tuple[int, float, int, int]:
            event, active, _, _, risk, hours = row
            proximity_bonus = (
                max(0.0, 28.0 - min(max(hours, 0.0), 168.0) / 6.0)
                if hours is not None else 0.0
            )
            relevance = float(event.priority + risk) + proximity_bonus
            return (1 if active else 0, relevance, risk, event.priority)

        macro_rows = [row for row in rows if row[0].kind in US_MACRO_KINDS]
        # A confirmed BTC security/network incident must never be hidden by an
        # otherwise valid macro appointment. Macro releases remain chronological
        # when there is no active hard incident.
        hard_btc_rows = [
            row for row in rows
            if symbol == "BTC"
            and row[0].kind in {"SECURITY", "NETWORK"}
            and (row[1] or row[2])
        ]
        if hard_btc_rows:
            selected = max(hard_btc_rows, key=ranking)
        elif symbol == "BTC" and macro_rows:
            active_rows = [row for row in macro_rows if row[1]]
            upcoming_rows = [row for row in macro_rows if row[5] is not None and row[5] >= 0]
            recent_rows = [row for row in macro_rows if row[5] is not None and row[5] < 0]
            if active_rows:
                selected = min(active_rows, key=lambda row: abs(float(row[5] or 0.0)))
            elif upcoming_rows:
                selected = min(upcoming_rows, key=lambda row: float(row[5] or 0.0))
            else:
                selected = min(recent_rows, key=lambda row: abs(float(row[5] or 0.0)))
        else:
            selected = max(rows, key=ranking)
        marks[symbol] = _mark_from_row(
            selected, now=now, timezone_name=timezone_name
        )
    return marks


def _pick_display_marks(
    events: list[CriticalEvent],
    symbols: set[str],
    now: datetime,
    timezone_name: str,
    config: Mapping[str, Any],
    risk_marks: Mapping[str, EventMark],
) -> dict[str, EventMark]:
    """Alternate today's timed US macro with the BTC price and cycle same-day releases."""
    display = dict(risk_marks)

    # Keep a verified upcoming/current unlock visible even when another event
    # is the primary risk mark for the same coin. The compact code remains
    # attached to the coin (for example SEC!U5D) without changing risk logic.
    eligible_by_symbol = _eligible_event_rows(events, symbols, now, config)
    for symbol, rows in eligible_by_symbol.items():
        unlock_rows = [row for row in rows if row[0].kind == "UNLOCK"]
        if not unlock_rows:
            continue
        upcoming = [row for row in unlock_rows if row[5] is None or row[5] >= 0]
        selected_unlock = min(
            upcoming or unlock_rows,
            key=lambda row: (
                abs(float(row[5] or 0.0)),
                -int(row[0].priority),
            ),
        )
        unlock_mark = _mark_from_row(
            selected_unlock, now=now, timezone_name=timezone_name
        )
        existing = display.get(symbol)
        if existing is None:
            display[symbol] = unlock_mark
        elif existing.kind != "UNLOCK" and unlock_mark.code not in existing.code:
            display[symbol] = EventMark(
                symbol=existing.symbol,
                code=f"{existing.code}{unlock_mark.code}",
                kind=existing.kind,
                title=existing.title,
                starts_at=existing.starts_at,
                ends_at=existing.ends_at,
                priority=max(existing.priority, unlock_mark.priority),
                risk=max(existing.risk, unlock_mark.risk),
                active=existing.active or unlock_mark.active,
                block_new=existing.block_new or unlock_mark.block_new,
                leverage_cap=min(
                    value for value in (existing.leverage_cap, unlock_mark.leverage_cap)
                    if value is not None
                ) if any(value is not None for value in (existing.leverage_cap, unlock_mark.leverage_cap)) else None,
                source_name=existing.source_name,
                source_url=existing.source_url,
            )

    if "BTC" not in symbols:
        return display

    zone = ZoneInfo(timezone_name)
    local_now = now.astimezone(zone)
    post_minutes = max(0, int(config.get("post_event_block_minutes", 10)))
    rows = _eligible_event_rows(events, symbols, now, config).get("BTC", [])
    today_rows = [
        row for row in rows
        if row[0].kind in US_MACRO_KINDS
        and row[0].exact_time
        and (starts := _parse_iso(row[0].starts_at)) is not None
        and starts.astimezone(zone).date() == local_now.date()
        and (row[5] is None or row[5] >= -(post_minutes / 60.0))
    ]
    flow_rows = [
        row for row in rows
        if row[0].kind == "ETF_FLOW" and row[1]
    ]
    if not today_rows and not flow_rows:
        return display

    # Odd minutes stay reserved for the current BTC price. Even minutes carry
    # today's macro appointments and, while fresh, the latest ETF-flow update.
    if local_now.minute % 2 == 1:
        display.pop("BTC", None)
        return display

    today_rows.sort(
        key=lambda row: (
            _parse_iso(row[0].starts_at) or datetime.max.replace(tzinfo=timezone.utc),
            row[0].kind,
            row[0].title,
        )
    )
    flow_rows.sort(
        key=lambda row: _parse_iso(row[0].starts_at)
        or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )

    selected = None
    if flow_rows and today_rows:
        if (local_now.minute // 2) % 2 == 0:
            selected = flow_rows[0]
        else:
            selected = today_rows[(local_now.minute // 4) % len(today_rows)]
    elif flow_rows:
        selected = flow_rows[0]
    elif today_rows:
        selected = today_rows[(local_now.minute // 2) % len(today_rows)]

    if selected is not None:
        display["BTC"] = _mark_from_row(
            selected, now=now, timezone_name=timezone_name
        )
    return display


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
    symbol_aliases = {
        str(key).upper().strip(): str(value).upper().strip()
        for key, value in (config.get("event_symbol_aliases") or {}).items()
        if str(key).strip() and str(value).strip()
    }
    timeout = max(3.0, min(20.0, float(section.get("timeout_seconds", 8.0))))
    timezone_name = str(config.get("timezone", "Europe/Berlin"))
    diagnostics: list[str] = []
    cached = _load_json(cache_path)
    if cached.get("version") != CACHE_VERSION:
        cached = {"version": CACHE_VERSION}
    now_s = int(now.timestamp())

    def cached_events(key: str) -> list[CriticalEvent]:
        raw = cached.get(key) if isinstance(cached.get(key), list) else []
        return [
            event for event in (
                _event_from_dict(item) for item in raw if isinstance(item, Mapping)
            ) if event
        ]

    # Official macro schedules are checked hourly. Each source has its own
    # successful-fetch timestamp, so a temporary failure cannot make another
    # source refresh an old date indefinitely. Failed checks are not retried on
    # every one-minute monitor run.
    schedule_refresh = max(15, int(section.get("schedule_refresh_minutes", 60))) * 60
    max_stale = max(1, int(section.get("schedule_cache_max_stale_hours", 48))) * 3600
    schedule_checked_at = int(cached.get("schedule_checked_at") or 0)
    raw_source_rows = cached.get("schedule_source_events")
    raw_source_rows = raw_source_rows if isinstance(raw_source_rows, Mapping) else {}
    raw_source_times = cached.get("schedule_source_fetched_at")
    raw_source_times = raw_source_times if isinstance(raw_source_times, Mapping) else {}
    schedule_by_source: dict[str, list[CriticalEvent]] = {}
    source_fetched_at: dict[str, int] = {}
    for source_id, rows in raw_source_rows.items():
        if not isinstance(source_id, str) or not isinstance(rows, list):
            continue
        parsed_rows = [
            event for event in (
                _event_from_dict(item) for item in rows if isinstance(item, Mapping)
            ) if event
        ]
        if parsed_rows:
            schedule_by_source[source_id] = parsed_rows
            source_fetched_at[source_id] = int(raw_source_times.get(source_id) or 0)

    next_month = (now.replace(day=28) + timedelta(days=4)).replace(day=1)
    fed_months = {(now.year, now.month), (next_month.year, next_month.month)}
    sources: dict[str, str] = {
        "bls": str(section.get("bls_ics_url", "https://www.bls.gov/schedule/news_release/bls.ics")),
        f"blshtml-{now.year}-{now.month}": f"https://www.bls.gov/schedule/{now.year}/{now.month:02d}_sched_list.htm",
        f"blshtml-{next_month.year}-{next_month.month}": f"https://www.bls.gov/schedule/{next_month.year}/{next_month.month:02d}_sched_list.htm",
        "bea": str(section.get("bea_schedule_url", "https://www.bea.gov/news/schedule")),
        "census": str(section.get("census_schedule_url", "https://www.census.gov/economic-indicators/calendar-listview.html")),
        "ism": str(section.get("ism_schedule_url", "https://www.ismworld.org/supply-management-news-and-reports/reports/rob-report-calendar/")),
        "adp": str(section.get("adp_schedule_url", "https://adpemploymentreport.com/")),
        "fred-adp": str(section.get("fred_adp_schedule_url", "https://fred.stlouisfed.org/releases/calendar?rid=194&view=year")),
        "michigan": str(section.get("michigan_schedule_url", "https://www.sca.isr.umich.edu/")),
        "confidence": str(section.get("consumer_confidence_url", "https://www.conference-board.org/topics/consumer-confidence/")),
        "fomc": str(section.get("fomc_calendar_url", "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm")),
    }
    for year, month in sorted(fed_months):
        sources[f"fedmonth-{year}-{month}"] = (
            f"https://www.federalreserve.gov/newsevents/{year}-{calendar.month_name[month].lower()}.htm"
        )
    # Remove monthly pages once they are no longer current/next month.
    for source_id in list(schedule_by_source):
        if (source_id.startswith("fedmonth-") or source_id.startswith("blshtml-")) and source_id not in sources:
            schedule_by_source.pop(source_id, None)
            source_fetched_at.pop(source_id, None)

    if now_s - schedule_checked_at >= schedule_refresh or schedule_checked_at <= 0:
        fetched: dict[str, str] = {}
        failed: dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=min(5, len(sources))) as pool:
            futures = {pool.submit(_request_text, url, timeout=timeout): name for name, url in sources.items()}
            for future in as_completed(futures):
                name = futures[future]
                try:
                    fetched[name] = future.result()
                except Exception as exc:
                    failed[name] = str(exc)
        for name, value in fetched.items():
            if name == "bls":
                parsed = _parse_bls_ics(value)
            elif name.startswith("blshtml-"):
                parsed = _parse_bls_html_schedule(value)
            elif name == "bea":
                parsed = _parse_bea_schedule(value, now.year)
            elif name == "census":
                parsed = _parse_census_schedule(value)
            elif name == "ism":
                parsed = _parse_ism_schedule(value)
            elif name == "adp":
                parsed = _parse_adp_schedule(value)
            elif name == "fred-adp":
                parsed = _parse_fred_adp_schedule(value)
            elif name == "michigan":
                parsed = _parse_michigan_schedule(value)
            elif name == "confidence":
                parsed = _parse_consumer_confidence_schedule(value, now=now)
            elif name == "fomc":
                parsed = _parse_fomc_calendar(value, (now.year, now.year + 1))
            else:
                _, year_text, month_text = name.split("-")
                parsed = _parse_fed_month_calendar(
                    value, year=int(year_text), month=int(month_text)
                )
            if parsed:
                schedule_by_source[name] = _dedupe(parsed)
                source_fetched_at[name] = now_s
            else:
                diagnostics.append(f"{name}-Kalender gelesen, aber keine passenden Termine erkannt")
        for name, error in failed.items():
            diagnostics.append(f"{name}-Kalender nicht aktualisiert: {error}")
        cached["schedule_checked_at"] = now_s

    # Expire each source independently after the configured stale window.
    for source_id in list(schedule_by_source):
        fetched_at = int(source_fetched_at.get(source_id) or 0)
        if fetched_at <= 0 or now_s - fetched_at > max_stale:
            schedule_by_source.pop(source_id, None)
            source_fetched_at.pop(source_id, None)
            diagnostics.append(f"{source_id}-Termin-Cache zu alt; Quelle ausgeblendet")
    schedule_events = _dedupe([
        *(event for rows in schedule_by_source.values() for event in rows),
        *_scheduled_claims(now),
    ])
    cached["schedule_source_events"] = {
        source_id: [asdict(event) for event in rows]
        for source_id, rows in schedule_by_source.items()
    }
    cached["schedule_source_fetched_at"] = source_fetched_at
    cached["schedule_events"] = [asdict(event) for event in schedule_events]
    cached["schedule_fetched_at"] = max(source_fetched_at.values(), default=0)

    # Coin/project status, release, news and unlock sources are normalized by
    # the central Cloudflare event feed. Keeping this monitor-side loader free
    # of symbol-specific status exceptions makes every configured coin follow
    # the same verified-feed path.

    # Large BTC option expiries are derived hourly from Deribit's official
    # public open-interest endpoint. A label appears only when both the absolute
    # notional and share-of-total thresholds are met.
    derivatives_refresh = max(15, int(section.get("derivatives_refresh_minutes", 60))) * 60
    derivatives_fetched_at = int(cached.get("derivatives_fetched_at") or 0)
    derivatives_checked_at = int(cached.get("derivatives_checked_at") or 0)
    derivative_events = cached_events("derivative_events")
    if now_s - derivatives_checked_at >= derivatives_refresh or derivatives_checked_at <= 0:
        currencies = ["BTC"] if "BTC" in symbols else []
        fresh_derivatives: list[CriticalEvent] = []
        successful_derivatives = 0
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {
                pool.submit(
                    _request_text,
                    f"https://www.deribit.com/api/v2/public/get_book_summary_by_currency?currency={currency}&kind=option",
                    timeout=timeout,
                ): currency for currency in currencies
            }
            for future in as_completed(futures):
                currency = futures[future]
                try:
                    fresh_derivatives.extend(_parse_deribit_expiries(
                        future.result(), currency=currency, now=now, config=section
                    ))
                    successful_derivatives += 1
                except Exception as exc:
                    diagnostics.append(f"Deribit-{currency}-Verfälle nicht aktualisiert: {exc}")
        cached["derivatives_checked_at"] = now_s
        if successful_derivatives:
            derivative_events = _dedupe(fresh_derivatives)
            cached["derivatives_fetched_at"] = now_s
            cached["derivative_events"] = [asdict(event) for event in derivative_events]
        elif derivatives_fetched_at and now_s - derivatives_fetched_at > max_stale:
            derivative_events = []

    allowlist = {
        str(value).lower().strip()
        for value in section.get("verified_source_domains", []) if str(value).strip()
    }
    feed_events: list[CriticalEvent] = []
    if local_feed_path is not None:
        feed_events.extend(
            _verified_feed_events(
                _load_json(local_feed_path),
                allowlist,
                symbols,
                symbol_aliases,
            )
        )
    inline = os.getenv("CRYPTO_EVENTS_JSON", "").strip()
    if inline:
        try:
            feed_events.extend(
                _verified_feed_events(
                    json.loads(inline),
                    allowlist,
                    symbols,
                    symbol_aliases,
                )
            )
        except (ValueError, TypeError, json.JSONDecodeError):
            diagnostics.append("CRYPTO_EVENTS_JSON ist ungültig")

    # The remote verified feed also carries acute security incidents, so it is
    # checked every minute by default. A failed incident feed is retained only
    # briefly; stale "active" alerts must never live for the general 48-hour
    # calendar cache window.
    feed_url = os.getenv("CRYPTO_EVENTS_URL", "").strip() or str(section.get("verified_feed_url", "")).strip()
    remote_feed_events = cached_events("remote_feed_events")
    remote_feed_fetched_at = int(cached.get("remote_feed_fetched_at") or 0)
    remote_feed_checked_at = int(cached.get("remote_feed_checked_at") or 0)
    feed_refresh = max(1, int(section.get("verified_feed_refresh_minutes", 1))) * 60
    feed_max_stale = max(
        1,
        int(section.get("verified_feed_cache_max_stale_minutes", 10)),
    ) * 60
    if feed_url:
        # The feed endpoint is an explicitly configured transport (usually the
        # project's own workers.dev URL). It must be HTTPS, while every event
        # inside it still requires verified=true and an individually allowlisted
        # HTTPS evidence URL. This keeps arbitrary feed contents untrusted.
        try:
            feed_transport = urlparse(feed_url)
        except ValueError:
            feed_transport = None
        if (
            feed_transport is None
            or feed_transport.scheme != "https"
            or not feed_transport.hostname
        ):
            diagnostics.append("Ereignisfeed-URL ist nicht gültig oder nicht HTTPS")
            remote_feed_events = []
        elif now_s - remote_feed_checked_at >= feed_refresh or remote_feed_checked_at <= 0:
            cached["remote_feed_checked_at"] = now_s
            try:
                raw = json.loads(_request_text(feed_url, timeout=timeout))
                remote_feed_events = _verified_feed_events(
                    raw,
                    allowlist,
                    symbols,
                    symbol_aliases,
                )
                cached["remote_feed_fetched_at"] = now_s
                cached["remote_feed_events"] = [asdict(event) for event in remote_feed_events]
            except Exception as exc:
                diagnostics.append(f"Verifizierter Ereignisfeed nicht aktualisiert: {exc}")
                if remote_feed_fetched_at and now_s - remote_feed_fetched_at > feed_max_stale:
                    remote_feed_events = []
    else:
        remote_feed_events = []
    feed_events.extend(remote_feed_events)

    all_events = _dedupe([
        *schedule_events, *derivative_events, *feed_events
    ])
    timing_section = dict(section)
    timing_section["timezone_name"] = timezone_name
    marks = _pick_marks(all_events, symbols, now, timezone_name, timing_section)
    display_marks = _pick_display_marks(
        all_events, symbols, now, timezone_name, timing_section, marks
    )
    cached["version"] = CACHE_VERSION
    cached["updated_at"] = now_s
    try:
        _save_json(cache_path, cached)
    except OSError as exc:
        diagnostics.append(f"Ereignis-Cache nicht gespeichert: {exc}")
    return EventSnapshot(
        marks, all_events, diagnostics, _iso(now) or "", display_marks=display_marks
    )


