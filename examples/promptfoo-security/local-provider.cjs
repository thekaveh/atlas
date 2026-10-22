"use strict";
// Local deterministic fixture adapter. No model, HTTP, shell or viewer.
const { spawnSync } = require("node:child_process");
const { appendFileSync } = require("node:fs");
const clipped = (value, limit) => {
  const text = Buffer.from(value || "").subarray(0, limit).toString("utf8");
  // A split UTF-8 sequence can expand to a replacement character on decoding.
  return Buffer.byteLength(text, "utf8") <= limit ? text : text.slice(0, -1);
};
module.exports = class LocalAuthorizationProvider {
  constructor(options) { this.config = options.config; }
  id() { return "atlas-synthetic-authorization"; }
  async callApi(prompt, context) {
    const caseId = context.vars.case_id;
    if (typeof caseId !== "string") throw new Error("missing fixed case id");
    const child = spawnSync(this.config.python,
      ["-I", "-B", this.config.helper, this.config.bindings], {
        input: JSON.stringify({ case_id: caseId }), encoding: "utf8",
        shell: false, timeout: 5000, maxBuffer: 65536, windowsHide: true,
        // Inherit only the runner's explicit synthetic environment.
        env: process.env, cwd: process.cwd(),
      });
    const fail = (kind, error) => {
      // Synthetic/private inputs only. Preserve bounded startup/helper evidence;
      // it is never treated as the deliberately failed authorization assertion.
      const diagnostic = { kind, case_id: caseId, exit_code: child.status,
        signal: child.signal, error: error ? {
          name: error.name, code: error.code, message: clipped(error.message, 1024),
        } : null, stderr: clipped(child.stderr, 4096), stdout: clipped(child.stdout, 1024) };
      appendFileSync(this.config.diagnostics, JSON.stringify(diagnostic) + "\n", { mode: 0o600 });
      throw new Error("local ownership helper failed (not a seeded assertion): " + JSON.stringify(diagnostic));
    };
    if (child.error || child.status !== 0) fail("helper-process-failure", child.error);
    let output;
    try { output = JSON.parse(child.stdout); }
    catch (error) { fail("helper-invalid-json", error); }
    return { output, cached: false, cost: 0 };
  }
};
