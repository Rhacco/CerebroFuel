# r1
"""Verified coin-event collection for the GitHub runner.

The Cloudflare Worker stays a tiny scheduler. Network/status/news/release/unlock
work is performed here, where CPU time is not constrained by Workers Free.
Only evidence URLs from the configured allowlists are accepted later by
``event_context``; this module never creates a trading direction.
"""
from __future__ import annotations

import json
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from html import unescape
from typing import Any, Iterable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

APP_VERSION = "7.1.0"
PACKAGE_REVISION = "r1"
STATE_VERSION = "project-events-v710-r1"
COMPATIBLE_STATE_VERSIONS = {STATE_VERSION, "project-events-v700-r1"}
USER_AGENT = f"crypto-signal-monitor/{APP_VERSION}"

ACTIVE_RETENTION_SECONDS = 25 * 60
STATUS_GRACE_SECONDS = 10 * 60
NEWS_REFRESH_SECONDS = 5 * 60
ETF_REFRESH_SECONDS = 5 * 60
ETF_ACTIVE_RETENTION_SECONDS = 10 * 60
RELEASE_REFRESH_SECONDS = 5 * 60
STATUS_REFRESH_SECONDS = 4 * 60
UNLOCK_REFRESH_SECONDS = 60 * 60
SEEN_RETENTION_SECONDS = 48 * 60 * 60
STATUS_BATCH_SIZE = 4
RELEASE_BATCH_SIZE = 4
UNLOCK_BATCH_SIZE = 4
ETF_SOURCE_URL = "https://farside.co.uk/btc/"

PROJECTS: dict[str, dict[str, Any]] = {
    "BTC": {"domains": ["bitcoin.org", "bitcoincore.org"], "github": ["bitcoin/bitcoin"]},
    "KAITO": {"domains": ["kaito.ai"], "unlock_slug": "kaito"},
    "PENGU": {"domains": ["pudgypenguins.com"]},
    "FARTCOIN": {"domains": ["fart.dev"]},
    "LDO": {"domains": ["lido.fi"]},
    "ZEC": {"domains": ["z.cash", "electriccoin.co"], "github": ["zcash/zcash"]},
    "WLD": {"domains": ["world.org"], "unlock_slug": "worldcoin-wld"},
    "PUMP": {"domains": ["pump.fun"]},
    "SUI": {"domains": ["sui.io"], "github": ["MystenLabs/sui"], "unlock_slug": "sui"},
    "ENA": {"domains": ["ethena.fi", "ethenafoundation.com"], "unlock_slug": "ethena"},
    "ETH": {"domains": ["ethereum.org", "ethereum.foundation"], "github": ["ethereum/go-ethereum"]},
    "SOL": {"domains": ["solana.com"], "github": ["anza-xyz/agave"]},
    "HYPE": {"domains": ["hyperfoundation.org", "hyperliquid.xyz"], "unlock_slug": "hyperliquid"},
    "XRP": {"domains": ["xrpl.org", "ripple.com"], "github": ["XRPLF/rippled"]},
    "XPL": {"domains": ["plasma.org", "plasma.to"], "unlock_slug": "plasma"},
    "ONDO": {"domains": ["ondo.finance"], "unlock_slug": "ondo-finance"},
    "ADA": {"domains": ["cardano.org", "essentialcardano.io", "iog.io", "iohk.io"], "github": ["IntersectMBO/cardano-node"]},
    "TAO": {"domains": ["bittensor.com"], "github": ["opentensor/bittensor"]},
    "JUP": {"domains": ["jup.ag"]},
    "NEAR": {"domains": ["near.org", "nearfoundation.org"], "github": ["near/nearcore"]},
    "AVAX": {"domains": ["avax.network"], "github": ["ava-labs/avalanchego"], "unlock_slug": "avalanche-2"},
    "UNI": {"domains": ["uniswap.org", "blog.uniswap.org"], "github": ["Uniswap/v4-core"]},
    "APT": {"domains": ["aptosnetwork.com", "aptosfoundation.org", "aptoslabs.com", "aptos.dev"], "github": ["aptos-labs/aptos-core"], "unlock_slug": "aptos"},
    "CASHCAT": {"domains": ["cashcat.cc", "cashcattoken.xyz"]},
}

STATUSPAGE_SOURCES = (
    ("SOL", "https://status.solana.com/api/v2/incidents/unresolved.json", "Solana Status", "incidents"),
    ("SOL", "https://status.solana.com/api/v2/scheduled-maintenances/upcoming.json", "Solana Status", "scheduled_maintenances"),
    ("HYPE", "https://hyperliquid.statuspage.io/api/v2/incidents/unresolved.json", "Hyperliquid Status", "incidents"),
    ("HYPE", "https://hyperliquid.statuspage.io/api/v2/scheduled-maintenances/upcoming.json", "Hyperliquid Status", "scheduled_maintenances"),
    ("SUI", "https://status.sui.io/api/v2/incidents/unresolved.json", "Sui Status", "incidents"),
    ("SUI", "https://status.sui.io/api/v2/scheduled-maintenances/upcoming.json", "Sui Status", "scheduled_maintenances"),
    ("AVAX", "https://status.avax.network/api/v2/incidents/unresolved.json", "Avalanche Status", "incidents"),
    ("AVAX", "https://status.avax.network/api/v2/scheduled-maintenances/upcoming.json", "Avalanche Status", "scheduled_maintenances"),
)
HTML_STATUS_SOURCES = (
    ("JUP", "https://status.jup.ag/", "All services are online", "Jupiter Status"),
    ("NEAR", "https://status.near.org/", "No problems detected", "NEAR Status"),
)
OFFICIAL_FEEDS = (
    ("SOL", "https://solana.com/changelog/rss.xml", "Solana Changelog"),
)

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}

EVENT_TERMS = (
    "hack", "exploit", "security", "vulnerability", "outage", "degraded", "halt",
    "upgrade", "hardfork", "mainnet", "maintenance", "governance", "proposal", "vote",
    "unlock", "vesting", "buyback", "burn", "mint", "tokenomics", "supply", "ETF",
    "airdrop", "listing", "delisting", "acquisition", "treasury", "lawsuit",
    "legal settlement", "regulatory", "regulatory license", "staking", "validator",
    "migration", "token sale", "emission", "bridge", "oracle", "emergency", "incident",
    "pause", "token distribution",
)


def collect_project_event_feed(
    *,
    now: datetime,
    symbols: Iterable[str],
    state: Mapping[str, Any] | None,
    timeout: float = 8.0,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """Return verified project feed, updated persistent state and diagnostics."""
    current_symbols = {str(value).upper().strip() for value in symbols if str(value).strip()}
    current_symbols &= set(PROJECTS)
    now = now.astimezone(timezone.utc)
    now_s = int(now.timestamp())
    now_iso = now.isoformat()
    old = (
        dict(state)
        if isinstance(state, Mapping) and state.get("version") in COMPATIBLE_STATE_VERSIONS
        else {}
    )
    source_last_ok = _clean_timestamp_map(old.get("source_last_ok"))
    source_last_attempt = _clean_timestamp_map(old.get("source_last_attempt"))
    diagnostics: list[str] = []
    fresh: list[dict[str, Any]] = []

    def last_ok(source_id: str) -> int:
        return int(source_last_ok.get(source_id) or 0)

    def last_attempt(source_id: str) -> int:
        return int(source_last_attempt.get(source_id) or 0)

    def due(source_id: str, interval: int) -> bool:
        success = last_ok(source_id)
        if success and now_s - success < interval:
            return False
        # A failed source is retried on the next monitor run, but never more
        # than once inside the same minute if duplicate runs overlap briefly.
        attempt = last_attempt(source_id)
        return not attempt or now_s - attempt >= 45

    def mark_attempt(source_id: str) -> None:
        source_last_attempt[source_id] = now_s

    def mark_ok(source_id: str) -> None:
        source_last_ok[source_id] = now_s

    status_sources: list[dict[str, Any]] = []
    for row in STATUSPAGE_SOURCES:
        if row[0] in current_symbols:
            status_sources.append({"type": "statuspage", "row": row, "id": f"status:{row[0]}:{row[1]}"})
    for row in HTML_STATUS_SOURCES:
        if row[0] in current_symbols:
            status_sources.append({"type": "html", "row": row, "id": f"status:{row[0]}:{row[1]}"})
    status_due = sorted(
        (source for source in status_sources if due(source["id"], STATUS_REFRESH_SECONDS)),
        key=lambda source: last_ok(source["id"]),
    )[:STATUS_BATCH_SIZE]
    for source in status_due:
        mark_attempt(source["id"])
    status_results = _parallel(
        status_due,
        lambda source: _fetch_status_source(source, now, timeout),
        max_workers=STATUS_BATCH_SIZE,
    )
    for source, value, error in status_results:
        if error is None:
            mark_ok(source["id"])
            fresh.extend(value)
        else:
            diagnostics.append(f"status {source['row'][0]}: {_short_error(error)}")

    if "XRP" in current_symbols:
        escrow = _scheduled_xrp_escrow(now)
        if escrow:
            fresh.append(escrow)

    gdelt_batches = [
        batch for batch in _build_gdelt_queries(current_symbols)
        if any(due(f"news:{symbol}", NEWS_REFRESH_SECONDS) for symbol in batch["symbols"])
    ]
    for batch in gdelt_batches:
        for symbol in batch["symbols"]:
            mark_attempt(f"news:{symbol}")
    gdelt_results = _parallel(
        gdelt_batches,
        lambda batch: _fetch_gdelt_batch(batch, now, timeout),
        max_workers=min(6, max(1, len(gdelt_batches))),
    )
    for batch, value, error in gdelt_results:
        if error is None:
            for symbol in batch["symbols"]:
                mark_ok(f"news:{symbol}")
            fresh.extend(value)
        else:
            diagnostics.append(f"news {','.join(batch['symbols'])}: {_short_error(error)}")

    feed_sources = [
        {"row": row, "id": f"feed:{row[0]}:{row[1]}"}
        for row in OFFICIAL_FEEDS
        if row[0] in current_symbols and due(f"feed:{row[0]}:{row[1]}", NEWS_REFRESH_SECONDS)
    ]
    for source in feed_sources:
        mark_attempt(source["id"])
    feed_results = _parallel(
        feed_sources,
        lambda source: _fetch_official_feed(source["row"], now, timeout),
        max_workers=min(2, max(1, len(feed_sources))),
    )
    for source, value, error in feed_results:
        if error is None:
            mark_ok(source["id"])
            fresh.extend(value)
        else:
            diagnostics.append(f"feed {source['row'][0]}: {_short_error(error)}")

    etf_last_date = str(old.get("etf_last_date") or "")
    try:
        etf_last_total = float(old["etf_last_total_m"]) if old.get("etf_last_total_m") is not None else None
    except (TypeError, ValueError):
        etf_last_total = None
    if "BTC" in current_symbols and due("etf:BTC", ETF_REFRESH_SECONDS):
        mark_attempt("etf:BTC")
        try:
            row = _fetch_bitcoin_etf_flow(now, timeout)
            mark_ok("etf:BTC")
            if row:
                has_baseline = bool(etf_last_date) and etf_last_total is not None
                changed = row["date"] != etf_last_date or round(row["total_m"]) != round(etf_last_total)
                if has_baseline and changed and abs(round(row["total_m"])) >= 1:
                    rounded = round(row["total_m"])
                    signed = f"{'+' if rounded >= 0 else '-'}{abs(rounded)}M"
                    fresh.append(_event_row(
                        symbol="BTC", kind="ETF_FLOW", title=f"US spot Bitcoin ETF net flow {signed}",
                        starts_at=now_iso,
                        ends_at=datetime.fromtimestamp(now_s + ETF_ACTIVE_RETENTION_SECONDS, timezone.utc).isoformat(),
                        expires_at=datetime.fromtimestamp(now_s + ETF_ACTIVE_RETENTION_SECONDS, timezone.utc).isoformat(),
                        exact_time=True, priority=91,
                        source_name="Farside Investors Bitcoin ETF Flow", source_url=ETF_SOURCE_URL,
                        active=True, source_type="etf_flow", impact_score=_etf_flow_impact_score(row["total_m"]),
                    ))
                etf_last_date = row["date"]
                etf_last_total = row["total_m"]
        except Exception as exc:  # noqa: BLE001 - diagnostics must retain partial feed
            diagnostics.append(f"etf: {_short_error(exc)}")

    release_sources: list[dict[str, str]] = []
    for symbol in sorted(current_symbols):
        for repo in PROJECTS[symbol].get("github", []):
            source_id = f"release:{symbol}:{repo}"
            if due(source_id, RELEASE_REFRESH_SECONDS):
                release_sources.append({"symbol": symbol, "repo": repo, "id": source_id})
    release_due = sorted(release_sources, key=lambda source: last_ok(source["id"]))[:RELEASE_BATCH_SIZE]
    for source in release_due:
        mark_attempt(source["id"])
    release_results = _parallel(
        release_due,
        lambda source: _fetch_github_releases(source["symbol"], source["repo"], now, timeout),
        max_workers=RELEASE_BATCH_SIZE,
    )
    for source, value, error in release_results:
        if error is None:
            mark_ok(source["id"])
            fresh.extend(value)
        else:
            diagnostics.append(f"release {source['symbol']}: {_short_error(error)}")

    unlock_sources: list[dict[str, str]] = []
    for symbol in sorted(current_symbols):
        slug = PROJECTS[symbol].get("unlock_slug")
        if not slug:
            continue
        source_id = f"unlock:{symbol}:{slug}"
        if due(source_id, UNLOCK_REFRESH_SECONDS):
            unlock_sources.append({"symbol": symbol, "slug": slug, "id": source_id})
    unlock_due = sorted(unlock_sources, key=lambda source: last_ok(source["id"]))[:UNLOCK_BATCH_SIZE]
    for source in unlock_due:
        mark_attempt(source["id"])
    unlock_results = _parallel(
        unlock_due,
        lambda source: _fetch_tokenomist_unlock(source["symbol"], source["slug"], now, timeout),
        max_workers=UNLOCK_BATCH_SIZE,
    )
    for source, value, error in unlock_results:
        if error is None:
            mark_ok(source["id"])
            if value:
                fresh.append(value)
        else:
            diagnostics.append(f"unlock {source['symbol']}: {_short_error(error)}")

    merged_events, seen = _merge_events(
        old_events=old.get("events"),
        old_seen=old.get("seen_events"),
        fresh=fresh,
        now=now,
    )
    source_health = _build_source_health(
        symbols=current_symbols,
        source_last_ok=source_last_ok,
        source_last_attempt=source_last_attempt,
        now_s=now_s,
    )
    new_state = {
        "version": STATE_VERSION,
        "updated_at": now_s,
        "source_last_ok": source_last_ok,
        "source_last_attempt": source_last_attempt,
        "etf_last_date": etf_last_date or None,
        "etf_last_total_m": etf_last_total,
        "events": merged_events,
        "seen_events": seen,
        "source_health": source_health,
    }
    feed = {
        "version": APP_VERSION,
        "package_revision": PACKAGE_REVISION,
        "generated_at": now_iso,
        "events": merged_events,
        "meta": {"source_health": source_health},
    }
    return feed, new_state, diagnostics[:32]


def _parallel(items: list[Any], fn: Any, *, max_workers: int) -> list[tuple[Any, Any, Exception | None]]:
    if not items:
        return []
    results: list[tuple[Any, Any, Exception | None]] = []
    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(items)))) as pool:
        futures = {pool.submit(fn, item): item for item in items}
        for future in as_completed(futures):
            item = futures[future]
            try:
                results.append((item, future.result(), None))
            except Exception as exc:  # noqa: BLE001 - caller records per-source diagnostics
                results.append((item, None, exc))
    return results


def _fetch_status_source(source: Mapping[str, Any], now: datetime, timeout: float) -> list[dict[str, Any]]:
    row = source["row"]
    if source["type"] == "statuspage":
        return _fetch_statuspage(row[0], row[1], row[2], row[3], now, timeout)
    return _fetch_html_status(row[0], row[1], row[2], row[3], now, timeout)


def _fetch_statuspage(symbol: str, url: str, source_name: str, collection: str, now: datetime, timeout: float) -> list[dict[str, Any]]:
    payload = json.loads(_fetch_text(url, timeout))
    rows = payload.get(collection) if isinstance(payload, Mapping) else []
    rows = rows if isinstance(rows, list) else []
    events: list[dict[str, Any]] = []
    for incident in rows:
        if not isinstance(incident, Mapping):
            continue
        status = str(incident.get("status") or "").lower()
        if status in {"resolved", "completed"}:
            continue
        title = _clean_text(incident.get("name") or "Network incident")
        scheduled = collection == "scheduled_maintenances" or status == "scheduled" or bool(re.search(r"maintenance", title, re.I))
        starts_at = _parse_date_value(incident.get("scheduled_for") or incident.get("started_at") or incident.get("created_at") or now.isoformat())
        ends_at = _parse_date_value(incident.get("scheduled_until"))
        started = bool(starts_at and datetime.fromisoformat(starts_at).timestamp() <= now.timestamp())
        events.append(_event_row(
            symbol=symbol,
            kind="MAINTENANCE" if scheduled else "NETWORK",
            title=title,
            starts_at=starts_at,
            ends_at=ends_at,
            exact_time=True,
            priority=84 if scheduled else 100,
            source_name=source_name,
            source_url=re.sub(r"/api/v2/(?:incidents/unresolved|scheduled-maintenances/upcoming)\.json$", "/", url),
            active=(status != "scheduled" or started) if scheduled else True,
            source_type="status",
        ))
    return events


def _fetch_html_status(symbol: str, url: str, healthy_marker: str, source_name: str, now: datetime, timeout: float) -> list[dict[str, Any]]:
    flat = _clean_text(_strip_html(_fetch_text(url, timeout)))
    if healthy_marker.lower() in flat.lower():
        return []
    if not re.search(r"degraded|outage|incident|disruption|down|halt|stalled|critical", flat, re.I):
        return []
    return [_event_row(
        symbol=symbol,
        kind="NETWORK",
        title=f"{source_name} meldet eine Störung",
        starts_at=now.isoformat(),
        exact_time=True,
        priority=100,
        source_name=source_name,
        source_url=url,
        active=True,
        source_type="status",
    )]


def _build_gdelt_queries(symbols: set[str]) -> list[dict[str, Any]]:
    ordered = [symbol for symbol in PROJECTS if symbol in symbols]
    batches: list[dict[str, Any]] = []
    for index in range(0, len(ordered), 4):
        batch_symbols = ordered[index:index + 4]
        domains = [domain for symbol in batch_symbols for domain in PROJECTS[symbol].get("domains", [])]
        batches.append({"symbols": batch_symbols, "domains": domains})
    return batches


def _fetch_gdelt_batch(batch: Mapping[str, Any], now: datetime, timeout: float) -> list[dict[str, Any]]:
    domain_query = " OR ".join(f"domainis:{domain}" for domain in batch["domains"])
    event_query = " OR ".join(_quote_gdelt(term) for term in EVENT_TERMS)
    query = f"({domain_query}) AND ({event_query})"
    params = urlencode({
        "query": query,
        "mode": "artlist",
        "maxrecords": "40",
        "format": "json",
        "sort": "datedesc",
        "timespan": "1d",
    })
    payload = json.loads(_fetch_text(f"https://api.gdeltproject.org/api/v2/doc/doc?{params}", timeout))
    articles = payload.get("articles") if isinstance(payload, Mapping) else []
    articles = articles if isinstance(articles, list) else []
    result: list[dict[str, Any]] = []
    for article in articles:
        if not isinstance(article, Mapping):
            continue
        source_url = str(article.get("url") or "")
        symbol = _symbol_for_official_url(source_url, batch["symbols"])
        if not symbol:
            continue
        title = _clean_text(article.get("title") or "")
        classification = _classify_headline(title)
        if not classification:
            continue
        seen_at = _parse_gdelt_date(article.get("seendate")) or now.isoformat()
        result.append(_event_row(
            symbol=symbol,
            kind=classification[0],
            title=title,
            starts_at=seen_at,
            exact_time=True,
            priority=classification[1],
            source_name=_hostname(source_url),
            source_url=source_url,
            active=True,
            source_type="news",
        ))
    return result


def _fetch_official_feed(row: tuple[str, str, str], now: datetime, timeout: float) -> list[dict[str, Any]]:
    symbol, url, source_name = row
    text = _fetch_text(url, timeout)
    blocks = [match.group(1) for match in re.finditer(r"<item(?:\s[^>]*)?>([\s\S]*?)</item>", text, re.I)]
    blocks.extend(match.group(1) for match in re.finditer(r"<entry(?:\s[^>]*)?>([\s\S]*?)</entry>", text, re.I))
    result: list[dict[str, Any]] = []
    for block in blocks[:8]:
        title = _clean_text(_decode_xml(_strip_html(_capture(block, r"<title[^>]*>([\s\S]*?)</title>"))))
        summary = _clean_text(_decode_xml(_strip_html(_capture(block, r"<(?:description|summary|content)(?:\s[^>]*)?>([\s\S]*?)</(?:description|summary|content)>"))))
        published = _parse_date_value(_capture(block, r"<(?:pubDate|published|updated)>([^<]+)</(?:pubDate|published|updated)>") or None)
        link = _decode_xml(
            _capture(block, r"<link[^>]+href=[\"']([^\"']+)[\"']")
            or _capture(block, r"<link(?:\s[^>]*)?>([^<]+)</link>")
            or url
        )
        if not title or not published:
            continue
        age = now.timestamp() - datetime.fromisoformat(published).timestamp()
        if age < -3600 or age > 86400:
            continue
        classification = _classify_headline(f"{title} {summary}")
        if not classification:
            continue
        result.append(_event_row(
            symbol=symbol,
            kind=classification[0],
            title=title,
            starts_at=published,
            exact_time=True,
            priority=classification[1],
            source_name=source_name,
            source_url=link,
            active=True,
            source_type="official_feed",
        ))
    return result


def _classify_headline(title: str) -> tuple[str, int] | None:
    text = str(title or "").lower()
    if not text:
        return None
    retrospective = bool(re.search(r"resolved|resolution|postmortem|post-mortem|incident report|root cause analysis|retrospective", text, re.I))
    security = bool(re.search(r"hack|exploit|security incident|critical(?: security)? vulnerability|security vulnerability.*critical|breach|compromis|under attack|attack detected|zero[- ]day|cve-\d{4}-\d+", text, re.I))
    vulnerability = bool(re.search(r"\bvulnerabilit(?:y|ies)\b", text, re.I))
    network = bool(re.search(r"outage|network halt|chain halt|stalled|downtime|degraded|network disruption|consensus issue", text, re.I))
    if retrospective and (security or network):
        return "NEWS", 78
    if security:
        return "SECURITY", 100
    if network:
        return "NETWORK", 100
    if vulnerability:
        return "NEWS", 88
    if re.search(r"etf|sec filing|regulatory approval|regulatory decision", text, re.I):
        return "ETF", 92
    if re.search(r"lawsuit|legal settlement|regulatory|regulator|regulatory license|court ruling|legal action", text, re.I):
        return "NEWS", 88
    if re.search(r"maintenance|scheduled downtime", text, re.I) and re.search(r"downtime|halt|pause|unavailable|degraded|network|chain|trading|withdraw|deposit", text, re.I):
        return "MAINTENANCE", 84
    governance = bool(re.search(r"governance|proposal|referendum|community vote|onchain vote", text, re.I))
    governance_impact = bool(re.search(r"fee|emission|supply|treasury|token|staking|validator|incentive|reward|burn|mint|upgrade|migration|slashing", text, re.I))
    if governance and governance_impact:
        return "GOVERNANCE", 86
    if re.search(r"hard fork|hardfork|mainnet launch|protocol upgrade|security patch|major migration|network upgrade|chain upgrade|protocol migration|deprecat", text, re.I):
        return "UPGRADE", 88
    if re.search(r"unlock|vesting|cliff release|buyback|token burn|burn program|mint|emission|tokenomics|supply change|airdrop|token distribution", text, re.I):
        return "NEWS", 82
    if re.search(r"staking|validator", text, re.I) and re.search(r"launch|change|reward|slashing|commission|requirement|migration|enable|disable|deprecat", text, re.I):
        return "NEWS", 80
    if re.search(r"listing|delisting|acquisition|treasury allocation|treasury purchase|token launch|token sale|migration", text, re.I):
        return "NEWS", 78
    return None


def _fetch_github_releases(symbol: str, repo: str, now: datetime, timeout: float) -> list[dict[str, Any]]:
    url = f"https://github.com/{repo}/releases.atom"
    text = _fetch_text(url, timeout)
    entries = [match.group(1) for match in re.finditer(r"<entry>([\s\S]*?)</entry>", text, re.I)][:3]
    result: list[dict[str, Any]] = []
    for block in entries:
        title = _clean_text(_decode_xml(_capture(block, r"<title[^>]*>([\s\S]*?)</title>")))
        summary = _clean_text(_decode_xml(_strip_html(_capture(block, r"<(?:content|summary)(?:\s[^>]*)?>([\s\S]*?)</(?:content|summary)>"))))
        updated = _parse_date_value(_capture(block, r"<updated>([^<]+)</updated>") or None)
        link = _capture(block, r"<link[^>]+href=\"([^\"]+)\"") or url
        if not title or not updated:
            continue
        if now.timestamp() - datetime.fromisoformat(updated).timestamp() > 86400:
            continue
        classification = _classify_headline(f"{title} {summary}")
        if not classification:
            continue
        result.append(_event_row(
            symbol=symbol,
            kind=classification[0],
            title=title,
            starts_at=updated,
            exact_time=True,
            priority=classification[1],
            source_name=f"GitHub {repo}",
            source_url=link,
            active=True,
            source_type="release",
        ))
    return result


def _etf_flow_impact_score(total_m: float) -> int:
    amount = abs(float(total_m))
    return max(10, min(90, round(15 + 75 * (1 - math.exp(-amount / 600)))))


def _fetch_bitcoin_etf_flow(now: datetime, timeout: float) -> dict[str, Any] | None:
    row = _parse_farside_bitcoin_etf_flow(_fetch_text(ETF_SOURCE_URL, timeout))
    if not row:
        raise RuntimeError("Farside BTC ETF row not recognized")
    row_time = datetime.fromisoformat(f"{row['date']}T00:00:00+00:00").timestamp()
    if now.timestamp() - row_time > 7 * 86400:
        raise RuntimeError("Farside BTC ETF row is stale")
    return row


def _parse_farside_bitcoin_etf_flow(text: str) -> dict[str, Any] | None:
    parsed: list[dict[str, Any]] = []
    for row_match in re.finditer(r"<tr\b[^>]*>([\s\S]*?)</tr>", str(text or ""), re.I):
        cells = [
            _clean_text(_strip_html(_decode_xml(cell.group(1))))
            for cell in re.finditer(r"<(?:td|th)\b[^>]*>([\s\S]*?)</(?:td|th)>", row_match.group(1), re.I)
        ]
        if len(cells) < 2:
            continue
        date = _parse_farside_date(cells[0])
        total_m = _parse_farside_number(cells[-1])
        if date and total_m is not None:
            parsed.append({"date": date, "total_m": total_m})
    if not parsed:
        for row in str(text or "").splitlines():
            date_match = re.search(r"\b(\d{1,2})\s+([A-Za-z]{3,9})\s+(20\d{2})\b", row)
            if not date_match or "|" not in row:
                continue
            date = _parse_farside_date(date_match.group(0))
            total_m = _parse_farside_number(row.rsplit("|", 1)[-1])
            if date and total_m is not None:
                parsed.append({"date": date, "total_m": total_m})
    parsed.sort(key=lambda row: row["date"])
    return parsed[-1] if parsed else None


def _parse_farside_date(value: str) -> str | None:
    match = re.fullmatch(r"\s*(\d{1,2})\s+([A-Za-z]{3,9})\s+(20\d{2})\s*", _clean_text(value))
    if not match:
        return None
    month_text = match.group(2).lower()
    month = MONTHS.get(month_text)
    if month is None:
        month = next((number for name, number in MONTHS.items() if name.startswith(month_text[:3])), None)
    if month is None:
        return None
    try:
        return datetime(int(match.group(3)), month, int(match.group(1)), tzinfo=timezone.utc).date().isoformat()
    except ValueError:
        return None


def _parse_farside_number(value: str) -> float | None:
    text = _clean_text(value).replace("$", "").replace(",", "")
    if not text or text == "-" or re.fullmatch(r"n/?a", text, re.I):
        return None
    negative_parentheses = text.startswith("(") and text.endswith(")")
    if negative_parentheses:
        text = text[1:-1].strip()
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", text):
        return None
    number = float(text)
    return -abs(number) if negative_parentheses else number


def _scheduled_xrp_escrow(now: datetime) -> dict[str, Any] | None:
    current = now.date()
    release = datetime(current.year, current.month, 1, tzinfo=timezone.utc)
    if current.day > 1:
        year = current.year + (1 if current.month == 12 else 0)
        month = 1 if current.month == 12 else current.month + 1
        release = datetime(year, month, 1, tzinfo=timezone.utc)
    if release > datetime(2028, 9, 1, tzinfo=timezone.utc):
        return None
    days = (release.date() - current).days
    if days < 0 or days > 14:
        return None
    return _event_row(
        symbol="XRP",
        kind="UNLOCK",
        title="XRP monthly Ripple escrow release window",
        starts_at=release.isoformat(),
        exact_time=False,
        priority=94,
        source_name="Ripple XRP Markets Report",
        source_url="https://ripple.com/insights/q1-2025-xrp-markets-report/",
        active=False,
        source_type="unlock",
    )


def _fetch_tokenomist_unlock(symbol: str, slug: str, now: datetime, timeout: float) -> dict[str, Any] | None:
    url = f"https://tokenomist.ai/{slug}/unlock-events"
    flat = _clean_text(_strip_html(_decode_xml(_fetch_text(url, timeout))))
    if re.search(r"is fully unlocked", flat, re.I):
        return None
    match = re.search(r"next unlock for [^.]{1,120}? is scheduled for ([A-Za-z]+\s+\d{1,2},\s+20\d{2})", flat, re.I)
    if not match:
        match = re.search(r"next unlock[^.]{0,80}? scheduled for ([A-Za-z]+\s+\d{1,2},\s+20\d{2})", flat, re.I)
    if not match:
        return None
    release = _parse_english_date(match.group(1))
    if not release:
        return None
    days = (release.date() - now.date()).days
    if days < 0 or days > 14:
        return None
    return _event_row(
        symbol=symbol,
        kind="UNLOCK",
        title=f"{symbol} token unlock",
        starts_at=release.isoformat(),
        exact_time=False,
        priority=94,
        source_name="Tokenomist public unlock calendar",
        source_url=url,
        active=False,
        source_type="unlock",
    )


def _event_row(
    *,
    symbol: str,
    kind: str,
    title: str,
    starts_at: str | None = None,
    ends_at: str | None = None,
    expires_at: str | None = None,
    exact_time: bool = True,
    priority: int = 70,
    source_name: str,
    source_url: str,
    active: bool = False,
    source_type: str,
    impact_score: int | None = None,
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "kind": kind,
        "title": _clean_text(title)[:240],
        "starts_at": starts_at,
        "ends_at": ends_at,
        "expires_at": expires_at,
        "exact_time": bool(exact_time),
        "priority": max(1, min(100, int(priority or 70))),
        "source_name": source_name,
        "source_url": source_url,
        "active": bool(active),
        "impact_score": max(0, min(99, int(impact_score))) if impact_score is not None else None,
        "verified": True,
        "source_type": source_type,
    }


def _merge_events(
    *,
    old_events: Any,
    old_seen: Any,
    fresh: list[dict[str, Any]],
    now: datetime,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    now_s = int(now.timestamp())
    seen: dict[str, int] = {}
    if isinstance(old_seen, Mapping):
        for key, value in old_seen.items():
            try:
                stamp = int(value)
            except (TypeError, ValueError):
                continue
            if stamp > 0 and now_s - stamp < SEEN_RETENTION_SECONDS:
                seen[str(key)] = stamp

    previous = old_events if isinstance(old_events, list) else []
    fresh_keys = {_event_key(event) for event in fresh}
    candidates: list[dict[str, Any]] = []
    for raw in previous:
        if not isinstance(raw, Mapping):
            continue
        event = dict(raw)
        expires = _parse_timestamp(event.get("expires_at"))
        starts = _parse_timestamp(event.get("starts_at"))
        is_future = not bool(event.get("active")) and starts and starts >= now_s - 86400
        retained_status = event.get("source_type") == "status" and expires and expires > now_s and _event_key(event) not in fresh_keys
        retained_active = bool(event.get("active")) and event.get("source_type") != "status" and expires and expires > now_s
        if is_future or retained_status or retained_active:
            candidates.append(event)

    for raw in fresh:
        event = dict(raw)
        key = _event_key(event)
        source_type = str(event.get("source_type") or "")
        if source_type in {"news", "release", "official_feed"}:
            first_seen = int(seen.get(key) or 0)
            if first_seen and now_s - first_seen >= ACTIVE_RETENTION_SECONDS:
                continue
            fixed_first = first_seen or now_s
            seen[key] = fixed_first
            event["first_seen_at"] = datetime.fromtimestamp(fixed_first, timezone.utc).isoformat()
            event["expires_at"] = datetime.fromtimestamp(fixed_first + ACTIVE_RETENTION_SECONDS, timezone.utc).isoformat()
        elif source_type == "etf_flow":
            event["first_seen_at"] = event.get("first_seen_at") or now.isoformat()
            event["expires_at"] = event.get("expires_at") or datetime.fromtimestamp(now_s + ETF_ACTIVE_RETENTION_SECONDS, timezone.utc).isoformat()
        elif source_type == "status":
            event["expires_at"] = datetime.fromtimestamp(now_s + STATUS_GRACE_SECONDS, timezone.utc).isoformat()
        candidates.append(event)

    best: dict[str, dict[str, Any]] = {}
    for event in candidates:
        key = _event_key(event)
        old = best.get(key)
        if old is None or int(event.get("priority") or 0) > int(old.get("priority") or 0):
            best[key] = event
    events = []
    for event in best.values():
        expires = _parse_timestamp(event.get("expires_at"))
        if event.get("active") and expires and expires <= now_s:
            continue
        starts = _parse_timestamp(event.get("starts_at"))
        if not event.get("active") and starts and starts < now_s - 86400:
            continue
        events.append(event)
    events.sort(key=lambda event: (
        -int(bool(event.get("active"))),
        _parse_timestamp(event.get("starts_at")) or 2**62,
        -int(event.get("priority") or 0),
    ))
    events = events[:128]
    seen = dict(sorted(seen.items(), key=lambda item: item[1], reverse=True)[:512])
    return events, seen


def _event_key(event: Mapping[str, Any]) -> str:
    return "|".join((
        str(event.get("symbol") or ""),
        str(event.get("kind") or ""),
        str(event.get("starts_at") or ""),
        str(event.get("source_url") or "").lower(),
    ))


def _core_source_ids(symbol: str) -> list[str]:
    ids = [f"news:{symbol}"]
    ids.extend(f"status:{symbol}:{row[1]}" for row in STATUSPAGE_SOURCES if row[0] == symbol)
    ids.extend(f"status:{symbol}:{row[1]}" for row in HTML_STATUS_SOURCES if row[0] == symbol)
    return ids


def _supplemental_source_ids(symbol: str) -> list[str]:
    project = PROJECTS.get(symbol, {})
    ids = [f"feed:{symbol}:{row[1]}" for row in OFFICIAL_FEEDS if row[0] == symbol]
    ids.extend(f"release:{symbol}:{repo}" for repo in project.get("github", []))
    if project.get("unlock_slug"):
        ids.append(f"unlock:{symbol}:{project['unlock_slug']}")
    if symbol == "BTC":
        ids.append("etf:BTC")
    return ids


def _source_freshness_seconds(source_id: str) -> int:
    if source_id.startswith("unlock:"):
        return 90 * 60
    if source_id.startswith("release:"):
        return 15 * 60
    return 12 * 60


def _source_coverage(ids: Iterable[str], source_last_ok: Mapping[str, int], source_last_attempt: Mapping[str, int], now_s: int, *, ignore_unattempted: bool) -> dict[str, Any]:
    missing: list[str] = []
    ok = 0
    total = 0
    for source_id in ids:
        attempted = int(source_last_attempt.get(source_id) or 0)
        if ignore_unattempted and not attempted:
            continue
        total += 1
        stamp = int(source_last_ok.get(source_id) or 0)
        if stamp and now_s - stamp <= _source_freshness_seconds(source_id):
            ok += 1
        else:
            missing.append(":".join(source_id.split(":", 2)[:2]))
    return {
        "ok": ok,
        "total": total,
        "coverage": ok / total if total else 1.0,
        "missing": list(dict.fromkeys(missing))[:8],
    }


def _build_source_health(*, symbols: set[str], source_last_ok: Mapping[str, int], source_last_attempt: Mapping[str, int], now_s: int) -> dict[str, Any]:
    by_symbol: dict[str, Any] = {}
    all_ok = 0
    all_total = 0
    for symbol in sorted(symbols):
        core = _source_coverage(_core_source_ids(symbol), source_last_ok, source_last_attempt, now_s, ignore_unattempted=True)
        supplemental = _source_coverage(_supplemental_source_ids(symbol), source_last_ok, source_last_attempt, now_s, ignore_unattempted=True)
        all_ok += core["ok"]
        all_total += core["total"]
        by_symbol[symbol] = {
            "coverage": core["coverage"],
            "ok": core["ok"],
            "total": core["total"],
            "degraded": core["ok"] < core["total"],
            "missing": core["missing"],
            "supplemental_coverage": supplemental["coverage"],
            "supplemental_ok": supplemental["ok"],
            "supplemental_total": supplemental["total"],
        }
    return {
        "by_symbol": by_symbol,
        "overall_coverage": all_ok / all_total if all_total else 1.0,
        "tracked_sources": all_total,
    }


def _symbol_for_official_url(value: str, allowed_symbols: Iterable[str]) -> str | None:
    try:
        host = (urlparse(value).hostname or "").lower().removeprefix("www.")
    except ValueError:
        return None
    if not host:
        return None
    for symbol in allowed_symbols:
        for domain in PROJECTS.get(symbol, {}).get("domains", []):
            normalized = str(domain).lower().removeprefix("www.")
            if host == normalized or host.endswith("." + normalized):
                return symbol
    return None


def _quote_gdelt(term: str) -> str:
    return f'"{term}"' if re.search(r"\s", term) else term


def _fetch_text(url: str, timeout: float, retries: int = 2) -> str:
    request = Request(
        url,
        headers={
            "Accept": "application/json,application/atom+xml,text/html;q=0.9,*/*;q=0.8",
            "User-Agent": USER_AGENT,
        },
    )
    last: Exception | None = None
    for attempt in range(max(1, retries)):
        try:
            with urlopen(request, timeout=max(3.0, min(20.0, timeout))) as response:
                raw = response.read()
            return raw.decode("utf-8", errors="replace")
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            last = exc
            if attempt + 1 < retries:
                time.sleep(0.25 * (attempt + 1))
    raise RuntimeError(f"Abruf fehlgeschlagen: {url}: {last}")


def _parse_english_date(value: str) -> datetime | None:
    match = re.fullmatch(r"\s*([A-Za-z]+)\s+(\d{1,2}),\s+(20\d{2})\s*", str(value or ""))
    if not match:
        return None
    month = MONTHS.get(match.group(1).lower())
    if month is None:
        return None
    try:
        return datetime(int(match.group(3)), month, int(match.group(2)), tzinfo=timezone.utc)
    except ValueError:
        return None


def _parse_gdelt_date(value: Any) -> str | None:
    match = re.fullmatch(r"(20\d{2})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z", str(value or ""))
    if not match:
        return None
    try:
        return datetime(
            int(match.group(1)), int(match.group(2)), int(match.group(3)),
            int(match.group(4)), int(match.group(5)), int(match.group(6)), tzinfo=timezone.utc,
        ).isoformat()
    except ValueError:
        return None


def _parse_date_value(value: Any) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            from email.utils import parsedate_to_datetime
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError, OverflowError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _parse_timestamp(value: Any) -> int:
    parsed = _parse_date_value(value)
    if not parsed:
        return 0
    try:
        return int(datetime.fromisoformat(parsed).timestamp())
    except ValueError:
        return 0


def _capture(text: str, pattern: str) -> str:
    match = re.search(pattern, str(text or ""), re.I)
    return match.group(1) if match else ""


def _strip_html(value: Any) -> str:
    text = re.sub(r"<script[\s\S]*?</script>", " ", str(value or ""), flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    return re.sub(r"<[^>]+>", " ", text)


def _decode_xml(value: Any) -> str:
    text = re.sub(r"<!\[CDATA\[([\s\S]*?)\]\]>", r"\1", str(value or ""))
    return unescape(text).replace("\xa0", " ")


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _hostname(value: str) -> str:
    try:
        return urlparse(value).hostname or "Official source"
    except ValueError:
        return "Official source"


def _clean_timestamp_map(value: Any) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, int] = {}
    for key, stamp in value.items():
        try:
            number = int(stamp)
        except (TypeError, ValueError):
            continue
        if number > 0:
            result[str(key)] = number
    return result


def _short_error(error: Any) -> str:
    return str(getattr(error, "message", None) or error or "unknown")[:240]
