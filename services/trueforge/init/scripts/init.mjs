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
//
// Every HTTP call is bounded (#1173): one finite total bootstrap budget plus
// a per-request AbortSignal, so a peer that accepts a connection but never
// replies cannot leave this container running (or hold a stale `Up` state)
// indefinitely. SIGTERM/SIGINT abort all outstanding work. Failures exit
// nonzero naming the operation — never a header, credential, or body.
// The *_MS constants are deliberately not user-facing env knobs; the env
// override exists so the bounds tests can exercise tiny budgets.

const TRUEFORGE_URL = process.env.TRUEFORGE_URL || "http://trueforge:8790";
const TRUEFORGE_API_KEY = process.env.TRUEFORGE_API_KEY || "";
const LITELLM_BASE_URL = process.env.LITELLM_BASE_URL || "http://litellm:4000";
const LITELLM_MASTER_KEY = process.env.LITELLM_MASTER_KEY || "";
const MCP_SERVERS_ENABLED = (process.env.MCP_SERVERS_SCALE || "0") === "1";

const VIRTUAL_KEY_ALIAS = "trueforge";
const PROVIDER_NAME = "atlas-litellm";
const MCP_CONNECTOR_NAME = "atlas-tools";

const positiveMs = (raw, fallback) => {
  const value = Number(raw);
  return Number.isFinite(value) && value > 0 ? value : fallback;
};
// Total bootstrap budget: generously above the worst honest path (healthz
// wait through first-boot migrations + four short API calls), far below
// "runs forever".
const TOTAL_BUDGET_MS = positiveMs(process.env.TRUEFORGE_INIT_TOTAL_BUDGET_MS, 300_000);
const REQUEST_TIMEOUT_MS = positiveMs(process.env.TRUEFORGE_INIT_REQUEST_TIMEOUT_MS, 15_000);
const HEALTH_PROBE_TIMEOUT_MS = positiveMs(process.env.TRUEFORGE_INIT_HEALTH_TIMEOUT_MS, 3_000);
const HEALTH_PROBE_INTERVAL_MS = positiveMs(process.env.TRUEFORGE_INIT_HEALTH_INTERVAL_MS, 2_000);

const startedAt = Date.now();
const remainingBudgetMs = () => TOTAL_BUDGET_MS - (Date.now() - startedAt);

// Shutdown propagation: aborting this controller rejects every in-flight
// fetch below, so a signal never has to wait out a hung request.
const shutdown = new AbortController();
let shutdownSignalName = "";
for (const signalName of ["SIGTERM", "SIGINT"]) {
  process.on(signalName, () => {
    shutdownSignalName = signalName;
    shutdown.abort(new Error(`received ${signalName}`));
  });
}

class BootstrapError extends Error {
  constructor(operation, cause) {
    // Never interpolate request bodies, headers, or credentials here — the
    // operation name plus the transport error name is enough to reconcile.
    super(cause?.name === "TimeoutError" ? "timed out" : String(cause?.message || cause));
    this.operation = operation;
  }
}

// One bounded fetch: per-request timeout, capped by what is left of the
// total budget, and cut short by shutdown. Throws BootstrapError with the
// operation name; secrets never appear in the message.
async function boundedFetch(operation, url, options = {}, timeoutMs = REQUEST_TIMEOUT_MS) {
  const remaining = remainingBudgetMs();
  if (remaining <= 0) {
    throw new BootstrapError(operation, new Error(`total bootstrap budget (${TOTAL_BUDGET_MS} ms) exhausted`));
  }
  const signal = AbortSignal.any([
    shutdown.signal,
    AbortSignal.timeout(Math.min(timeoutMs, remaining)),
  ]);
  try {
    return await fetch(url, { ...options, signal });
  } catch (cause) {
    if (shutdown.signal.aborted) {
      throw new BootstrapError(operation, new Error(`aborted by ${shutdownSignalName || "shutdown"}`));
    }
    throw new BootstrapError(operation, cause);
  }
}

const sleep = (ms) =>
  new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      shutdown.signal.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    const onAbort = () => {
      clearTimeout(timer);
      reject(new BootstrapError("wait", new Error(`aborted by ${shutdownSignalName || "shutdown"}`)));
    };
    shutdown.signal.addEventListener("abort", onAbort, { once: true });
  });

// Health probes are safe reads — the one place retrying is correct. Both the
// per-probe signal and the loop itself stay inside the total budget.
async function waitForHealthz() {
  while (true) {
    try {
      const res = await boundedFetch("healthz probe", `${TRUEFORGE_URL}/healthz`, {}, HEALTH_PROBE_TIMEOUT_MS);
      if (res.ok) return;
    } catch (err) {
      if (shutdown.signal.aborted || remainingBudgetMs() <= 0) throw err;
      // transport error or per-probe timeout — the retry sleep follows
    }
    if (remainingBudgetMs() <= HEALTH_PROBE_INTERVAL_MS) {
      throw new BootstrapError(
        "healthz wait",
        new Error(`server never became healthy within the ${TOTAL_BUDGET_MS} ms bootstrap budget`),
      );
    }
    await sleep(HEALTH_PROBE_INTERVAL_MS);
  }
}

function litellm(operation, path, body) {
  return boundedFetch(operation, `${LITELLM_BASE_URL}${path}`, {
    method: body === undefined ? "GET" : "POST",
    headers: {
      Authorization: `Bearer ${LITELLM_MASTER_KEY}`,
      "Content-Type": "application/json",
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

// Mint (or rotate) the dedicated virtual key. Virtual keys are not readable
// back in plaintext, so idempotency is delete-alias-then-generate; the fresh
// key immediately replaces the stored provider credential below. Credential
// writes are never retried within a run (#1173): a timed-out /key/generate
// has an uncertain outcome, and retrying could mint duplicates — the
// alias-scoped delete on the NEXT start reconciles any orphan, so this run
// falls back to the master key with a loud warning instead.
async function mintVirtualKey() {
  try {
    await litellm("litellm key delete", "/key/delete", { key_aliases: [VIRTUAL_KEY_ALIAS] });
  } catch (err) {
    if (shutdown.signal.aborted) throw err;
    // best-effort: alias may not exist yet, or an older LiteLLM may not
    // support alias-addressed deletes — /key/generate below is the arbiter
  }
  try {
    const res = await litellm("litellm key generate", "/key/generate", {
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
    if (shutdown.signal.aborted) throw err;
    console.warn(`trueforge-init: WARNING ${err.operation || "key mint"} failed (${err.message}); falling back to the master key`);
  }
  return LITELLM_MASTER_KEY;
}

async function fetchModelIds() {
  const res = await litellm("litellm model catalog read", "/v1/models");
  if (!res.ok) {
    throw new BootstrapError("litellm model catalog read", new Error(`answered ${res.status}`));
  }
  const data = await res.json();
  const ids = (data.data || []).map((m) => m.id).filter(Boolean);
  if (ids.length === 0) {
    throw new BootstrapError(
      "litellm model catalog read",
      new Error("returned no models — cannot seed a provider with an empty catalog"),
    );
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

// Settings requests: with the OIDC triple unset the server treats any caller
// as its fixed admin identity, so an unauthenticated call is the expected
// path; retry once with the service credential in case a future upstream
// tightens the settings surface to bearer auth. Safe for reads and for PUT
// (replace-by-name, idempotent).
async function settingsRequest(operation, path, init = {}) {
  const url = `${TRUEFORGE_URL}${path}`;
  const attempt = (headers) =>
    boundedFetch(operation, url, { ...init, headers: { ...(init.headers || {}), ...headers } });
  let res = await attempt({});
  if ((res.status === 401 || res.status === 403) && TRUEFORGE_API_KEY) {
    res = await attempt({ Authorization: `Bearer ${TRUEFORGE_API_KEY}` });
  }
  return res;
}

async function putSettings(section, manifest) {
  const operation = `settings ${section} write`;
  const res = await settingsRequest(operation, `/api/v1/settings/${section}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ manifest }),
  });
  if (!res.ok) {
    // Status code only — response bodies of failed writes stay out of logs.
    throw new BootstrapError(operation, new Error(`answered ${res.status}`));
  }
  console.log(`trueforge-init: seeded settings/${section} (${manifest.name})`);
}

// The pinned v0.2.0 settings API has no DELETE and no enabled flag for MCP
// connectors (GET/POST/PUT only — verified against docs/openapi.json), so
// "remove" is not expressible. Reconciliation therefore parks the
// Atlas-managed connector on a reserved, guaranteed-unresolvable address
// (RFC 2606 `.invalid`) with a marked description: the catalog clearly shows
// it disabled instead of offering a dead-but-normal-looking tool. Keeping
// the NAME configured is also the gentler choice for saved agents — upstream
// rejects session creation outright when a referenced connector name is
// absent (`Unknown MCP server … — not configured`,
// packages/trueforge/src/runtime/sessionResources.ts:333 at the pinned
// commit), while a parked connector lets sessions start and only the tool
// calls fail, bounded by MCP_CONNECT_TIMEOUT_MS. Only the connector named
// `atlas-tools` is ever touched: that name is Atlas-owned, and user-created
// connectors under other names are never read or written.
const DISABLED_CONNECTOR_URL = "http://atlas-mcp-servers.disabled.invalid/mcp";

async function reconcileDisabledMcpConnector() {
  const operation = "mcp connector reconcile read";
  const res = await settingsRequest(operation, `/api/v1/settings/mcp-servers/${MCP_CONNECTOR_NAME}`);
  if (res.status === 404) {
    console.log("trueforge-init: mcp-servers family disabled — no managed connector to reconcile");
    return;
  }
  if (!res.ok) {
    throw new BootstrapError(operation, new Error(`answered ${res.status}`));
  }
  const body = await res.json();
  const current = body?.data?.manifest;
  if (current?.url === DISABLED_CONNECTOR_URL) {
    console.log("trueforge-init: mcp-servers family disabled — managed connector already parked");
    return;
  }
  await putSettings("mcp-servers", {
    type: "remote",
    name: MCP_CONNECTOR_NAME,
    url: DISABLED_CONNECTOR_URL,
    description:
      "[disabled by Atlas] The mcp-servers family is disabled in this stack (MCP_SERVERS_SOURCE=disabled), so this managed connector is parked on an unresolvable address. Re-enable mcp-servers to restore it. Saved agents still start; calls to these tools fail at connect time.",
  });
  console.log("trueforge-init: mcp-servers family disabled — managed connector parked");
}

async function main() {
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
    await reconcileDisabledMcpConnector();
  }

  console.log("trueforge-init: done");
}

try {
  await main();
} catch (err) {
  const operation = err instanceof BootstrapError ? err.operation : "bootstrap";
  console.error(`trueforge-init: FAILED at ${operation}: ${err.message}`);
  process.exit(1);
}
