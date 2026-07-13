const COIN_CODES = [
  "BTC", "ETH", "BNB", "XRP", "SOL", "DOGE", "XLM", "ADA", "HBAR", "SUI",
  "TAO", "UNI", "PEPE", "RENDER", "_______________________________TRUMP", "TRUMP",
  "BONK", "WIF", "__________________MEGA", "MEGA", "W", "KMNO", "ZK", "ZKSYNC",
  "APE", "FARTCOIN", "_XPL", "XPL", "OP", "LDO", "PYTH", "JTO", "CRV", "SEI",
  "ETHFI", "FET", "TIA", "INJ", "PUMP", "MORPHO", "__WLD", "WLD", "DOT",
  "AAVE", "ONDO", "WLFI", "NEAR", "BCH", "______HYPE", "HYPE"
];

const VALID_MODES = new Set(["5", "10", "30", "pause"]);
const STATE_KEY = "market-state-v31";
const MODE_KEY = "scheduler-mode-v31";
const STATUS_KEY = "scheduler-status-v31";

export default {
  async scheduled(controller, env, ctx) {
    ctx.waitUntil(handleScheduled(controller, env));
  },

  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname === "/control") {
      return handleControl(request, env, url);
    }
    if (url.pathname === "/state") {
      return handleState(request, env, url);
    }
    if (url.pathname === "/test") {
      return handleTest(request, env, url);
    }

    return new Response(
      "Crypto-Scheduler v3.1 aktiv. Steuerung: /control?key=DEIN_KEY",
      { status: 200, headers: { "content-type": "text/plain; charset=utf-8" } },
    );
  },
};

async function handleScheduled(controller, env) {
  const startedAt = Date.now();
  let snapshotResult;
  try {
    snapshotResult = await collectSnapshot(env);
  } catch (error) {
    snapshotResult = { ok: false, error: String(error) };
    console.error("Snapshot fehlgeschlagen", error);
  }

  const mode = await getMode(env);
  const minute = new Date(controller.scheduledTime).getUTCMinutes();
  let triggerResult = { triggered: false, mode };
  let triggerError = null;

  if (shouldTrigger(mode, minute)) {
    try {
      const github = await triggerGitHub(env);
      triggerResult = { triggered: true, mode, github };
    } catch (error) {
      triggerError = error;
      triggerResult = { triggered: false, mode, error: String(error) };
      console.error("GitHub-Trigger fehlgeschlagen", error);
    }
  }

  await env.SCHEDULER_KV.put(
    STATUS_KEY,
    JSON.stringify({
      scheduledTime: controller.scheduledTime,
      cron: controller.cron,
      mode,
      snapshot: snapshotResult,
      trigger: triggerResult,
      durationMs: Date.now() - startedAt,
    }),
  );
  console.log(JSON.stringify({ mode, minute, snapshotResult, triggerResult }));
  if (triggerError) throw triggerError;
}

function shouldTrigger(mode, minute) {
  if (mode === "pause") return false;
  if (mode === "5") return true;
  if (mode === "10") return (minute - 1 + 60) % 10 === 0;
  if (mode === "30") return (minute - 1 + 60) % 30 === 0;
  return true;
}

async function getMode(env) {
  const stored = await env.SCHEDULER_KV.get(MODE_KEY);
  return VALID_MODES.has(stored) ? stored : "5";
}

async function collectSnapshot(env) {
  if (!env.LCW_API_KEY) {
    throw new Error("Cloudflare-Secret LCW_API_KEY fehlt.");
  }
  const response = await fetch("https://api.livecoinwatch.com/coins/map", {
    method: "POST",
    headers: {
      accept: "application/json",
      "content-type": "application/json",
      "cache-control": "no-cache",
      pragma: "no-cache",
      "x-api-key": env.LCW_API_KEY,
      "user-agent": "crypto-signal-monitor-worker/3.1",
    },
    body: JSON.stringify({
      codes: COIN_CODES,
      currency: "USD",
      sort: "rank",
      order: "ascending",
      offset: 0,
      limit: 0,
      meta: false,
    }),
  });

  if (!response.ok) {
    throw new Error(`LCW ${response.status}: ${(await response.text()).slice(0, 300)}`);
  }
  const rows = await response.json();
  if (!Array.isArray(rows)) {
    throw new Error("LCW /coins/map lieferte kein Array.");
  }

  const coins = {};
  for (const row of rows) {
    if (!row || !row.code || row.rate == null) continue;
    coins[String(row.code).toUpperCase()] = {
      rate: Number(row.rate),
      volume: row.volume == null ? null : Number(row.volume),
    };
  }

  const now = Date.now();
  const existingRaw = await env.SCHEDULER_KV.get(STATE_KEY, "json");
  const existing = existingRaw && Array.isArray(existingRaw.snapshots)
    ? existingRaw.snapshots
    : [];
  const cutoff = now - 130 * 60 * 1000;
  const snapshots = existing
    .filter((item) => item && Number(item.ts) >= cutoff)
    .concat([{ ts: now, coins }])
    .sort((a, b) => Number(a.ts) - Number(b.ts));

  await env.SCHEDULER_KV.put(
    STATE_KEY,
    JSON.stringify({ version: 1, updatedAt: now, snapshots }),
  );
  return { ok: true, coins: Object.keys(coins).length, snapshots: snapshots.length, ts: now };
}

async function handleState(request, env, url) {
  if (!authorized(request, url, env.STATE_KEY || env.TEST_KEY, "x-state-key")) {
    return json({ ok: false, error: "Nicht erlaubt." }, 403);
  }
  const state = await env.SCHEDULER_KV.get(STATE_KEY, "json");
  return json(state || { version: 1, updatedAt: null, snapshots: [] }, 200);
}

async function handleTest(request, env, url) {
  if (!authorized(request, url, env.CONTROL_KEY || env.TEST_KEY, "x-control-key")) {
    return json({ ok: false, error: "Nicht erlaubt." }, 403);
  }
  try {
    const snapshot = await collectSnapshot(env);
    const github = await triggerGitHub(env);
    return json({ ok: true, snapshot, github }, 200);
  } catch (error) {
    return json({ ok: false, error: String(error) }, 500);
  }
}

async function handleControl(request, env, url) {
  const controlKey = env.CONTROL_KEY || env.TEST_KEY;
  if (!authorized(request, url, controlKey, "x-control-key")) {
    return new Response("Nicht erlaubt.", { status: 403 });
  }

  let message = "";
  if (request.method === "POST") {
    const form = await request.formData();
    const action = String(form.get("action") || "");
    if (VALID_MODES.has(action)) {
      await env.SCHEDULER_KV.put(MODE_KEY, action);
      message = action === "pause" ? "Automatik pausiert." : `Intervall auf ${action} Minuten gesetzt.`;
    } else if (action === "now") {
      try {
        await collectSnapshot(env);
        await triggerGitHub(env);
        message = "Sofortlauf gestartet.";
      } catch (error) {
        message = `Fehler: ${String(error)}`;
      }
    } else {
      message = "Unbekannte Aktion.";
    }
  }

  const mode = await getMode(env);
  const status = await env.SCHEDULER_KV.get(STATUS_KEY, "json");
  const state = await env.SCHEDULER_KV.get(STATE_KEY, "json");
  const secret = escapeHtml(encodeURIComponent(url.searchParams.get("key") || ""));
  const statusText = status ? escapeHtml(JSON.stringify(status, null, 2)) : "Noch kein Cron-Status vorhanden.";
  const snapshotCount = state && Array.isArray(state.snapshots) ? state.snapshots.length : 0;

  const html = `<!doctype html>
<html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Krypto-Scheduler</title>
<style>
body{font-family:system-ui,sans-serif;max-width:760px;margin:36px auto;padding:0 16px;background:#111;color:#eee}
.card{background:#1d1d1d;border:1px solid #444;border-radius:12px;padding:18px;margin:14px 0}
button{font-size:16px;padding:11px 16px;margin:5px;border:0;border-radius:9px;cursor:pointer}
.active{outline:3px solid #65d46e}.green{background:#2d8a3f;color:white}.blue{background:#2867b2;color:white}.orange{background:#b96a1f;color:white}.red{background:#a52d2d;color:white}.gray{background:#555;color:white}
pre{white-space:pre-wrap;word-break:break-word;font-size:12px}small{color:#bbb}
</style></head><body>
<h1>Krypto-Scheduler v3.1</h1>
<div class="card"><b>Aktuell:</b> ${mode === "pause" ? "Pause" : `${mode} Minuten`} · <b>Snapshots:</b> ${snapshotCount}<br><small>Basis-Cron: :01, :06, :11, :16 …</small></div>
${message ? `<div class="card">${escapeHtml(message)}</div>` : ""}
<div class="card"><form method="post" action="/control?key=${secret}">
<button class="green ${mode === "5" ? "active" : ""}" name="action" value="5">5 Min</button>
<button class="blue ${mode === "10" ? "active" : ""}" name="action" value="10">10 Min</button>
<button class="orange ${mode === "30" ? "active" : ""}" name="action" value="30">30 Min</button>
<button class="red ${mode === "pause" ? "active" : ""}" name="action" value="pause">Pause</button>
<button class="gray" name="action" value="now">Jetzt starten</button>
</form></div>
<div class="card"><b>Letzter Cron-Status</b><pre>${statusText}</pre></div>
</body></html>`;
  return new Response(html, {
    status: 200,
    headers: {
      "content-type": "text/html; charset=utf-8",
      "cache-control": "no-store",
    },
  });
}

function authorized(request, url, expected, headerName) {
  if (!expected) return false;
  const supplied = request.headers.get(headerName) || url.searchParams.get("key") || "";
  return timingSafeEqual(String(supplied), String(expected));
}

function timingSafeEqual(a, b) {
  if (a.length !== b.length) return false;
  let mismatch = 0;
  for (let i = 0; i < a.length; i++) mismatch |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return mismatch === 0;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function triggerGitHub(env) {
  const required = ["GH_OWNER", "GH_REPO", "GH_PAT"];
  const missing = required.filter((name) => !env[name]);
  if (missing.length) throw new Error(`Cloudflare-Variable fehlt: ${missing.join(", ")}`);

  const endpoint =
    `https://api.github.com/repos/${env.GH_OWNER}/${env.GH_REPO}` +
    `/actions/workflows/${env.GH_WORKFLOW || "monitor.yml"}/dispatches`;
  let lastError = "";

  for (let attempt = 1; attempt <= 3; attempt++) {
    const response = await fetch(endpoint, {
      method: "POST",
      headers: {
        Accept: "application/vnd.github+json",
        Authorization: `Bearer ${env.GH_PAT}`,
        "X-GitHub-Api-Version": "2026-03-10",
        "User-Agent": "cloudflare-crypto-scheduler/3.1",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        ref: env.GH_REF || "master",
        inputs: { send_discord: "true" },
      }),
    });

    const body = await response.text();
    if (response.ok) {
      let parsed = null;
      try { parsed = body ? JSON.parse(body) : null; } catch { parsed = body || null; }
      return { status: response.status, attempt, response: parsed };
    }
    lastError = `${response.status}: ${body.slice(0, 500)}`;
    if (attempt < 3) await scheduler.wait(attempt * 1500);
  }
  throw new Error(`GitHub konnte nicht gestartet werden: ${lastError}`);
}

function json(payload, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
    },
  });
}
