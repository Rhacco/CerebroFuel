// r2
// Crypto event feed and GitHub scheduler for v7.0.0.

const APP_VERSION = "7.0.0";
const PACKAGE_REVISION = "r2";
const STORE_KEY = "crypto-events-v700-r2";
const CACHE_URL = "https://crypto-events.internal/v7.0.0-r2/events.json";
const LEGACY_STORE_KEY = "crypto-events-v700-r1";
const LEGACY_CACHE_URL = "https://crypto-events.internal/v7.0.0-r1/events.json";
const ACTIVE_RETENTION_MS = 25 * 60 * 1000;
const STATUS_GRACE_MS = 10 * 60 * 1000;
const KV_HEARTBEAT_MS = 10 * 60 * 1000;
const NEWS_REFRESH_MS = 5 * 60 * 1000;
const ETF_REFRESH_MS = 5 * 60 * 1000;
const ETF_ACTIVE_RETENTION_MS = 10 * 60 * 1000;
const ETF_SOURCE_URL = "https://farside.co.uk/btc/";
const UNLOCK_REFRESH_MS = 60 * 60 * 1000;
const UNLOCK_BATCH_SIZE = 4;
const SEEN_RETENTION_MS = 48 * 60 * 60 * 1000;

const PROJECTS = Object.freeze({
  BTC: { domains: ["bitcoin.org", "bitcoincore.org"], github: ["bitcoin/bitcoin"] },
  KAITO: { domains: ["kaito.ai"], unlockSlug: "kaito" },
  PENGU: { domains: ["pudgypenguins.com"] },
  FARTCOIN: { domains: ["fart.dev"] },
  LDO: { domains: ["lido.fi"] },
  ZEC: { domains: ["z.cash", "electriccoin.co"], github: ["zcash/zcash"] },
  WLD: { domains: ["world.org"], unlockSlug: "worldcoin-wld" },
  PUMP: { domains: ["pump.fun"] },
  SUI: { domains: ["sui.io"], github: ["MystenLabs/sui"], unlockSlug: "sui" },
  ENA: { domains: ["ethena.fi", "ethenafoundation.com"], unlockSlug: "ethena" },
  ETH: { domains: ["ethereum.org", "ethereum.foundation"], github: ["ethereum/go-ethereum"] },
  SOL: { domains: ["solana.com"], github: ["anza-xyz/agave"] },
  HYPE: { domains: ["hyperfoundation.org", "hyperliquid.xyz"], unlockSlug: "hyperliquid" },
  XRP: { domains: ["xrpl.org", "ripple.com"], github: ["XRPLF/rippled"] },
  XPL: { domains: ["plasma.to"], unlockSlug: "plasma" },
  ONDO: { domains: ["ondo.finance"], unlockSlug: "ondo-finance" },
  ADA: { domains: ["cardano.org", "essentialcardano.io", "iohk.io"], github: ["IntersectMBO/cardano-node"] },
  TAO: { domains: ["bittensor.com"], github: ["opentensor/bittensor"] },
  JUP: { domains: ["jup.ag"] },
  NEAR: { domains: ["near.org", "nearfoundation.org"], github: ["near/nearcore"] },
  AVAX: { domains: ["avax.network"], github: ["ava-labs/avalanchego"], unlockSlug: "avalanche-2" },
  UNI: { domains: ["uniswap.org", "blog.uniswap.org"], github: ["Uniswap/v4-core"] },
  APT: { domains: ["aptosfoundation.org", "aptoslabs.com", "aptos.dev"], github: ["aptos-labs/aptos-core"], unlockSlug: "aptos" },
});

const STATUSPAGE_SOURCES = Object.freeze([
  ["SOL", "https://status.solana.com/api/v2/incidents/unresolved.json", "Solana Status", "incidents"],
  ["SOL", "https://status.solana.com/api/v2/scheduled-maintenances/upcoming.json", "Solana Status", "scheduled_maintenances"],
  ["HYPE", "https://hyperliquid.statuspage.io/api/v2/incidents/unresolved.json", "Hyperliquid Status", "incidents"],
  ["HYPE", "https://hyperliquid.statuspage.io/api/v2/scheduled-maintenances/upcoming.json", "Hyperliquid Status", "scheduled_maintenances"],
  ["SUI", "https://status.sui.io/api/v2/incidents/unresolved.json", "Sui Status", "incidents"],
  ["SUI", "https://status.sui.io/api/v2/scheduled-maintenances/upcoming.json", "Sui Status", "scheduled_maintenances"],
  ["AVAX", "https://status.avax.network/api/v2/incidents/unresolved.json", "Avalanche Status", "incidents"],
  ["AVAX", "https://status.avax.network/api/v2/scheduled-maintenances/upcoming.json", "Avalanche Status", "scheduled_maintenances"],
]);

const HTML_STATUS_SOURCES = Object.freeze([
  ["JUP", "https://status.jup.ag/", "All services are online", "Jupiter Status"],
  ["NEAR", "https://status.near.org/", "No problems detected", "NEAR Status"],
]);

const OFFICIAL_FEEDS = Object.freeze([
  ["SOL", "https://solana.com/changelog/rss.xml", "Solana Changelog"],
]);

const RELEASE_REFRESH_MS = 5 * 60 * 1000;
const STATUS_REFRESH_MS = 4 * 60 * 1000;
const STATUS_BATCH_SIZE = 4;

const MONTHS = Object.freeze({
  january: 0, february: 1, march: 2, april: 3, may: 4, june: 5,
  july: 6, august: 7, september: 8, october: 9, november: 10, december: 11,
});

export default {
  async scheduled(controller, env, ctx) {
    ctx.waitUntil(runScheduled(env, controller.cron));
  },

  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (url.pathname === "/events.json") {
      let feed = await readStoredFeed(env);
      if (!feed) {
        feed = await refreshEventFeed(env, new Date());
        await writeStoredFeed(env, feed);
      }
      return jsonResponse(feed, 200, { "Cache-Control": "public, max-age=20" });
    }
    if (url.pathname === "/refresh" && request.method === "POST") {
      const auth = refreshAuthorization(request, env);
      if (auth !== 200) return jsonResponse({ ok: false }, auth);
      const feed = await refreshEventFeed(env, new Date());
      await writeStoredFeed(env, feed);
      return jsonResponse({ ok: true, generated_at: feed.generated_at, events: feed.events.length });
    }
    const feed = await readStoredFeed(env);
    return jsonResponse({
      ok: true,
      version: APP_VERSION,
      revision: PACKAGE_REVISION,
      scheduler: schedulerEnabled(env) ? "enabled" : "paused",
      generated_at: feed?.generated_at ?? null,
      events: Array.isArray(feed?.events) ? feed.events.length : 0,
      feed: `${url.origin}/events.json`,
    });
  },
};

async function runScheduled(env, source) {
  const now = new Date();
  const feed = await refreshEventFeed(env, now);
  await writeStoredFeed(env, feed); // Feed must exist before GitHub starts.
  if (!schedulerEnabled(env)) {
    console.log(JSON.stringify({ event: "scheduler-paused", source, feed_events: feed.events.length }));
    return;
  }
  await triggerGitHubWithRetry(env, source, feed);
}

function schedulerEnabled(env) {
  return String(env.ENABLED ?? "1").trim() === "1";
}

function refreshAuthorization(request, env) {
  const expected = String(env.REFRESH_TOKEN || "").trim();
  // Manual refresh is disabled unless an explicit secret was configured.
  if (!expected) return 404;
  const authorization = String(request.headers.get("Authorization") || "");
  return authorization === `Bearer ${expected}` ? 200 : 403;
}

async function refreshEventFeed(env, now) {
  const previous = (await readStoredFeed(env)) || emptyFeed(now);
  const previousMeta = isObject(previous.meta) ? previous.meta : {};
  const previousSameRevision = String(previous.package_revision || "") === PACKAGE_REVISION;
  const sourceLastOk = isObject(previousMeta.source_last_ok) ? { ...previousMeta.source_last_ok } : {};
  const diagnostics = [];
  const fresh = [];
  const nowIso = now.toISOString();
  const nowMs = now.getTime();

  const markOk = (id) => { sourceLastOk[id] = nowIso; };
  const lastOk = (id) => parseMillis(sourceLastOk[id]);
  const due = (id, interval) => !lastOk(id) || nowMs - lastOk(id) >= interval;

  // Status sources are selected by oldest successful check. A failed source
  // therefore stays due and is retried on the next minute instead of being
  // hidden by a rotating batch counter.
  const statusSources = [
    ...STATUSPAGE_SOURCES.map((row) => ({ type: "statuspage", row, id: `status:${row[0]}:${row[1]}` })),
    ...HTML_STATUS_SOURCES.map((row) => ({ type: "html", row, id: `status:${row[0]}:${row[1]}` })),
  ];
  const statusDue = statusSources
    .filter((source) => due(source.id, STATUS_REFRESH_MS))
    .sort((a, b) => (lastOk(a.id) || 0) - (lastOk(b.id) || 0))
    .slice(0, STATUS_BATCH_SIZE);
  const statusResults = await Promise.allSettled(statusDue.map((source) => {
    const row = source.row;
    if (source.type === "statuspage") {
      const [symbol, url, name, collection] = row;
      return fetchStatuspage(symbol, url, name, collection, now);
    }
    const [symbol, url, healthy, name] = row;
    return fetchHtmlStatus(symbol, url, healthy, name, now);
  }));
  let statusOk = 0;
  for (let index = 0; index < statusResults.length; index += 1) {
    const result = statusResults[index];
    const source = statusDue[index];
    if (result.status === "fulfilled") {
      statusOk += 1;
      markOk(source.id);
      fresh.push(...result.value);
    } else diagnostics.push(`status ${source.row[0]}: ${shortError(result.reason)}`);
  }

  const xrpEscrow = scheduledXrpEscrowUnlock(now);
  if (xrpEscrow) fresh.push(xrpEscrow);

  // Every pool coin has the same baseline official-domain news channel. GDELT
  // is only an index/transport: article URLs are still accepted solely when
  // they resolve to a configured official project domain.
  const gdeltDue = buildGdeltQueries().filter((batch) =>
    batch.symbols.some((symbol) => due(`news:${symbol}`, NEWS_REFRESH_MS)),
  );
  const gdeltResults = await Promise.allSettled(gdeltDue.map((batch) => fetchGdeltBatch(batch, now)));
  let newsOk = 0;
  let newsTotal = gdeltResults.length;
  for (let index = 0; index < gdeltResults.length; index += 1) {
    const result = gdeltResults[index];
    const batch = gdeltDue[index];
    if (result.status === "fulfilled") {
      newsOk += 1;
      for (const symbol of batch.symbols) markOk(`news:${symbol}`);
      fresh.push(...result.value);
    } else diagnostics.push(`news ${batch.symbols.join(",")}: ${shortError(result.reason)}`);
  }

  const feedDue = OFFICIAL_FEEDS
    .map((row) => ({ row, id: `feed:${row[0]}:${row[1]}` }))
    .filter((source) => due(source.id, NEWS_REFRESH_MS));
  const feedResults = await Promise.allSettled(
    feedDue.map((source) => fetchOfficialFeed(source.row[0], source.row[1], source.row[2], now)),
  );
  newsTotal += feedResults.length;
  for (let index = 0; index < feedResults.length; index += 1) {
    const result = feedResults[index];
    const source = feedDue[index];
    if (result.status === "fulfilled") {
      newsOk += 1;
      markOk(source.id);
      fresh.push(...result.value);
    } else diagnostics.push(`feed ${source.row[0]}: ${shortError(result.reason)}`);
  }

  let etfLastDate = String(previousMeta.etf_last_date || "");
  let etfLastTotal = Number.isFinite(Number(previousMeta.etf_last_total_m))
    ? Number(previousMeta.etf_last_total_m) : null;
  let etfOk = 0;
  const etfAttempted = due("etf:BTC", ETF_REFRESH_MS);
  if (etfAttempted) {
    try {
      const row = await fetchBitcoinEtfFlow(now);
      etfOk = 1;
      markOk("etf:BTC");
      if (row) {
        const hasBaseline = Boolean(etfLastDate) && etfLastTotal !== null;
        const changed = row.date !== etfLastDate || Math.round(row.totalM) !== Math.round(etfLastTotal);
        if (hasBaseline && changed && Math.abs(Math.round(row.totalM)) >= 1) {
          const rounded = Math.round(row.totalM);
          const signed = `${rounded >= 0 ? "+" : "-"}${Math.abs(rounded)}M`;
          const expiresAt = new Date(nowMs + ETF_ACTIVE_RETENTION_MS).toISOString();
          fresh.push(eventRow({
            symbol: "BTC", kind: "ETF_FLOW", title: `US spot Bitcoin ETF net flow ${signed}`,
            startsAt: nowIso, endsAt: expiresAt, expiresAt, exactTime: true, priority: 91,
            sourceName: "Farside Investors Bitcoin ETF Flow", sourceUrl: ETF_SOURCE_URL,
            active: true, sourceType: "etf_flow",
          }));
        }
        etfLastDate = row.date;
        etfLastTotal = row.totalM;
      }
    } catch (error) {
      diagnostics.push(`etf: ${shortError(error)}`);
    }
  }

  const repoSources = githubSources().map(([symbol, repo]) => ({
    symbol, repo, id: `release:${symbol}:${repo}`,
  }));
  const repoDue = repoSources
    .filter((source) => due(source.id, RELEASE_REFRESH_MS))
    .sort((a, b) => (lastOk(a.id) || 0) - (lastOk(b.id) || 0))
    .slice(0, 4);
  const releaseResults = await Promise.allSettled(
    repoDue.map((source) => fetchGithubReleases(source.symbol, source.repo, now)),
  );
  let releaseOk = 0;
  for (let index = 0; index < releaseResults.length; index += 1) {
    const result = releaseResults[index];
    const source = repoDue[index];
    if (result.status === "fulfilled") {
      releaseOk += 1;
      markOk(source.id);
      fresh.push(...result.value);
    } else diagnostics.push(`release ${source.symbol}: ${shortError(result.reason)}`);
  }

  const unlockSources = Object.entries(PROJECTS)
    .filter(([, project]) => project.unlockSlug)
    .map(([symbol, project]) => ({ symbol, slug: project.unlockSlug, id: `unlock:${symbol}:${project.unlockSlug}` }));
  const unlockDue = unlockSources
    .filter((source) => due(source.id, UNLOCK_REFRESH_MS))
    .sort((a, b) => (lastOk(a.id) || 0) - (lastOk(b.id) || 0))
    .slice(0, UNLOCK_BATCH_SIZE);
  const unlockResults = await Promise.allSettled(
    unlockDue.map((source) => fetchTokenomistUnlock(source.symbol, source.slug, now)),
  );
  let unlockOk = 0;
  for (let index = 0; index < unlockResults.length; index += 1) {
    const result = unlockResults[index];
    const source = unlockDue[index];
    if (result.status === "fulfilled") {
      unlockOk += 1;
      markOk(source.id);
      if (result.value) fresh.push(result.value);
    } else diagnostics.push(`unlock ${source.symbol}: ${shortError(result.reason)}`);
  }

  const merged = mergeEvents(previous, fresh, now);
  const sourceHealth = buildSourceHealth(sourceLastOk, now);
  return {
    version: APP_VERSION,
    package_revision: PACKAGE_REVISION,
    generated_at: nowIso,
    events: merged.events,
    diagnostics: diagnostics.slice(0, 32),
    meta: {
      etf_last_date: etfLastDate || null,
      etf_last_total_m: etfLastTotal,
      kv_written_at: previousSameRevision ? (previousMeta.kv_written_at || null) : null,
      kv_signature: previousSameRevision ? (previousMeta.kv_signature || null) : null,
      source_last_ok: sourceLastOk,
      source_health: {
        ...sourceHealth,
        status_ok: statusOk, status_total: statusResults.length,
        news_ok: newsOk, news_total: newsTotal,
        etf_ok: etfOk, etf_total: etfAttempted ? 1 : 0,
        release_ok: releaseOk, release_total: releaseResults.length,
        unlock_ok: unlockOk, unlock_total: unlockResults.length,
      },
      seen_events: merged.seen,
    },
  };
}

function emptyFeed(now) {
  return {
    version: APP_VERSION,
    package_revision: PACKAGE_REVISION,
    generated_at: now.toISOString(),
    events: [],
    diagnostics: [],
    meta: {
      etf_last_date: null,
      etf_last_total_m: null,
      kv_written_at: null,
      kv_signature: null,
      source_last_ok: {},
      source_health: { by_symbol: {}, overall_coverage: 0 },
      seen_events: {},
    },
  };
}

function expectedSourceIds(symbol) {
  const project = PROJECTS[symbol] || {};
  const ids = [`news:${symbol}`];
  for (const row of STATUSPAGE_SOURCES) if (row[0] === symbol) ids.push(`status:${symbol}:${row[1]}`);
  for (const row of HTML_STATUS_SOURCES) if (row[0] === symbol) ids.push(`status:${symbol}:${row[1]}`);
  for (const row of OFFICIAL_FEEDS) if (row[0] === symbol) ids.push(`feed:${symbol}:${row[1]}`);
  for (const repo of project.github || []) ids.push(`release:${symbol}:${repo}`);
  if (project.unlockSlug) ids.push(`unlock:${symbol}:${project.unlockSlug}`);
  if (symbol === "BTC") ids.push("etf:BTC");
  return ids;
}

function sourceFreshnessMs(id) {
  if (id.startsWith("unlock:")) return 90 * 60 * 1000;
  if (id.startsWith("release:")) return 15 * 60 * 1000;
  if (id.startsWith("status:")) return 12 * 60 * 1000;
  return 12 * 60 * 1000;
}

function buildSourceHealth(sourceLastOk, now) {
  const nowMs = now.getTime();
  const bySymbol = {};
  let allOk = 0;
  let allTotal = 0;
  for (const symbol of Object.keys(PROJECTS)) {
    const expected = expectedSourceIds(symbol);
    const missing = [];
    let ok = 0;
    for (const id of expected) {
      const stamp = parseMillis(sourceLastOk[id]);
      if (stamp && nowMs - stamp <= sourceFreshnessMs(id)) ok += 1;
      else missing.push(id.split(":", 2).join(":"));
    }
    const total = expected.length;
    allOk += ok;
    allTotal += total;
    bySymbol[symbol] = {
      coverage: total ? ok / total : 0,
      ok,
      total,
      degraded: ok < total,
      missing: [...new Set(missing)].slice(0, 8),
    };
  }
  return {
    by_symbol: bySymbol,
    overall_coverage: allTotal ? allOk / allTotal : 0,
    tracked_sources: allTotal,
  };
}

function mergeEvents(previous, fresh, now) {
  const nowMs = now.getTime();
  const seen = {};
  const oldSeen = isObject(previous?.meta?.seen_events) ? previous.meta.seen_events : {};
  for (const [key, value] of Object.entries(oldSeen)) {
    const timestamp = parseMillis(value);
    if (timestamp && nowMs - timestamp < SEEN_RETENTION_MS) seen[key] = new Date(timestamp).toISOString();
  }

  const previousEvents = Array.isArray(previous?.events) ? previous.events : [];
  const freshKeys = new Set(fresh.map(eventKey));
  const candidates = [];

  for (const event of previousEvents) {
    if (!isObject(event)) continue;
    const expires = parseMillis(event.expires_at);
    const starts = parseMillis(event.starts_at);
    const isFuture = !event.active && starts && starts >= nowMs - 24 * 60 * 60 * 1000;
    const retainedStatus = event.source_type === "status" && expires && expires > nowMs && !freshKeys.has(eventKey(event));
    const retainedActive = event.active && event.source_type !== "status" && expires && expires > nowMs;
    if (isFuture || retainedStatus || retainedActive) candidates.push(event);
  }

  for (const event of fresh) {
    if (!isObject(event)) continue;
    const key = eventKey(event);
    if (["news", "release", "official_feed"].includes(event.source_type)) {
      const firstSeen = parseMillis(seen[key]);
      if (firstSeen && nowMs - firstSeen >= ACTIVE_RETENTION_MS) continue;
      const fixedFirst = firstSeen || nowMs;
      seen[key] = new Date(fixedFirst).toISOString();
      event.first_seen_at = new Date(fixedFirst).toISOString();
      event.expires_at = new Date(fixedFirst + ACTIVE_RETENTION_MS).toISOString();
    } else if (event.source_type === "etf_flow") {
      event.first_seen_at = event.first_seen_at || now.toISOString();
      event.expires_at = event.expires_at || new Date(nowMs + ETF_ACTIVE_RETENTION_MS).toISOString();
    } else if (event.source_type === "status") {
      event.expires_at = new Date(nowMs + STATUS_GRACE_MS).toISOString();
    }
    candidates.push(event);
  }

  const best = new Map();
  for (const event of candidates) {
    const key = eventKey(event);
    const old = best.get(key);
    if (!old || Number(event.priority || 0) > Number(old.priority || 0)) best.set(key, event);
  }
  const events = [...best.values()]
    .filter((event) => {
      const expires = parseMillis(event.expires_at);
      if (event.active && expires && expires <= nowMs) return false;
      const starts = parseMillis(event.starts_at);
      if (!event.active && starts && starts < nowMs - 24 * 60 * 60 * 1000) return false;
      return true;
    })
    .sort((a, b) => {
      const active = Number(Boolean(b.active)) - Number(Boolean(a.active));
      if (active) return active;
      const startA = parseMillis(a.starts_at) || Number.MAX_SAFE_INTEGER;
      const startB = parseMillis(b.starts_at) || Number.MAX_SAFE_INTEGER;
      return startA - startB || Number(b.priority || 0) - Number(a.priority || 0);
    })
    .slice(0, 128);

  const seenEntries = Object.entries(seen)
    .sort((a, b) => parseMillis(b[1]) - parseMillis(a[1]))
    .slice(0, 512);
  return { events, seen: Object.fromEntries(seenEntries) };
}

async function fetchStatuspage(symbol, url, sourceName, collection, now) {
  const payload = await fetchJson(url);
  const rows = Array.isArray(payload?.[collection]) ? payload[collection] : [];
  const events = [];
  for (const incident of rows) {
    const status = String(incident?.status || "").toLowerCase();
    if (!isObject(incident) || ["resolved", "completed"].includes(status)) continue;
    const title = cleanText(incident.name || "Network incident");
    const scheduled = collection === "scheduled_maintenances" || status === "scheduled" || /maintenance/i.test(title);
    const startsAt = parseDateValue(incident.scheduled_for || incident.started_at || incident.created_at || now.toISOString());
    const endsAt = parseDateValue(incident.scheduled_until);
    const started = Boolean(startsAt && Date.parse(startsAt) <= now.getTime());
    events.push(eventRow({
      symbol,
      kind: scheduled ? "MAINTENANCE" : "NETWORK",
      title,
      startsAt,
      endsAt,
      exactTime: true,
      priority: scheduled ? 84 : 100,
      sourceName,
      sourceUrl: url.replace(/\/api\/v2\/(?:incidents\/unresolved|scheduled-maintenances\/upcoming)\.json$/, "/"),
      active: scheduled ? (status !== "scheduled" || started) : true,
      sourceType: "status",
    }));
  }
  return events;
}

async function fetchHtmlStatus(symbol, url, healthyMarker, sourceName, now) {
  const text = await fetchText(url);
  const flat = cleanText(stripHtml(text));
  if (flat.toLowerCase().includes(healthyMarker.toLowerCase())) return [];
  if (!/(degraded|outage|incident|disruption|down|halt|stalled|critical)/i.test(flat)) return [];
  return [eventRow({
    symbol,
    kind: "NETWORK",
    title: `${sourceName} meldet eine Störung`,
    startsAt: now.toISOString(),
    exactTime: true,
    priority: 100,
    sourceName,
    sourceUrl: url,
    active: true,
    sourceType: "status",
  })];
}

function buildGdeltQueries() {
  const symbols = Object.keys(PROJECTS);
  const batches = [];
  for (let index = 0; index < symbols.length; index += 4) {
    const batchSymbols = symbols.slice(index, index + 4);
    const domains = batchSymbols.flatMap((symbol) => PROJECTS[symbol].domains || []);
    batches.push({ symbols: batchSymbols, domains });
  }
  return batches;
}

async function fetchGdeltBatch(batch, now) {
  const domainQuery = batch.domains.map((domain) => `domainis:${domain}`).join(" OR ");
  const eventTerms = [
    "hack", "exploit", "security", "vulnerability", "outage", "degraded", "halt",
    "upgrade", "hardfork", "mainnet", "maintenance", "governance", "proposal", "vote",
    "unlock", "vesting", "buyback", "burn", "mint", "tokenomics", "supply", "ETF",
    "airdrop", "listing", "delisting", "acquisition", "treasury", "partnership",
    "integration", "lawsuit", "legal settlement", "regulatory", "regulatory license", "staking",
    "validator", "migration", "token sale", "emission", "bridge", "oracle",
    "emergency", "incident", "pause", "token distribution",
  ];
  const query = `(${domainQuery}) AND (${eventTerms.map(quoteGdelt).join(" OR ")})`;
  const url = new URL("https://api.gdeltproject.org/api/v2/doc/doc");
  url.searchParams.set("query", query);
  url.searchParams.set("mode", "artlist");
  url.searchParams.set("maxrecords", "40");
  url.searchParams.set("format", "json");
  url.searchParams.set("sort", "datedesc");
  url.searchParams.set("timespan", "1d");
  const payload = await fetchJson(url.toString());
  const articles = Array.isArray(payload?.articles) ? payload.articles : [];
  const result = [];
  for (const article of articles) {
    const sourceUrl = String(article?.url || "");
    const symbol = symbolForOfficialUrl(sourceUrl, batch.symbols);
    if (!symbol) continue;
    const title = cleanText(article?.title || "");
    const classification = classifyHeadline(title);
    if (!classification) continue;
    const seenAt = parseGdeltDate(article?.seendate) || now.toISOString();
    result.push(eventRow({
      symbol,
      kind: classification.kind,
      title,
      startsAt: seenAt,
      exactTime: true,
      priority: classification.priority,
      sourceName: hostname(sourceUrl),
      sourceUrl,
      active: true,
      sourceType: "news",
    }));
  }
  return result;
}


async function fetchOfficialFeed(symbol, url, sourceName, now) {
  const text = await fetchText(url);
  const blocks = [
    ...[...text.matchAll(/<item(?:\s[^>]*)?>([\s\S]*?)<\/item>/gi)].map((match) => match[1]),
    ...[...text.matchAll(/<entry(?:\s[^>]*)?>([\s\S]*?)<\/entry>/gi)].map((match) => match[1]),
  ].slice(0, 8);
  const result = [];
  for (const block of blocks) {
    const title = cleanText(decodeXml(stripHtml(capture(block, /<title[^>]*>([\s\S]*?)<\/title>/i))));
    const summary = cleanText(decodeXml(stripHtml(
      capture(block, /<(?:description|summary|content)(?:\s[^>]*)?>([\s\S]*?)<\/(?:description|summary|content)>/i),
    )));
    const published = parseDateValue(
      capture(block, /<(?:pubDate|published|updated)>([^<]+)<\/(?:pubDate|published|updated)>/i),
    );
    const link = decodeXml(
      capture(block, /<link[^>]+href=["']([^"']+)["']/i)
      || capture(block, /<link(?:\s[^>]*)?>([^<]+)<\/link>/i)
      || url,
    );
    if (!title || !published) continue;
    const age = now.getTime() - Date.parse(published);
    if (age < -60 * 60 * 1000 || age > 24 * 60 * 60 * 1000) continue;
    const classification = classifyHeadline(`${title} ${summary}`);
    if (!classification) continue;
    result.push(eventRow({
      symbol,
      kind: classification.kind,
      title,
      startsAt: published,
      exactTime: true,
      priority: classification.priority,
      sourceName,
      sourceUrl: link,
      active: true,
      sourceType: "official_feed",
    }));
  }
  return result;
}

function classifyHeadline(title) {
  const text = String(title || "").toLowerCase();
  if (!text) return null;
  const retrospective = /(resolved|resolution|postmortem|post-mortem|incident report|root cause analysis|retrospective)/i.test(text);
  const security = /(hack|exploit|security incident|critical(?: security)? vulnerability|security vulnerability.*critical|breach|compromis|under attack|attack detected|zero[- ]day|cve-\d{4}-\d+)/i.test(text);
  const vulnerability = /\bvulnerabilit(?:y|ies)\b/i.test(text);
  const network = /(outage|network halt|chain halt|stalled|downtime|degraded|network disruption|consensus issue)/i.test(text);
  if (retrospective && (security || network)) return { kind: "NEWS", priority: 78 };
  if (security) return { kind: "SECURITY", priority: 100 };
  if (network) return { kind: "NETWORK", priority: 100 };
  if (vulnerability) return { kind: "NEWS", priority: 88 };
  // A headline timestamp is not the execution time of a token release. Exact
  // unlock risk comes from the date-verified unlock calendar below.
  if (/(unlock|vesting|cliff release|buyback|token burn|burn program|mint|emission|tokenomics|supply change|airdrop)/i.test(text)) return { kind: "NEWS", priority: 82 };
  if (/(etf|sec filing|regulatory approval|regulatory decision)/i.test(text)) return { kind: "ETF", priority: 92 };
  if (/(lawsuit|legal settlement|regulatory|regulator|regulatory license|court ruling|legal action)/i.test(text)) return { kind: "NEWS", priority: 88 };
  if (/(maintenance|scheduled downtime)/i.test(text)) return { kind: "MAINTENANCE", priority: 84 };
  if (/(governance|proposal|referendum|community vote|onchain vote)/i.test(text)) return { kind: "GOVERNANCE", priority: 86 };
  if (/(upgrade|hard fork|hardfork|mainnet launch|protocol release|security patch|new version)/i.test(text)) return { kind: "UPGRADE", priority: 88 };
  if (/(staking|validator)/i.test(text) && /(launch|change|update|reward|slashing|commission|requirement|migration|enable|disable|deprecat)/i.test(text)) return { kind: "NEWS", priority: 80 };
  if (/(listing|delisting|acquisition|treasury|partnership|integration|token launch|token sale|migration)/i.test(text)) return { kind: "NEWS", priority: 78 };
  return null;
}

async function fetchGithubReleases(symbol, repo, now) {
  const url = `https://github.com/${repo}/releases.atom`;
  const text = await fetchText(url);
  const entries = [...text.matchAll(/<entry>([\s\S]*?)<\/entry>/gi)].slice(0, 3);
  const result = [];
  for (const match of entries) {
    const block = match[1];
    const title = cleanText(decodeXml(capture(block, /<title[^>]*>([\s\S]*?)<\/title>/i)));
    const summary = cleanText(decodeXml(stripHtml(
      capture(block, /<(?:content|summary)(?:\s[^>]*)?>([\s\S]*?)<\/(?:content|summary)>/i),
    )));
    const updated = parseDateValue(capture(block, /<updated>([^<]+)<\/updated>/i));
    const link = capture(block, /<link[^>]+href="([^"]+)"/i) || url;
    if (!title || !updated || now.getTime() - Date.parse(updated) > 24 * 60 * 60 * 1000) continue;
    // Routine version tags are not automatically market-moving news. Keep a
    // GitHub release only when its official title/body actually contains one
    // of the same relevant event signals used by the project-news pipeline.
    const classification = classifyHeadline(`${title} ${summary}`);
    if (!classification) continue;
    result.push(eventRow({
      symbol,
      kind: classification.kind,
      title,
      startsAt: updated,
      exactTime: true,
      priority: classification.priority,
      sourceName: `GitHub ${repo}`,
      sourceUrl: link,
      active: true,
      sourceType: "release",
    }));
  }
  return result;
}

async function fetchBitcoinEtfFlow(now) {
  const text = await fetchText(ETF_SOURCE_URL);
  const row = parseFarsideBitcoinEtfFlow(text);
  if (!row) throw new Error("Farside BTC ETF row not recognized");
  const rowTime = Date.parse(`${row.date}T00:00:00Z`);
  if (!Number.isFinite(rowTime) || now.getTime() - rowTime > 7 * 86_400_000) {
    throw new Error("Farside BTC ETF row is stale");
  }
  return row;
}

function parseFarsideBitcoinEtfFlow(text) {
  const rows = [...String(text || "").matchAll(/<tr\b[^>]*>([\s\S]*?)<\/tr>/gi)];
  const parsed = [];
  for (const match of rows) {
    const cells = [...match[1].matchAll(/<(?:td|th)\b[^>]*>([\s\S]*?)<\/(?:td|th)>/gi)]
      .map((cell) => cleanText(stripHtml(decodeXml(cell[1]))));
    if (cells.length < 2) continue;
    const date = parseFarsideDate(cells[0]);
    if (!date) continue;
    const totalM = parseFarsideNumber(cells[cells.length - 1]);
    if (totalM === null) continue;
    parsed.push({ date, totalM });
  }
  if (!parsed.length) {
    // Conservative fallback for text-table mirrors: date plus the final pipe cell.
    const flatRows = String(text || "").split(/\r?\n/);
    for (const row of flatRows) {
      const dateMatch = row.match(/\b(\d{1,2})\s+([A-Za-z]{3,9})\s+(20\d{2})\b/);
      if (!dateMatch || !row.includes("|")) continue;
      const date = parseFarsideDate(dateMatch[0]);
      const totalM = parseFarsideNumber(row.split("|").pop());
      if (date && totalM !== null) parsed.push({ date, totalM });
    }
  }
  parsed.sort((a, b) => Date.parse(a.date) - Date.parse(b.date));
  return parsed.at(-1) || null;
}

function parseFarsideDate(value) {
  const match = cleanText(value).match(/^(\d{1,2})\s+([A-Za-z]{3,9})\s+(20\d{2})$/);
  if (!match) return null;
  const monthName = match[2].toLowerCase();
  const month = MONTHS[monthName] ?? Object.entries(MONTHS)
    .find(([name]) => name.startsWith(monthName.slice(0, 3)))?.[1];
  if (month === undefined) return null;
  const date = new Date(Date.UTC(Number(match[3]), month, Number(match[1])));
  return Number.isNaN(date.getTime()) ? null : date.toISOString().slice(0, 10);
}

function parseFarsideNumber(value) {
  let text = cleanText(value).replace(/[$,]/g, "");
  if (!text || text === "-" || /^n\/?a$/i.test(text)) return null;
  let sign = 1;
  if (/^\(.*\)$/.test(text)) {
    sign = -1;
    text = text.slice(1, -1).trim();
  }
  const match = text.match(/^-?\d+(?:\.\d+)?$/);
  if (!match) return null;
  const number = Number(text);
  if (!Number.isFinite(number)) return null;
  // Preserve an explicit leading minus. Parenthesized values are negative too.
  return sign < 0 ? -Math.abs(number) : number;
}

function scheduledXrpEscrowUnlock(now) {
  // Ripple's official escrow schedule releases an on-ledger escrow on the
  // first day of each month. Unused XRP can be re-escrowed, so this is an
  // availability/supply-risk reminder, not an assumption that 1B XRP is sold.
  const current = utcDay(now);
  let release = new Date(Date.UTC(current.getUTCFullYear(), current.getUTCMonth(), 1));
  if (current.getUTCDate() > 1) {
    release = new Date(Date.UTC(current.getUTCFullYear(), current.getUTCMonth() + 1, 1));
  }
  // Ripple's Q1 2025 market report said escrow releases extend for the next
  // 42 months; do not extrapolate this deterministic rule beyond that window.
  const documentedEnd = Date.UTC(2028, 8, 1);
  if (release.getTime() > documentedEnd) return null;
  const days = Math.floor((release.getTime() - current.getTime()) / 86_400_000);
  if (days < 0 || days > 14) return null;
  return eventRow({
    symbol: "XRP",
    kind: "UNLOCK",
    title: "XRP monthly Ripple escrow release window",
    startsAt: release.toISOString(),
    exactTime: false,
    priority: 94,
    sourceName: "Ripple XRP Markets Report",
    sourceUrl: "https://ripple.com/insights/q1-2025-xrp-markets-report/",
    active: false,
    sourceType: "unlock",
  });
}

async function fetchTokenomistUnlock(symbol, slug, now) {
  const url = `https://tokenomist.ai/${slug}/unlock-events`;
  const text = await fetchText(url);
  return parseTokenomistUnlock(symbol, text, url, now);
}

function parseTokenomistUnlock(symbol, text, sourceUrl, now = new Date()) {
  const flat = cleanText(stripHtml(decodeXml(String(text || ""))));
  if (/is fully unlocked/i.test(flat)) return null;
  const match = flat.match(/next unlock for [^.]{1,120}? is scheduled for ([A-Za-z]+\s+\d{1,2},\s+20\d{2})/i)
    || flat.match(/next unlock[^.]{0,80}? scheduled for ([A-Za-z]+\s+\d{1,2},\s+20\d{2})/i);
  if (!match) return null;
  const date = parseEnglishDate(match[1]);
  if (!date) return null;
  const days = Math.floor((date.getTime() - utcDay(now).getTime()) / 86_400_000);
  if (days < 0 || days > 14) return null;
  return eventRow({
    symbol,
    kind: "UNLOCK",
    title: `${symbol} token unlock`,
    startsAt: date.toISOString(),
    exactTime: false,
    priority: 94,
    sourceName: "Tokenomist public unlock calendar",
    sourceUrl,
    active: false,
    sourceType: "unlock",
  });
}

function eventRow({ symbol, kind, title, startsAt = null, endsAt = null, expiresAt = null, exactTime = true, priority = 70, sourceName, sourceUrl, active = false, sourceType }) {
  return {
    symbol,
    kind,
    title: cleanText(title).slice(0, 240),
    starts_at: startsAt,
    ends_at: endsAt,
    expires_at: expiresAt,
    exact_time: Boolean(exactTime),
    priority: Math.max(1, Math.min(100, Number(priority) || 70)),
    source_name: sourceName,
    source_url: sourceUrl,
    active: Boolean(active),
    verified: true,
    source_type: sourceType,
  };
}

function eventKey(event) {
  return [event.symbol, event.kind, event.starts_at || "", String(event.source_url || "").toLowerCase()].join("|");
}

function githubSources() {
  return Object.entries(PROJECTS).flatMap(([symbol, project]) => (project.github || []).map((repo) => [symbol, repo]));
}

function symbolForOfficialUrl(value, allowedSymbols) {
  let host;
  try { host = new URL(value).hostname.toLowerCase().replace(/^www\./, ""); }
  catch { return null; }
  for (const symbol of allowedSymbols) {
    for (const domain of PROJECTS[symbol]?.domains || []) {
      const normalized = domain.toLowerCase().replace(/^www\./, "");
      if (host === normalized || host.endsWith(`.${normalized}`)) return symbol;
    }
  }
  return null;
}

function quoteGdelt(term) {
  return /\s/.test(term) ? `"${term}"` : term;
}

async function fetchText(url, timeoutMs = 12_000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, {
      headers: { Accept: "application/json,application/atom+xml,text/html;q=0.9,*/*;q=0.8", "User-Agent": `crypto-signal-monitor/${APP_VERSION}` },
      signal: controller.signal,
    });
    if (!response.ok) throw new Error(`${response.status} ${url}`);
    return await response.text();
  } finally {
    clearTimeout(timer);
  }
}

async function fetchJson(url) {
  return JSON.parse(await fetchText(url));
}

async function readStoredFeed(env) {
  for (const cacheUrl of [CACHE_URL, LEGACY_CACHE_URL]) {
    try {
      const response = await caches.default.match(new Request(cacheUrl));
      if (response) {
        const value = await response.json();
        if (isObject(value) && Array.isArray(value.events)) return value;
      }
    } catch (error) {
      console.warn(`Cache read failed: ${shortError(error)}`);
    }
  }
  if (!env.EVENTS_KV?.get) return null;
  for (const storeKey of [STORE_KEY, LEGACY_STORE_KEY]) {
    try {
      const value = await env.EVENTS_KV.get(storeKey, { type: "json" });
      if (isObject(value) && Array.isArray(value.events)) return value;
    } catch (error) {
      console.warn(`KV read failed: ${shortError(error)}`);
    }
  }
  return null;
}

function durableFeedSignature(feed) {
  const rows = Array.isArray(feed?.events) ? feed.events : [];
  const stableEvents = rows.map((event) => ({
    symbol: event?.symbol || "",
    kind: event?.kind || "",
    title: event?.title || "",
    starts_at: event?.starts_at || null,
    ends_at: event?.ends_at || null,
    active: Boolean(event?.active),
    exact_time: Boolean(event?.exact_time),
    priority: Number(event?.priority || 0),
    source_type: event?.source_type || "",
    source_url: event?.source_url || "",
  })).sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b)));
  const meta = isObject(feed?.meta) ? feed.meta : {};
  const text = JSON.stringify({
    events: stableEvents,
    etf_last_date: meta.etf_last_date || null,
    etf_last_total_m: Number.isFinite(Number(meta.etf_last_total_m)) ? Number(meta.etf_last_total_m) : null,
  });
  let hash = 2166136261;
  for (let index = 0; index < text.length; index += 1) {
    hash ^= text.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(16).padStart(8, "0");
}

async function writeStoredFeed(env, feed) {
  feed.meta = isObject(feed.meta) ? feed.meta : {};
  const signature = durableFeedSignature(feed);
  const previousSignature = String(feed.meta.kv_signature || "");
  const previousWrite = parseMillis(feed.meta.kv_written_at);
  const generatedAt = parseMillis(feed.generated_at) || Date.now();
  const kvDue = !previousWrite
    || signature !== previousSignature
    || generatedAt - previousWrite >= KV_HEARTBEAT_MS;

  const kvConfigured = Boolean(env.EVENTS_KV?.put);
  let kvOk = kvConfigured && !kvDue;
  if (kvDue && kvConfigured) {
    const candidate = {
      ...feed,
      meta: {
        ...feed.meta,
        kv_signature: signature,
        kv_written_at: feed.generated_at || new Date(generatedAt).toISOString(),
      },
    };
    try {
      await env.EVENTS_KV.put(STORE_KEY, JSON.stringify(candidate));
      feed.meta.kv_signature = candidate.meta.kv_signature;
      feed.meta.kv_written_at = candidate.meta.kv_written_at;
      kvOk = true;
    } catch (error) {
      // Crucial: do not advance kv_signature/kv_written_at on failure.  The
      // local cache will retain the old metadata so the next run retries KV.
      console.warn(`KV write failed: ${shortError(error)}`);
    }
  }

  const body = JSON.stringify(feed);
  let cacheOk = false;
  try {
    await caches.default.put(
      new Request(CACHE_URL),
      new Response(body, { headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "public, max-age=120" } }),
    );
    cacheOk = true;
  } catch (error) {
    console.warn(`Cache write failed: ${shortError(error)}`);
  }
  if (!cacheOk && !kvOk) throw new Error("Event feed could not be persisted");
}

function githubHeaders(env) {
  return {
    Accept: "application/vnd.github+json",
    Authorization: `Bearer ${env.GH_PAT}`,
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": `cloudflare-crypto-scheduler-v${APP_VERSION}`,
  };
}

async function workflowAlreadyActive(env, workflow) {
  const url = `https://api.github.com/repos/${env.GH_OWNER}/${env.GH_REPO}/actions/workflows/${workflow}/runs?per_page=6`;
  const response = await fetch(url, { headers: githubHeaders(env) });
  if (!response.ok) {
    console.warn(`Run check failed: ${response.status}`);
    return false;
  }
  const payload = await response.json();
  const runs = Array.isArray(payload.workflow_runs) ? payload.workflow_runs : [];
  return runs.some((run) => ["queued", "in_progress", "waiting", "pending", "requested"].includes(run.status));
}

async function gzipBase64(text) {
  const input = new Blob([text], { type: "application/json" });
  const compressed = input.stream().pipeThrough(new CompressionStream("gzip"));
  const bytes = new Uint8Array(await new Response(compressed).arrayBuffer());
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
  }
  return btoa(binary);
}

async function dispatchSnapshot(feed) {
  const raw = JSON.stringify({
    version: APP_VERSION,
    package_revision: PACKAGE_REVISION,
    generated_at: feed?.generated_at || new Date().toISOString(),
    events: Array.isArray(feed?.events) ? feed.events : [],
    meta: { source_health: isObject(feed?.meta?.source_health) ? feed.meta.source_health : {} },
  });
  // GitHub workflow_dispatch accepts at most 65,535 characters across inputs.
  // Normal feeds stay plain JSON (zero compression CPU). Only unusually large
  // feeds use the Workers-native gzip stream; Python decodes the `gz:` prefix.
  let payload = raw;
  if (raw.length > 50_000) payload = `gz:${await gzipBase64(raw)}`;
  if (payload.length > 60_000) {
    throw new Error(`Event dispatch payload too large: ${payload.length} chars`);
  }
  return payload;
}

async function triggerGitHubWithRetry(env, source, feed) {
  const required = ["GH_OWNER", "GH_REPO", "GH_PAT"];
  const missing = required.filter((name) => !env[name]);
  if (missing.length) throw new Error(`Variable missing: ${missing.join(", ")}`);
  const workflow = env.GH_WORKFLOW || "monitor.yml";
  const ref = env.GH_REF || "master";
  if (await workflowAlreadyActive(env, workflow)) {
    console.log(JSON.stringify({ event: "github-dispatch-skipped", reason: "workflow-active", source }));
    return;
  }
  const eventFeedPayload = await dispatchSnapshot(feed);
  const endpoint = `https://api.github.com/repos/${env.GH_OWNER}/${env.GH_REPO}/actions/workflows/${workflow}/dispatches`;
  let lastError = "";
  for (let attempt = 1; attempt <= 3; attempt += 1) {
    const response = await fetch(endpoint, {
      method: "POST",
      headers: { ...githubHeaders(env), "Content-Type": "application/json" },
      body: JSON.stringify({
        ref,
        inputs: { send_discord: "true", event_feed_payload: eventFeedPayload },
      }),
    });
    if (response.ok) {
      console.log(JSON.stringify({ event: "github-dispatch", status: response.status, attempt, source, feed_events: feed.events.length }));
      return;
    }
    lastError = `${response.status}: ${(await response.text()).slice(0, 500)}`;
    if (attempt < 3) await new Promise((resolve) => setTimeout(resolve, attempt * 1500));
  }
  throw new Error(`GitHub dispatch failed: ${lastError}`);
}

function parseEnglishDate(value) {
  const match = String(value || "").trim().match(/^([A-Za-z]+)\s+(\d{1,2}),\s+(20\d{2})$/);
  if (!match) return null;
  const month = MONTHS[match[1].toLowerCase()];
  if (month === undefined) return null;
  const date = new Date(Date.UTC(Number(match[3]), month, Number(match[2])));
  return Number.isNaN(date.getTime()) ? null : date;
}

function parseGdeltDate(value) {
  const match = String(value || "").match(/^(20\d{2})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$/);
  if (!match) return null;
  return new Date(Date.UTC(+match[1], +match[2] - 1, +match[3], +match[4], +match[5], +match[6])).toISOString();
}

function parseDateValue(value) {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}

function parseMillis(value) {
  if (!value) return 0;
  const number = typeof value === "number" ? value : Date.parse(String(value));
  return Number.isFinite(number) ? number : 0;
}

function utcDay(value) {
  return new Date(Date.UTC(value.getUTCFullYear(), value.getUTCMonth(), value.getUTCDate()));
}

function capture(text, pattern) {
  return String(text || "").match(pattern)?.[1] || "";
}

function stripHtml(value) {
  return String(value || "").replace(/<script[\s\S]*?<\/script>/gi, " ").replace(/<style[\s\S]*?<\/style>/gi, " ").replace(/<[^>]+>/g, " ");
}

function decodeXml(value) {
  return String(value || "")
    .replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, "$1")
    .replace(/&amp;/g, "&").replace(/&quot;/g, '"').replace(/&#39;|&apos;/g, "'")
    .replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&nbsp;|&#160;/g, " ");
}

function cleanText(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function hostname(value) {
  try { return new URL(value).hostname; } catch { return "Official source"; }
}

function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function shortError(error) {
  return String(error?.message || error || "unknown").slice(0, 240);
}

function jsonResponse(value, status = 200, headers = {}) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", ...headers },
  });
}

