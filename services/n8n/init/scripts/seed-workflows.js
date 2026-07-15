#!/usr/bin/env node
/*
 * seed-workflows.js — Atlas consumer n8n workflow seeding (#412).
 *
 * Runs in the Atlas-owned `n8n-seed` container (the n8n image, so it has the
 * `n8n` CLI + node + the n8n schema). All HTTP is done with node's http/https
 * (NOT wget): the n8n image is Alpine/BusyBox and its wget has no `--method`,
 * so a shell `wget --method=POST` would silently fail every API call.
 *
 * Flow (after n8n is healthy):
 *   1. `n8n import:workflow` each normalized file — idempotent upsert keyed by
 *      the Atlas-namespaced `seed_id` (can't collide with a user/stack workflow).
 *   2. Activate active workflows via the public API when N8N_API_KEY is set
 *      (registers the production webhook on the RUNNING instance, no restart).
 *   3. Reconcile: delete any `atlas-consumer-*` workflow no longer declared
 *      (a since-removed manifest entry) so removal doesn't orphan a live webhook.
 *   4. Probe declared webhooks for readiness (opt-in; POST only when probe:true).
 *
 * Best-effort: a per-workflow failure is logged + isolated and NEVER fails the
 * stack launch (exit 0), so one bad consumer workflow can't abort `compose up
 * --wait`. Secrets: workflow bodies are never printed — only ids/paths/statuses.
 */
'use strict';

const fs = require('fs');
const { spawnSync } = require('child_process');
const { URL } = require('url');

const PLAN = process.env.N8N_SEED_PLAN || '/consumer-workflows/plan.json';
const BASE = (process.env.N8N_SEED_BASE_URL || 'http://n8n:5678').replace(/\/+$/, '');
const KEY = process.env.N8N_API_KEY || '';

function positiveMilliseconds(name, fallback) {
  const value = Number.parseInt(process.env[name] || '', 10);
  return Number.isFinite(value) && value > 0 ? value : fallback;
}

const HTTP_TIMEOUT_MS = positiveMilliseconds('N8N_SEED_HTTP_TIMEOUT_MS', 10000);
const COMMAND_TIMEOUT_MS = positiveMilliseconds('N8N_SEED_COMMAND_TIMEOUT_MS', 120000);
const MAX_RESPONSE_BYTES = positiveMilliseconds('N8N_SEED_MAX_RESPONSE_BYTES', 1048576);

const log = (m) => console.log('n8n-seed: ' + m);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function request(method, path, opts) {
  opts = opts || {};
  return new Promise((resolve) => {
    let settled = false;
    let deadlineTimer;
    let response;
    const finish = (result) => {
      if (!settled) {
        settled = true;
        if (deadlineTimer) clearTimeout(deadlineTimer);
        resolve(result);
      }
    };
    let u;
    try {
      u = new URL(BASE + path);
    } catch (e) {
      finish({ status: 0, body: '' });
      return;
    }
    const lib = u.protocol === 'https:' ? require('https') : require('http');
    const headers = Object.assign({}, opts.headers || {});
    if (opts.body) headers['content-length'] = Buffer.byteLength(opts.body);
    const req = lib.request(
      { method, hostname: u.hostname, port: u.port, path: u.pathname + u.search, headers },
      (res) => {
        response = res;
        let data = '';
        let received = 0;
        const declared = Number.parseInt(res.headers['content-length'] || '', 10);
        if (Number.isFinite(declared) && declared > MAX_RESPONSE_BYTES) {
          res.destroy(new Error('n8n seed HTTP response exceeded byte limit'));
          finish({ status: 0, body: '' });
          return;
        }
        res.on('data', (c) => {
          received += c.length;
          if (received > MAX_RESPONSE_BYTES) {
            res.destroy(new Error('n8n seed HTTP response exceeded byte limit'));
            finish({ status: 0, body: '' });
            return;
          }
          data += c;
        });
        res.on('end', () => finish({ status: res.statusCode || 0, body: data }));
        res.on('error', () => finish({ status: 0, body: '' }));
      }
    );
    const timeoutMs = opts.timeoutMs || HTTP_TIMEOUT_MS;
    const timeoutError = () => new Error('n8n seed HTTP request timed out');
    deadlineTimer = setTimeout(() => {
      if (response) response.destroy(timeoutError());
      req.destroy(timeoutError());
      finish({ status: 0, body: '' });
    }, timeoutMs);
    req.setTimeout(timeoutMs, () => {
      req.destroy(new Error('n8n seed HTTP request timed out'));
    });
    req.on('error', () => finish({ status: 0, body: '' }));
    if (opts.body) req.write(opts.body);
    req.end();
  });
}

const ok = (s) => s >= 200 && s < 300;
const authHeaders = () => ({ 'X-N8N-API-KEY': KEY, 'accept': 'application/json' });

async function waitHealthy(timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const remaining = deadline - Date.now();
    if (remaining <= 0) return false;
    const r = await request('GET', '/healthz', {
      timeoutMs: Math.min(HTTP_TIMEOUT_MS, remaining),
    });
    if (ok(r.status)) return true;
    const pause = Math.min(5000, deadline - Date.now());
    if (pause <= 0) return false;
    await sleep(pause);
  }
}

function runCommand(command, args, timeoutMs) {
  return spawnSync(command, args, {
    stdio: ['ignore', 'ignore', 'pipe'],
    timeout: timeoutMs || COMMAND_TIMEOUT_MS,
    killSignal: 'SIGKILL',
  });
}

function effectiveActive(wf) {
  if (wf.active === 'true') return true;
  if (wf.active === 'false') return false;
  // fromJson: read the normalized file's own active flag.
  try {
    return !!JSON.parse(fs.readFileSync(wf.file, 'utf8')).active;
  } catch (e) {
    return false;
  }
}

async function removeOrphan(workflowId) {
  const encoded = encodeURIComponent(workflowId);
  const deactivate = await request('POST', `/api/v1/workflows/${encoded}/deactivate`, {
    headers: authHeaders(),
  });
  if (!ok(deactivate.status)) {
    log(`WARN - orphan '${workflowId}' deactivation returned HTTP ${deactivate.status || 'none'}`);
  }
  const deleted = await request('DELETE', `/api/v1/workflows/${encoded}`, {
    headers: authHeaders(),
  });
  if (!ok(deleted.status)) {
    log(`WARN - orphan '${workflowId}' deletion returned HTTP ${deleted.status || 'none'}; workflow remains unreconciled`);
    return false;
  }
  log(`✓ reconciled: removed orphaned workflow '${workflowId}'`);
  return true;
}

async function main() {
  if (!fs.existsSync(PLAN)) {
    log('no plan at ' + PLAN + ' — nothing to seed');
    return;
  }
  let plan;
  try {
    plan = JSON.parse(fs.readFileSync(PLAN, 'utf8'));
  } catch (e) {
    log('ERROR - could not parse plan ' + PLAN + ': ' + e.message);
    return;
  }
  const workflows = plan.workflows || [];
  const namespace = plan.namespace || 'atlas-consumer-';

  log('waiting for n8n at ' + BASE + '/healthz ...');
  if (!(await waitHealthy(300000))) {
    log('ERROR - n8n not healthy after 300s; skipping seeding (stack not aborted)');
    return;
  }
  log('n8n is healthy. seeding ' + workflows.length + ' consumer workflow(s).');

  let imported = 0;
  let failed = 0;
  for (const wf of workflows) {
    if (!fs.existsSync(wf.file)) {
      log(`ERROR - workflow '${wf.id}' (owner=${wf.consumer}) file missing: ${wf.file} — skipping`);
      failed++;
      continue;
    }
    // Idempotent upsert keyed by the namespaced seed_id baked into the file.
    const res = runCommand('n8n', ['import:workflow', '--input=' + wf.file]);
    if (res.status === 0) {
      log(`✓ imported '${wf.id}' (owner=${wf.consumer})`);
      imported++;
    } else {
      log(`ERROR - import failed for '${wf.id}' (owner=${wf.consumer}) — skipping others unaffected`);
      failed++;
      continue;
    }

    if (effectiveActive(wf)) {
      if (KEY) {
        const a = await request('POST', `/api/v1/workflows/${encodeURIComponent(wf.seed_id)}/activate`, {
          headers: authHeaders(),
        });
        if (ok(a.status)) {
          log(`✓ activated '${wf.id}' via API (production webhook registered)`);
        } else {
          log(`WARN - activation of '${wf.id}' returned HTTP ${a.status || 'none'}; production webhook NOT registered`);
        }
      } else {
        log(`note: '${wf.id}' active but N8N_API_KEY unset — production webhook registers on next n8n restart`);
      }
    }
  }

  // Reconcile removed workflows (Atlas owns the atlas-consumer-* id namespace).
  if (KEY) {
    const declared = new Set(workflows.map((w) => w.seed_id));
    // The n8n public API caps limit at 250 and paginates the rest via
    // nextCursor. Follow every page — a partial (first-page-only) list would
    // silently miss orphans on instances with >250 total workflows.
    const all = [];
    let cursor = '';
    let listOk = true;
    for (let guard = 0; guard < 1000; guard++) {
      const q = '/api/v1/workflows?limit=250' + (cursor ? '&cursor=' + encodeURIComponent(cursor) : '');
      const page = await request('GET', q, { headers: authHeaders() });
      if (!ok(page.status)) {
        log(`WARN - could not list workflows for reconcile (HTTP ${page.status || 'none'}); orphans not cleaned this run`);
        listOk = false;
        break;
      }
      let body = {};
      try {
        body = JSON.parse(page.body) || {};
      } catch (e) {
        body = {};
      }
      for (const w of body.data || []) all.push(w);
      cursor = body.nextCursor || '';
      if (!cursor) break;
    }
    if (listOk) {
      for (const w of all) {
        if (typeof w.id === 'string' && w.id.startsWith(namespace) && !declared.has(w.id)) {
          await removeOrphan(w.id);
        }
      }
    }
  } else {
    log('note: N8N_API_KEY unset — cannot reconcile removed workflows; orphaned atlas-consumer-* workflows persist until a key is set');
  }

  // Coalesced webhook readiness probes (opt-in only; POST probes are
  // side-effecting and were explicitly probe:true in the manifest).
  for (const wf of workflows) {
    for (const p of (wf.webhooks || []).filter((x) => x.probe)) {
      const r = await request(p.method, p.path);
      if (r.status === p.expect_status) {
        log(`✓ webhook ready: ${p.method} ${p.path} → ${r.status}`);
      } else {
        log(`WARN - webhook ${p.method} ${p.path} returned ${r.status || 'none'}, expected ${p.expect_status}`);
      }
    }
  }

  log(`seed summary: imported=${imported} failed=${failed}`);
}

module.exports = { removeOrphan, request, runCommand, waitHealthy };

if (require.main === module) {
  main()
    .then(() => process.exit(0))
    .catch((e) => {
      log('ERROR - ' + (e && e.message ? e.message : e));
      // Best-effort: never abort the stack launch on a seeding error.
      process.exit(0);
    });
}
