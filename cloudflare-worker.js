// v5.1.0: request a run every minute; never queue over an active run.
function schedulerEnabled(env) {
  // ENABLED 1 = aktiv, 2 = pausiert.
  return String(env.ENABLED ?? "1").trim() === "1";
}

export default {
  async scheduled(controller, env, ctx) {
    if (!schedulerEnabled(env)) {
      console.log(JSON.stringify({ event: "scheduler-paused", cron: controller.cron }));
      return;
    }
    ctx.waitUntil(triggerGitHubWithRetry(env, controller.cron));
  },

  async fetch(request, env) {
    return Response.json({
      ok: true,
      scheduler: schedulerEnabled(env) ? "enabled" : "paused",
      interval: "1m",
      version: "5.1.0",
    });
  },
};

function githubHeaders(env) {
  return {
    Accept: "application/vnd.github+json",
    Authorization: `Bearer ${env.GH_PAT}`,
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "cloudflare-crypto-scheduler-v5.1.0",
  };
}

async function workflowAlreadyActive(env, workflow) {
  const url =
    `https://api.github.com/repos/${env.GH_OWNER}/${env.GH_REPO}` +
    `/actions/workflows/${workflow}/runs?per_page=6`;
  const response = await fetch(url, { headers: githubHeaders(env) });
  if (!response.ok) {
    console.warn(`Run-Prüfung fehlgeschlagen: ${response.status}`);
    return false;
  }
  const payload = await response.json();
  const runs = Array.isArray(payload.workflow_runs) ? payload.workflow_runs : [];
  return runs.some((run) => ["queued", "in_progress", "waiting", "pending", "requested"].includes(run.status));
}

async function triggerGitHubWithRetry(env, source) {
  const required = ["GH_OWNER", "GH_REPO", "GH_PAT"];
  const missing = required.filter((name) => !env[name]);
  if (missing.length) throw new Error(`Variable fehlt: ${missing.join(", ")}`);

  const workflow = env.GH_WORKFLOW || "monitor.yml";
  const ref = env.GH_REF || "master";
  if (await workflowAlreadyActive(env, workflow)) {
    console.log(JSON.stringify({ event: "github-dispatch-skipped", reason: "workflow-active", source }));
    return { skipped: true };
  }

  const endpoint =
    `https://api.github.com/repos/${env.GH_OWNER}/${env.GH_REPO}` +
    `/actions/workflows/${workflow}/dispatches`;
  let lastError = "";
  for (let attempt = 1; attempt <= 3; attempt++) {
    const response = await fetch(endpoint, {
      method: "POST",
      headers: { ...githubHeaders(env), "Content-Type": "application/json" },
      body: JSON.stringify({
        ref,
        inputs: { send_discord: "true" },
      }),
    });
    if (response.ok) {
      const result = { status: response.status, attempt, source };
      console.log(JSON.stringify({ event: "github-dispatch", ...result }));
      return result;
    }
    lastError = `${response.status}: ${(await response.text()).slice(0, 500)}`;
    if (attempt < 3) await new Promise((resolve) => setTimeout(resolve, attempt * 1500));
  }
  throw new Error(`GitHub konnte nicht gestartet werden: ${lastError}`);
}

