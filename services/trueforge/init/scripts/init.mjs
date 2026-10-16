// trueforge-init — seed TrueForge's live settings from the Atlas stack.
//
// Runs on every start (settings replace by name; keys rotate only with
// confirmed retirement):
//   1. wait for the server's /healthz,
//   2. read the gateway's live /v1/models catalog,
//   3. capture settled keys and mint a dedicated pending generation,
//   4. seed one `custom` model provider pointed at LiteLLM,
//   5. confirm the replacement and retire only previously settled keys,
//   6. seed the in-stack mcp-servers connector when that family is enabled.
// Agent traffic normally uses the virtual key (#1159 Q4 mitigation); mint
// failure retains the existing loud master-key fallback. Pending and legacy
// keys remain valid until an operator reconciles their uncertain history.
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

import { createHash, randomUUID } from "node:crypto";

const TRUEFORGE_URL = process.env.TRUEFORGE_URL || "http://trueforge:8790";
const TRUEFORGE_API_KEY = process.env.TRUEFORGE_API_KEY || "";
const LITELLM_BASE_URL = process.env.LITELLM_BASE_URL || "http://litellm:4000";
const LITELLM_MASTER_KEY = process.env.LITELLM_MASTER_KEY || "";
const MCP_SERVERS_ENABLED = (process.env.MCP_SERVERS_SCALE || "0") === "1";

const VIRTUAL_KEY_ALIAS = "trueforge";
const GENERATION_ALIAS = /^trueforge-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const PROVIDER_NAME = "atlas-litellm";
const MCP_CONNECTOR_NAME = "atlas-tools";

const positiveMs = (raw, fallback) => {
  const value = Number(raw);
  return Number.isFinite(value) && value > 0 ? value : fallback;
};
// Total bootstrap budget: generously above the worst honest path (healthz
// wait through first-boot migrations + bounded settings/key reconciliation), far below
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

const ownership = (state) => ({
  service: "trueforge", managed_by: "trueforge-init", rotation_state: state,
});

function keyMetadata(row) {
  if (typeof row?.metadata !== "string") return row?.metadata;
  try { return JSON.parse(row.metadata); } catch { return null; }
}

function isManagedKey(row) {
  const metadata = keyMetadata(row);
  return metadata?.service === "trueforge" && metadata?.managed_by === "trueforge-init"
    && (row.key_alias === VIRTUAL_KEY_ALIAS || GENERATION_ALIAS.test(row.key_alias))
    && typeof row.token === "string" && /^[0-9a-f]{64}$/.test(row.token);
}

async function responseJson(res, operation) {
  try { return await res.json(); } catch {
    // JSON parser messages can include fragments of credential-bearing bodies.
    throw new BootstrapError(operation, new Error("invalid JSON response"));
  }
}

async function readManagedKeys() {
  const operation = "litellm key inventory read";
  const rows = new Map();
  for (let page = 1; ; page++) {
    const res = await litellm(operation, `/key/list?page=${page}&size=100&return_full_object=true&key_alias=${VIRTUAL_KEY_ALIAS}`);
    if (!res.ok) throw new BootstrapError(operation, new Error(`answered ${res.status}`));
    const body = await responseJson(res, operation);
    if (!Array.isArray(body?.keys) || body.keys.length > 100) {
      throw new BootstrapError(operation, new Error("invalid key inventory"));
    }
    for (const row of body.keys) if (isManagedKey(row)) rows.set(row.token, row);
    if (body.keys.length < 100) return [...rows.values()];
  }
}

async function captureSettledKeys() {
  try {
    const hashes = (await readManagedKeys()).filter((row) => GENERATION_ALIAS.test(row.key_alias)
      && keyMetadata(row)?.rotation_state === "confirmed").map((row) => row.token);
    return { hashes, available: true };
  } catch (err) {
    if (shutdown.signal.aborted || remainingBudgetMs() <= 0) throw err;
    // Preserve mint/master fallback when the key-management database is down.
    // An unavailable inventory never supplies any retirement authority.
    console.warn("trueforge-init: WARNING key inventory unavailable; settled-key retirement deferred");
    return { hashes: [], available: false };
  }
}

// A candidate starts pending. Only this run may confirm it, after its sole
// provider PUT definitely succeeds. Never revoke a pending or legacy key:
// an earlier timed-out server PUT can still install that credential later.
async function mintVirtualKey() {
  const alias = `${VIRTUAL_KEY_ALIAS}-${randomUUID()}`;
  try {
    const res = await litellm("litellm key generate", "/key/generate", {
      key_alias: alias,
      metadata: ownership("pending"),
    });
    if (res.ok) {
      const data = await responseJson(res, "litellm key generate");
      if (typeof data?.key === "string" && data.key.length > 0) {
        console.log(`trueforge-init: minted LiteLLM virtual key (alias ${alias})`);
        return { key: data.key, hash: createHash("sha256").update(data.key).digest("hex") };
      }
    }
    console.warn(`trueforge-init: WARNING /key/generate answered ${res.status}; falling back to the master key`);
  } catch (err) {
    if (shutdown.signal.aborted) throw err;
    console.warn(`trueforge-init: WARNING ${err.operation || "key mint"} failed (${err.message}); falling back to the master key`);
  }
  return { key: LITELLM_MASTER_KEY, hash: null };
}

async function confirmVirtualKey(hash) {
  const operation = "litellm key confirmation";
  const res = await litellm(operation, "/key/update", { key: hash, metadata: ownership("confirmed") });
  if (!res.ok) throw new BootstrapError(operation, new Error(`answered ${res.status}`));
  const body = await responseJson(res, operation);
  if (body?.key !== hash || body?.token !== hash || !isManagedKey(body)
      || keyMetadata(body)?.rotation_state !== "confirmed") {
    throw new BootstrapError(operation, new Error("invalid key confirmation"));
  }
}

async function retireSettledKeys(hashes) {
  const operation = "litellm settled key retirement";
  if (hashes.length) {
    const res = await litellm(operation, "/key/delete", { keys: hashes });
    // A different successful run can already have retired these exact hashes.
    if (!res.ok && res.status !== 404) throw new BootstrapError(operation, new Error(`answered ${res.status}`));
  }
  const remaining = await readManagedKeys();
  if (remaining.some((row) => hashes.includes(row.token))) {
    throw new BootstrapError(operation, new Error("captured settled keys remain"));
  }
  console.log(`trueforge-init: retired ${hashes.length} settled LiteLLM virtual key(s)`);
  const protectedCount = remaining.filter((row) => !GENERATION_ALIAS.test(row.key_alias)
    || keyMetadata(row)?.rotation_state !== "confirmed").length;
  if (protectedCount) {
    console.warn(`trueforge-init: WARNING retained ${protectedCount} pending or legacy virtual key(s); operator reconciliation required`);
  }
}

async function fetchModelIds() {
  const res = await litellm("litellm model catalog read", "/v1/models");
  if (!res.ok) {
    throw new BootstrapError("litellm model catalog read", new Error(`answered ${res.status}`));
  }
  const data = await responseJson(res, "litellm model catalog read");
  if (!Array.isArray(data?.data)
      || data.data.some((model) => model == null
        || (model.id && typeof model.id !== "string"))) {
    throw new BootstrapError("litellm model catalog read", new Error("invalid model catalog"));
  }
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
  for (let n = 2; taken.has(candidate); n++) {
    const suffix = `-${n}`;
    // Preserve existing valid names while reserving room for wider suffixes.
    candidate = `${name.slice(0, Math.min(61, 64 - suffix.length))}${suffix}`;
  }
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

async function putSettings(section, manifest, confirmProvider = false) {
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
  if (confirmProvider) {
    // The pinned handler returns 200 after commit. A queued 202 is not proof
    // that this key's sole provider write has finished and is safe to settle.
    if (res.status !== 200) {
      throw new BootstrapError(operation, new Error(`unconfirmed status ${res.status}`));
    }
    const body = await responseJson(res, operation);
    if (!validProviderConfirmation(body?.data, manifest)) {
      throw new BootstrapError(operation, new Error("invalid provider confirmation"));
    }
  }
  console.log(`trueforge-init: seeded settings/${section} (${manifest.name})`);
}

function validProviderConfirmation(data, manifest) {
  const key = manifest.auth.api_key;
  const mask = key.length >= 10 ? `${key.slice(0, 3)}-***REDACTED***-${key.slice(-3)}` : "***REDACTED***";
  const current = data?.manifest;
  return data?.name === manifest.name && current?.name === manifest.name
    && current?.type === "custom" && current?.base_url === manifest.base_url
    && current?.auth?.api_key === mask && Array.isArray(current?.models)
    && current.models.length === manifest.models.length
    && current.models.every((model, index) => model?.model_id === manifest.models[index].model_id
      && model?.name === manifest.models[index].name);
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
  const body = await responseJson(res, operation);
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

  // Freeze retirement eligibility before minting. A confirmed generation's
  // only provider PUT already completed, so it cannot be installed by an
  // outstanding write after this run's later successful PUT. Pagination can
  // omit concurrent rows; it must never expand this set after minting.
  const modelIds = await fetchModelIds();
  const settled = await captureSettledKeys();
  const candidate = await mintVirtualKey();
  const taken = new Set();
  await putSettings("model-providers", {
    type: "custom",
    name: PROVIDER_NAME,
    base_url: `${LITELLM_BASE_URL}/v1`,
    auth: { api_key: candidate.key },
    models: modelIds.map((id) => ({ model_id: id, name: resourceName(id, taken), properties: {} })),
  }, true);
  if (candidate.hash) {
    await confirmVirtualKey(candidate.hash);
    if (settled.available) {
      await retireSettledKeys(settled.hashes.filter((hash) => hash !== candidate.hash));
    }
  }
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
