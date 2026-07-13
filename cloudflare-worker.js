export default {
  async scheduled(controller, env, ctx) {
    ctx.waitUntil(triggerGitHub(env));
  },

  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname !== "/test") {
      return new Response("Crypto-Scheduler aktiv.", { status: 200 });
    }
    if (!env.TEST_KEY || url.searchParams.get("key") !== env.TEST_KEY) {
      return new Response("Nicht erlaubt.", { status: 403 });
    }
    try {
      const result = await triggerGitHub(env);
      return Response.json({ ok: true, ...result });
    } catch (error) {
      return Response.json({ ok: false, error: String(error) }, { status: 500 });
    }
  },
};

async function triggerGitHub(env) {
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
        "User-Agent": "cloudflare-crypto-scheduler",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        ref: env.GH_REF || "master",
        inputs: { send_discord: "true" },
      }),
    });

    if (response.ok) {
      console.log(`GitHub-Workflow gestartet, Versuch ${attempt}.`);
      return { status: response.status, attempt };
    }
    lastError = `${response.status}: ${await response.text()}`;
    if (attempt < 3) {
      await new Promise((resolve) => setTimeout(resolve, attempt * 1500));
    }
  }
  throw new Error(`GitHub konnte nicht gestartet werden: ${lastError}`);
}
