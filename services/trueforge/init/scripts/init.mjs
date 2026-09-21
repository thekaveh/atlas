// trueforge-init — seed TrueForge's live settings from the Atlas stack.
//
// Runs on every start (PUT /api/v1/settings/* is replace-by-name, so the
// whole script is idempotent):
//   1. wait for the server's /healthz,
//   2. mint a dedicated LiteLLM virtual key (alias `trueforge`) so agent
//      traffic never carries the master key (#1159 Q4 mitigation),
//   3. read the gateway's live /v1/models catalog — whatever Ollama and
//      cloud models are enabled arrive through here, nothing is hardcoded,
//   4. seed one `custom` model provider pointed at LiteLLM,
//   5. seed the in-stack mcp-servers connector when that family is enabled.
//
// Skills and sandbox providers are deliberately not seeded (#1159 Q3):
// TrueForge rejects agent specs that request them at session creation, which
// keeps the v1 boundary explicit. Runs inside the server's own image (Node 24,
// built-in fetch) — no dependencies beyond this file.

const TRUEFORGE_URL = process.env.TRUEFORGE_URL || "http://trueforge:8790";
const TRUEFORGE_API_KEY = process.env.TRUEFORGE_API_KEY || "";
const LITELLM_BASE_URL = process.env.LITELLM_BASE_URL || "http://litellm:4000";
const LITELLM_MASTER_KEY = process.env.LITELLM_MASTER_KEY || "";
const MCP_SERVERS_ENABLED = (process.env.MCP_SERVERS_SCALE || "0") === "1";

const VIRTUAL_KEY_ALIAS = "trueforge";
const PROVIDER_NAME = "atlas-litellm";
const MCP_CONNECTOR_NAME = "atlas-tools";

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function waitForHealthz(attempts = 60) {
  for (let i = 1; i <= attempts; i++) {
    try {
      const res = await fetch(`${TRUEFORGE_URL}/healthz`);
      if (res.ok) return;
    } catch {
      // server not up yet — fall through to the retry sleep
    }
    await sleep(2000);
  }
  throw new Error(`TrueForge server never became healthy at ${TRUEFORGE_URL}`);
}

async function litellm(path, body) {
  const res = await fetch(`${LITELLM_BASE_URL}${path}`, {
    method: body === undefined ? "GET" : "POST",
    headers: {
      Authorization: `Bearer ${LITELLM_MASTER_KEY}`,
      "Content-Type": "application/json",
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return res;
}

// Mint (or rotate) the dedicated virtual key. Virtual keys are not readable
// back in plaintext, so idempotency is delete-alias-then-generate; the fresh
// key immediately replaces the stored provider credential below. Falls back
// to the master key with a loud warning rather than leaving agents dead.
async function mintVirtualKey() {
  try {
    await litellm("/key/delete", { key_aliases: [VIRTUAL_KEY_ALIAS] });
  } catch {
    // best-effort: alias may not exist yet, or an older LiteLLM may not
    // support alias-addressed deletes — /key/generate below is the arbiter
  }
  try {
    const res = await litellm("/key/generate", {
      key_alias: VIRTUAL_KEY_ALIAS,
      metadata: { service: "trueforge", managed_by: "trueforge-init" },
    });
    if (res.ok) {
      const data = await res.json();
      if (data.key) {
        console.log(`trueforge-init: minted LiteLLM virtual key (alias ${VIRTUAL_KEY_ALIAS})`);
        return data.key;
      }
    }
    console.warn(`trueforge-init: WARNING /key/generate answered ${res.status}; falling back to the master key`);
  } catch (err) {
    console.warn(`trueforge-init: WARNING /key/generate failed (${err}); falling back to the master key`);
  }
  return LITELLM_MASTER_KEY;
}

async function fetchModelIds() {
  const res = await litellm("/v1/models");
  if (!res.ok) {
    throw new Error(`LiteLLM /v1/models answered ${res.status}`);
  }
  const data = await res.json();
  const ids = (data.data || []).map((m) => m.id).filter(Boolean);
  if (ids.length === 0) {
    throw new Error("LiteLLM /v1/models returned no models — cannot seed a provider with an empty catalog");
  }
  return ids;
}

// TrueForge resource names must match ^[a-z][a-z0-9-]{0,62}[a-z0-9]$ — LiteLLM
// ids like `ollama/qwen3:8b` do not, so derive a compliant display name and
// keep the raw id as model_id (what is actually sent to the gateway).
function resourceName(id, taken) {
  let name = id.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
  if (!/^[a-z]/.test(name)) name = `m-${name}`;
  name = name.slice(0, 64).replace(/-+$/g, "");
  if (name.length < 2) name = `m-${name}`;
  let candidate = name;
  for (let n = 2; taken.has(candidate); n++) candidate = `${name.slice(0, 61)}-${n}`;
  taken.add(candidate);
  return candidate;
}

// Settings writes: with the OIDC triple unset the server treats any caller as
// its fixed admin identity, so an unauthenticated PUT is the expected path;
// retry once with the service credential in case a future upstream tightens
// the settings surface to bearer auth.
async function putSettings(section, manifest) {
  const url = `${TRUEFORGE_URL}/api/v1/settings/${section}`;
  const body = JSON.stringify({ manifest });
  const attempt = (headers) =>
    fetch(url, { method: "PUT", headers: { "Content-Type": "application/json", ...headers }, body });
  let res = await attempt({});
  if ((res.status === 401 || res.status === 403) && TRUEFORGE_API_KEY) {
    res = await attempt({ Authorization: `Bearer ${TRUEFORGE_API_KEY}` });
  }
  if (!res.ok) {
    throw new Error(`PUT ${url} answered ${res.status}: ${(await res.text()).slice(0, 500)}`);
  }
  console.log(`trueforge-init: seeded settings/${section} (${manifest.name})`);
}

await waitForHealthz();

const apiKey = await mintVirtualKey();
const modelIds = await fetchModelIds();
const taken = new Set();
await putSettings("model-providers", {
  type: "custom",
  name: PROVIDER_NAME,
  base_url: `${LITELLM_BASE_URL}/v1`,
  auth: { api_key: apiKey },
  models: modelIds.map((id) => ({ model_id: id, name: resourceName(id, taken), properties: {} })),
});
console.log(`trueforge-init: provider ${PROVIDER_NAME} carries ${modelIds.length} gateway model(s)`);

if (MCP_SERVERS_ENABLED) {
  await putSettings("mcp-servers", {
    type: "remote",
    name: MCP_CONNECTOR_NAME,
    url: "http://mcp-servers:8000/mcp",
    description: "Atlas in-stack MCP tools: Supabase Postgres queries, Neo4j graph queries, and SearXNG web search.",
    // no `auth` block: the in-stack endpoint is unauthenticated on the
    // internal compose network (streamable HTTP at /mcp)
  });
} else {
  console.log("trueforge-init: mcp-servers family disabled — skipping connector seed");
}

console.log("trueforge-init: done");
