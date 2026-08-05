// Package revision: r1
// Crypto event feed and GitHub scheduler for v5.3.0.

const APP_VERSION = "5.3.0";
const PACKAGE_REVISION = "r1";
const STORE_KEY = "crypto-events-v530";
const CACHE_URL = "https://crypto-events.internal/v5.3.0/events.json";
const ACTIVE_RETENTION_MS = 25 * 60 * 1000;
const STATUS_GRACE_MS = 10 * 60 * 1000;
const NEWS_REFRESH_MS = 5 * 60 * 1000;
const UNLOCK_REFRESH_MS = 6 * 60 * 60 * 1000;
const SEEN_RETENTION_MS = 48 * 60 * 60 * 1000;

const PROJECTS = Object.freeze({
  BTC: {
    domains: ["bitcoin.org", "bitcoincore.org"],
    github: ["bitcoin/bitcoin"],
  },
  ETH: {
    domains: ["ethereum.org"],
    github: ["ethereum/go-ethereum", "ethereum/consensus-specs"],
  },
  SOL: {
    domains: ["solana.com"],
    github: ["anza-xyz/agave"],
    unlockSlug: "solana",
  },
  HYPE: {
    domains: ["hyperfoundation.org", "hyperliquid.xyz"],
    unlockSlug: "hyperliquid",
  },
  ENA: {
    domains: ["ethena.fi", "ethenafoundation.com"],
    unlockSlug: "ethena",
  },
  ZEC: {
    domains: ["z.cash", "electriccoin.co"],
    github: ["zcash/zcash"],
    unlockSlug: "zcash",
  },
  PUMP: {
    domains: ["pump.fun"],
    unlockSlug: "pump-fun",
  },
  AAVE: {
    domains: ["aave.com", "governance.aave.com"],
    github: ["aave/aave-v3-core"],
    unlockSlug: "aave",
  },
  ADA: {
    domains: ["cardano.org", "essentialcardano.io", "iohk.io"],
    github: ["IntersectMBO/cardano-node"],
    unlockSlug: "cardano",
  },
  AVAX: {
    domains: ["avax.network"],
    github: ["ava-labs/avalanchego"],
    unlockSlug: "avalanche",
  },
  JUP: {
    domains: ["jup.ag"],
    unlockSlug: "jupiter-exchange-solana",
  },
  APT: {
    domains: ["aptosfoundation.org", "aptoslabs.com", "aptos.dev"],
    github: ["aptos-labs/aptos-core"],
    unlockSlug: "aptos",
  },
  NEAR: {
    domains: ["near.org", "nearfoundation.org"],
    github: ["near/nearcore"],
    unlockSlug: "near",
  },
  ONDO: {
    domains: ["ondo.finance"],
    unlockSlug: "ondo-finance",
  },
  SUI: {
    domains: ["sui.io"],
    github: ["MystenLabs/sui"],
    unlockSlug: "sui",
  },
  TIA: {
    domains: ["celestia.org"],
    github: ["celestiaorg/celestia-app"],
    unlockSlug: "celestia",
  },
});

const STATUSPAGE_SOURCES = Object.freeze([
  ["SOL", "https://status.solana.com/api/v2/incidents/unresolved.json", "Solana Status", "incidents"],
  ["SOL", "https://status.solana.com/api/v2/scheduled-maintenances/upcoming.json", "Solana Status", "scheduled_maintenances"],
  ["HYPE", "https://hyperliquid.statuspage.io/api/v2/incidents/unresolved.json", "Hyperliquid Status", "incidents"],
  ["HYPE", "https://hyperliquid.statuspage.io/api/v2/scheduled-maintenances/upcoming.json", "Hyperliquid Status", "scheduled_maintenances"],
  ["AVAX", "https://status.avax.network/api/v2/incidents/unresolved.json", "Avalanche Status", "incidents"],
  ["AVAX", "https://status.avax.network/api/v2/scheduled-maintenances/upcoming.json", "Avalanche Status", "scheduled_maintenances"],
  ["SUI", "https://status.sui.io/api/v2/incidents/unresolved.json", "Sui Status", "incidents"],
  ["SUI", "https://status.sui.io/api/v2/scheduled-maintenances/upcoming.json", "Sui Status", "scheduled_maintenances"],
]);

const HTML_STATUS_SOURCES = Object.freeze([
  ["AAVE", "https://status.aave.com/", "fully operational", "Aave Status"],
  ["JUP", "https://status.jup.ag/", "All services are online", "Jupiter Status"],
  ["NEAR", "https://status.near.org/", "No problems detected", "NEAR Status"],
  ["TIA", "https://status.celestia.org/", "fully operational", "Celestia Status"],
]);

const OFFICIAL_FEEDS = Object.freeze([
  ["ETH", "https://blog.ethereum.org/feed.xml", "Ethereum Foundation Blog"],
  ["SOL", "https://solana.com/changelog/rss.xml", "Solana Changelog"],
]);

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
  await triggerGitHubWithRetry(env, source);
}

function schedulerEnabled(env) {
  return String(env.ENABLED ?? "1").trim() === "1";
}

async function refreshEventFeed(env, now) {
  const previous = (await readStoredFeed(env)) || emptyFeed(now);
  const diagnostics = [];
  const fresh = [];

  const statusResults = await Promise.allSettled([
    ...STATUSPAGE_SOURCES.map(([symbol, url, name, collection]) => fetchStatuspage(symbol, url, name, collection, now)),
    ...HTML_STATUS_SOURCES.map(([symbol, url, healthy, name]) => fetchHtmlStatus(symbol, url, healthy, name, now)),
  ]);
  let statusOk = 0;
  for (const result of statusResults) {
    if (result.status === "fulfilled") {
      statusOk += 1;
      fresh.push(...result.value);
    } else diagnostics.push(`status: ${shortError(result.reason)}`);
  }

  const previousMeta = isObject(previous.meta) ? previous.meta : {};
  const lastNews = parseMillis(previousMeta.news_checked_at);
  let newsCheckedAt = previousMeta.news_checked_at || null;
  let newsOk = Number(previousMeta?.source_health?.news_ok || 0);
  let newsTotal = Number(previousMeta?.source_health?.news_total || 0);
  if (!lastNews || now.getTime() - lastNews >= NEWS_REFRESH_MS) {
    const newsResults = await Promise.allSettled([
      ...buildGdeltQueries().map((batch) => fetchGdeltBatch(batch, now)),
      ...OFFICIAL_FEEDS.map(([symbol, url, name]) => fetchOfficialFeed(symbol, url, name, now)),
    ]);
    newsOk = 0;
    newsTotal = newsResults.length;
    for (const result of newsResults) {
      if (result.status === "fulfilled") {
        newsOk += 1;
        fresh.push(...result.value);
      } else diagnostics.push(`news: ${shortError(result.reason)}`);
    }
    // If every news source failed, retry on the next minute instead of hiding
    // the outage behind the normal five-minute refresh interval.
    if (newsOk > 0) newsCheckedAt = now.toISOString();
  }

  const repos = githubSources();
  const batchCount = Math.max(1, Math.ceil(repos.length / 4));
  const batchIndex = Math.floor(now.getTime() / 60_000) % batchCount;
  const githubBatch = repos.slice(batchIndex * 4, batchIndex * 4 + 4);
  const releaseResults = await Promise.allSettled(
    githubBatch.map(([symbol, repo]) => fetchGithubReleases(symbol, repo, now)),
  );
  let releaseOk = 0;
  for (const result of releaseResults) {
    if (result.status === "fulfilled") {
      releaseOk += 1;
      fresh.push(...result.value);
    } else diagnostics.push(`release: ${shortError(result.reason)}`);
  }

  const lastUnlock = parseMillis(previousMeta.unlocks_checked_at);
  let unlocksCheckedAt = previousMeta.unlocks_checked_at || null;
  let unlockOk = Number(previousMeta?.source_health?.unlock_ok || 0);
  let unlockTotal = Number(previousMeta?.source_health?.unlock_total || 0);
  if (!lastUnlock || now.getTime() - lastUnlock >= UNLOCK_REFRESH_MS) {
    const unlockResults = await Promise.allSettled(
      Object.entries(PROJECTS)
        .filter(([, project]) => project.unlockSlug)
        .map(([symbol, project]) => fetchTokenomistUnlock(symbol, project.unlockSlug, now)),
    );
    unlockOk = 0;
    unlockTotal = unlockResults.length;
    for (const result of unlockResults) {
      if (result.status === "fulfilled") {
        unlockOk += 1;
        if (result.value) fresh.push(result.value);
      } else diagnostics.push(`unlock: ${shortError(result.reason)}`);
    }
    if (unlockOk > 0) unlocksCheckedAt = now.toISOString();
  }

  const merged = mergeEvents(previous, fresh, now);
  return {
    version: APP_VERSION,
    package_revision: PACKAGE_REVISION,
    generated_at: now.toISOString(),
    events: merged.events,
    diagnostics: diagnostics.slice(0, 24),
    meta: {
      news_checked_at: newsCheckedAt,
      unlocks_checked_at: unlocksCheckedAt,
      github_batch: batchIndex,
      source_health: {
        status_ok: statusOk,
        status_total: statusResults.length,
        news_ok: newsOk,
        news_total: newsTotal,
        release_ok: releaseOk,
        release_total: releaseResults.length,
        unlock_ok: unlockOk,
        unlock_total: unlockTotal,
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
      news_checked_at: null, unlocks_checked_at: null, github_batch: 0,
      source_health: { status_ok: 0, status_total: 0, news_ok: 0, news_total: 0, release_ok: 0, release_total: 0, unlock_ok: 0, unlock_total: 0 },
      seen_events: {},
    },
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
    "airdrop", "listing", "delisting", "acquisition", "treasury", "strategic partnership",
  ];
  const query = `(${domainQuery}) AND (${eventTerms.map(quoteGdelt).join(" OR ")})`;
  const url = new URL("https://api.gdeltproject.org/api/v2/doc/doc");
  url.searchParams.set("query", query);
  url.searchParams.set("mode", "artlist");
  url.searchParams.set("maxrecords", "75");
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
  const security = /(hack|exploit|security incident|critical vulnerability|breach|compromis|under attack|attack detected)/i.test(text);
  const network = /(outage|network halt|chain halt|stalled|downtime|degraded|network disruption|consensus issue)/i.test(text);
  if (retrospective && (security || network)) return { kind: "NEWS", priority: 78 };
  if (security) return { kind: "SECURITY", priority: 100 };
  if (network) return { kind: "NETWORK", priority: 100 };
  // A headline timestamp is not the execution time of a token release. Exact
  // unlock risk comes from the date-verified unlock calendar below.
  if (/(unlock|vesting|cliff release|buyback|token burn|burn program|mint|emission|tokenomics|supply change|airdrop)/i.test(text)) return { kind: "NEWS", priority: 82 };
  if (/(etf|sec filing|regulatory approval|regulatory decision)/i.test(text)) return { kind: "ETF", priority: 92 };
  if (/(maintenance|scheduled downtime)/i.test(text)) return { kind: "MAINTENANCE", priority: 84 };
  if (/(governance|proposal|referendum|community vote|onchain vote)/i.test(text)) return { kind: "GOVERNANCE", priority: 86 };
  if (/(upgrade|hard fork|hardfork|mainnet launch|protocol release|security patch|new version)/i.test(text)) return { kind: "UPGRADE", priority: 88 };
  if (/(listing|delisting|acquisition|treasury|strategic partnership|integration|token launch)/i.test(text)) return { kind: "NEWS", priority: 76 };
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
    const updated = parseDateValue(capture(block, /<updated>([^<]+)<\/updated>/i));
    const link = capture(block, /<link[^>]+href="([^"]+)"/i) || url;
    if (!title || !updated || now.getTime() - Date.parse(updated) > 24 * 60 * 60 * 1000) continue;
    const emergency = /(active exploit|exploited|critical vulnerability|security incident|emergency hotfix|cve-\d{4}-\d+)/i.test(title);
    result.push(eventRow({
      symbol,
      kind: emergency ? "SECURITY" : "UPGRADE",
      title,
      startsAt: updated,
      exactTime: true,
      priority: emergency ? 100 : 88,
      sourceName: `GitHub ${repo}`,
      sourceUrl: link,
      active: true,
      sourceType: "release",
    }));
  }
  return result;
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

function eventRow({ symbol, kind, title, startsAt = null, endsAt = null, exactTime = true, priority = 70, sourceName, sourceUrl, active = false, sourceType }) {
  return {
    symbol,
    kind,
    title: cleanText(title).slice(0, 240),
    starts_at: startsAt,
    ends_at: endsAt,
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
  try {
    if (env.EVENTS_KV?.get) {
      const value = await env.EVENTS_KV.get(STORE_KEY, { type: "json" });
      if (isObject(value) && Array.isArray(value.events)) return value;
    }
  } catch (error) {
    console.warn(`KV read failed: ${shortError(error)}`);
  }
  try {
    const response = await caches.default.match(new Request(CACHE_URL));
    if (response) {
      const value = await response.json();
      if (isObject(value) && Array.isArray(value.events)) return value;
    }
  } catch (error) {
    console.warn(`Cache read failed: ${shortError(error)}`);
  }
  return null;
}

async function writeStoredFeed(env, feed) {
  const body = JSON.stringify(feed);
  const jobs = [];
  if (env.EVENTS_KV?.put) jobs.push(env.EVENTS_KV.put(STORE_KEY, body));
  jobs.push(caches.default.put(
    new Request(CACHE_URL),
    new Response(body, { headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "public, max-age=120" } }),
  ));
  const results = await Promise.allSettled(jobs);
  const failed = results.filter((result) => result.status === "rejected");
  if (failed.length === results.length) throw new Error("Event feed could not be persisted");
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

async function triggerGitHubWithRetry(env, source) {
  const required = ["GH_OWNER", "GH_REPO", "GH_PAT"];
  const missing = required.filter((name) => !env[name]);
  if (missing.length) throw new Error(`Variable missing: ${missing.join(", ")}`);
  const workflow = env.GH_WORKFLOW || "monitor.yml";
  const ref = env.GH_REF || "master";
  if (await workflowAlreadyActive(env, workflow)) {
    console.log(JSON.stringify({ event: "github-dispatch-skipped", reason: "workflow-active", source }));
    return;
  }
  const endpoint = `https://api.github.com/repos/${env.GH_OWNER}/${env.GH_REPO}/actions/workflows/${workflow}/dispatches`;
  let lastError = "";
  for (let attempt = 1; attempt <= 3; attempt += 1) {
    const response = await fetch(endpoint, {
      method: "POST",
      headers: { ...githubHeaders(env), "Content-Type": "application/json" },
      body: JSON.stringify({ ref, inputs: { send_discord: "true" } }),
    });
    if (response.ok) {
      console.log(JSON.stringify({ event: "github-dispatch", status: response.status, attempt, source }));
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
    .replace(/&lt;/g, "<").replace(/&gt;/g, ">");
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

export {
  PROJECTS,
  buildGdeltQueries,
  classifyHeadline,
  fetchOfficialFeed,
  mergeEvents,
  parseTokenomistUnlock,
  symbolForOfficialUrl,
};
