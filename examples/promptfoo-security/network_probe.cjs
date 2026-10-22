"use strict";
const net = require('node:net');
const dgram = require('node:dgram');
const dns = require('node:dns');
const cp = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const cfg = JSON.parse(fs.readFileSync(0, 'utf8'));
const actor = process.argv[2];
if (!['node-parent', 'node-child'].includes(actor) || cfg.host !== '127.0.0.1') throw new Error('invalid local probe input');
function result(op, ok, error, timedOut = false) {
  return { actor, operation: op, ok, code: error ? String(error.code || error.name) : null, message: error ? String(error.message).slice(0, 512) : null, timed_out: timedOut };
}
function tcp() {
  return new Promise(resolve => {
    let s, done = false;
    const timer = setTimeout(() => finish(false, new Error('probe timeout'), true), 500);
    function finish(ok, err, timeout = false) { if (done) return; done = true; clearTimeout(timer); if (s) s.destroy(); resolve(result('tcp', ok, err, timeout)); }
    try {
      const payload = `atlas-1213:${actor}:tcp`;
      s = net.createConnection({ host: cfg.host, port: cfg.tcp }, () => s.write(payload));
      let received = '';
      s.on('data', b => { received += b.toString(); if (received.length >= payload.length) finish(received === payload, null); });
      s.once('error', e => finish(false, e));
    } catch (e) { finish(false, e); }
  });
}
function udp() {
  return new Promise(resolve => {
    let s, done = false;
    const timer = setTimeout(() => finish(false, new Error('probe timeout'), true), 500);
    function finish(ok, err, timeout = false) { if (done) return; done = true; clearTimeout(timer); if (s) { try { s.close(); } catch (_) {} } resolve(result('udp', ok, err, timeout)); }
    try {
      const payload = `atlas-1213:${actor}:udp`;
      s = dgram.createSocket('udp4');
      s.once('message', b => finish(b.toString() === payload, null));
      s.once('error', e => finish(false, e));
      s.send(Buffer.from(payload), cfg.udp, cfg.host, e => { if (e) finish(false, e); });
    } catch (e) { finish(false, e); }
  });
}
function queryDns() {
  return new Promise(resolve => {
    let resolver, done = false;
    const timer = setTimeout(() => finish(false, new Error('probe timeout'), true), 600);
    function finish(ok, err, timeout = false) { if (done) return; done = true; clearTimeout(timer); if (resolver) resolver.cancel(); resolve(result('dns', ok, err, timeout)); }
    try {
      resolver = new dns.Resolver({ timeout: 400, tries: 1 });
      resolver.setServers([`${cfg.host}:${cfg.dns}`]);
      resolver.resolve4(`${actor}.qualification.invalid`, (e, ips) => finish(!e && ips.length === 1 && ips[0] === '127.0.0.1', e));
    } catch (e) { finish(false, e); }
  });
}
function child(executable, args) {
  const c = cp.spawnSync(executable, args, { input: JSON.stringify(cfg), encoding: 'utf8', stdio: ['pipe', 'pipe', 'pipe'], shell: false, env: { PATH: '/usr/bin:/bin', LANG: 'C', HOME: process.env.HOME, TMPDIR: process.env.TMPDIR }, timeout: 2200, maxBuffer: 16384, killSignal: 'SIGKILL' });
  if (c.error || c.signal || c.status !== 0 || c.stderr) throw new Error(JSON.stringify({ child_error: c.error ? String(c.error) : null, status: c.status, signal: c.signal, stderr: String(c.stderr || '').slice(0, 4096) }));
  return JSON.parse(c.stdout);
}
(async () => {
  let rows = [await tcp(), await udp(), await queryDns()];
  if (actor === 'node-parent') {
    rows = rows.concat(child(process.execPath, [__filename, 'node-child']));
    rows = rows.concat(child(cfg.python, ['-I', '-S', '-B', path.join(__dirname, 'network_probe.py')]));
  }
  process.stdout.write(JSON.stringify(rows) + '\n');
})().catch(e => { process.stderr.write(String(e.stack).slice(0, 8192) + '\n'); process.exitCode = 1; });
