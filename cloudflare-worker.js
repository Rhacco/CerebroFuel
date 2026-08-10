// r1
// Minimal GitHub scheduler for Cloudflare Workers Free.

const APP_VERSION = "7.1.0";
const PACKAGE_REVISION = "r1";

export default {
  async scheduled(_controller, env, ctx) {
    ctx.waitUntil(triggerGitHub(env));
  },

  async fetch(_request, env) {
    return jsonResponse({
      ok: true,
      version: APP_VERSION,
      revision: PACKAGE_REVISION,
      scheduler: schedulerEnabled(env) ? "enabled" : "paused",
    });
  },
};

function schedulerEnabled(env) {
  return String(env.ENABLED ?? "1").trim() === "1";
}

function githubHeaders(env) {
  return {
    Accept: "application/vnd.github+json",
    Authorization: `Bearer ${env.GH_PAT}`,
    "X-GitHub-Api-Version": "2026-03-10",
    "User-Agent": `cloudflare-crypto-scheduler-v${APP_VERSION}`,
    "Content-Type": "application/json",
  };
}

async function triggerGitHub(env) {
  if (!schedulerEnabled(env)) return;

  const required = ["GH_OWNER", "GH_REPO", "GH_PAT"];
  const missing = required.filter((name) => !String(env[name] ?? "").trim());
  if (missing.length) throw new Error(`Variable missing: ${missing.join(", ")}`);

  const owner = encodeURIComponent(String(env.GH_OWNER).trim());
  const repo = encodeURIComponent(String(env.GH_REPO).trim());
  const workflow = encodeURIComponent(String(env.GH_WORKFLOW || "monitor.yml").trim());
  const ref = String(env.GH_REF || "master").trim();
  const endpoint = `https://api.github.com/repos/${owner}/${repo}/actions/workflows/${workflow}/dispatches`;
  const body = JSON.stringify({ ref, inputs: { send_discord: "true" } });

  let lastError = "";
  for (let attempt = 1; attempt <= 3; attempt += 1) {
    try {
      const response = await fetch(endpoint, {
        method: "POST",
        headers: githubHeaders(env),
        body,
      });
      if (response.ok) return;

      const detail = (await response.text()).slice(0, 300);
      lastError = `${response.status}: ${detail}`;
      if (response.status < 500 && response.status !== 429) break;
    } catch (error) {
      lastError = String(error?.message || error);
    }
    if (attempt < 3) await new Promise((resolve) => setTimeout(resolve, attempt * 1000));
  }
  throw new Error(`GitHub dispatch failed: ${lastError}`);
}

function jsonResponse(value, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8" },
  });
}
