"""Automatically qualify the optional, pinned recipe on macOS ARM64."""
from pathlib import Path
import argparse
import email.parser
import importlib.util
import json
import os
import platform
import re
import sys

RECIPE = Path(__file__).resolve().parent


def adjacent(name):
    path = RECIPE / (name + ".py")
    spec = importlib.util.spec_from_file_location("_atlas_promptfoo_" + name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ["node", "python", "install-root", "output-base"]:
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    if any(not Path(value).is_absolute() for value in vars(args).values()):
        parser.error("all four inputs must be absolute paths")
    return args


def fresh_roots(value):
    raw = Path(value)
    base = raw.parent.resolve(strict=True) / raw.name
    inputs = base.with_name(base.name + ".inputs")
    if base.exists() or base.is_symlink() or inputs.exists() or inputs.is_symlink():
        raise ValueError("output base and generated-input sibling must both be new")
    base.mkdir(mode=0o700)
    inputs.mkdir(mode=0o700)
    for name in ["qualification", "evaluation"]:
        (base / name).mkdir(mode=0o700)
    for name in ["home", "tmp", "config", "cache"]:
        (base / "qualification" / name).mkdir(mode=0o700)
    if base.stat().st_uid != os.getuid() or base.stat().st_mode & 0o077:
        raise ValueError("output base must be owned and private")
    return {"output": base, "inputs": inputs}


def package_inputs(args, runtime):
    install = Path(args.install_root).resolve(strict=True)
    for name in ["package.json", "package-lock.json"]:
        if runtime.digest(install / name) != runtime.digest(RECIPE / name):
            raise ValueError("isolated npm install differs from the pinned recipe: " + name)
    lock = json.loads((install / "package-lock.json").read_text())
    for relative, row in lock["packages"].items():
        if not relative:
            continue
        metadata = install / relative / "package.json"
        if metadata.is_file():
            if json.loads(metadata.read_text())["version"] != row["version"]:
                raise ValueError("installed npm version differs from lock: " + relative)
        elif not (row.get("optional", False) or row.get("extraneous", False)):
            raise ValueError("required npm package missing: " + relative)
    return install


def python_pins(paths):
    lock = RECIPE / "requirements-auth-macos-arm64-py312.lock"
    normal = lambda value: re.sub(r"[-_.]+", "-", value).lower()
    pairs = re.findall(r"^([A-Za-z0-9_.-]+)==([^\s]+)", lock.read_text(), re.M)
    expected = {normal(name): version for name, version in pairs}
    actual = {}
    for path in (paths["venv"] / "lib/python3.12/site-packages").glob("*.dist-info/METADATA"):
        metadata = email.parser.Parser().parsestr(path.read_text())
        actual[normal(metadata["Name"])] = metadata["Version"]
    if not expected or actual != expected:
        raise ValueError("isolated auth distributions differ from the pinned Python lock")
    return lock


def environment(base):
    root = base / "qualification"
    return {"PATH": "/usr/bin:/bin", "LANG": "C", "TZ": "UTC", "NO_COLOR": "1",
            "HOME": str(root / "home"), "TMPDIR": str(root / "tmp"),
            "XDG_CACHE_HOME": str(root / "cache"), "PROMPTFOO_CONFIG_DIR": str(root / "config"),
            "PROMPTFOO_DISABLE_TELEMETRY": "1", "PROMPTFOO_DISABLE_UPDATE": "1",
            "PROMPTFOO_DISABLE_SHARING": "1", "PROMPTFOO_DISABLE_REMOTE_GENERATION": "1",
            "PROMPTFOO_TRACING_ENABLED": "false", "PROMPTFOO_FAILED_TEST_EXIT_CODE": "100"}


def prepare(args, modules):
    profile, runtime = modules["profile"], modules["runtime"]
    install = package_inputs(args, runtime)
    paths = profile.required_paths(args, RECIPE)
    python_lock = python_pins(paths)
    roots = fresh_roots(args.output_base)
    text, metadata = profile.profile_for(paths, roots)
    generated = roots["inputs"]
    profile_path = generated / "profile.sb"
    profile_path.write_text(text)
    python_inventory = runtime.snapshot(paths["venv"])
    npm_inventory = runtime.snapshot(paths["npm"])
    if any(not Path(name).resolve().is_relative_to(paths["npm"]) for name in npm_inventory["symlinks"]):
        raise ValueError("npm symlink escapes the isolated installation")
    runtime.save(generated / "python-inventory.json", python_inventory)
    runtime.save(generated / "python-base-inventory.json", runtime.snapshot(paths["base"]))
    runtime.save(generated / "npm-inventory.json", npm_inventory)
    runtime.save(generated / "runtime-inventory.json", metadata)
    identity_hash = runtime.digest(paths["identity"])
    runtime.save(generated / "helper-bindings.json", {"backend_identity": str(paths["identity"]),
        "file_hashes": {str(paths["identity"]): identity_hash}})
    prefix = ["/usr/bin/sandbox-exec", "-D", "OUTPUT=" + str(roots["output"]), "-f", str(profile_path)]
    return {"recipe": RECIPE, "paths": paths, "base": roots["output"], "inputs": generated,
            "profile": profile_path, "prefix": prefix, "env": environment(roots["output"]),
            "stages": [], "install": install, "python_lock": python_lock}


def helper_and_version(context, runtime):
    paths, inputs = context["paths"], context["inputs"]
    context.update(timeout=10, input=None)
    helper = RECIPE / "identity_helper.py"
    command = [str(paths["node"]), str(RECIPE / "helper_probe.cjs"), str(paths["python"]),
               str(helper), str(inputs / "helper-bindings.json")]
    proof = runtime.require_success(runtime.execute(context["prefix"] + command, context))
    command = context["prefix"] + [str(paths["node"]), str(paths["cli"]), "--version"]
    version = runtime.execute(command, context)
    if version["exit_code"] or version["stop"] or version.get("stderr"):
        raise RuntimeError("contained CLI startup failed: " + json.dumps(version))
    if not re.search(r"(?m)^0\.124\.1$", version["stdout"]):
        raise RuntimeError("actual contained CLI does not report pinned version0.124.1")
    runtime.save(inputs / "startup.json", {"actual_helper": proof, "cli": version})


def all_denied(rows, expected):
    return len(rows) == expected and all(not row["ok"] and not row["timed_out"] for row in rows)


def actual_checks(inputs):
    network = json.loads((inputs / "network.json").read_text())
    startup = json.loads((inputs / "startup.json").read_text())
    denied = network["denied_rows"]
    flags = {}
    for operation in ["tcp", "dns", "udp"]:
        rows = [row for row in denied if row["operation"] == operation]
        flags["direct_" + operation + "_denied"] = all_denied(rows, 3)
    children = [row for row in denied if row["actor"] in ["node-child", "python-child"]]
    flags["child_network_denied"] = all_denied(children, 6)
    flags["profile_applied"] = network["classification"] == "NETWORK_CONTAINMENT_PASS"
    flags["local_python_pipes_work"] = startup["actual_helper"]["mode"] == "qualification"
    return flags


def runtime_hashes(inputs, runtime):
    metadata = json.loads((inputs / "runtime-inventory.json").read_text())
    expected = {path: row["sha256"] for path, row in metadata["runtime_files"].items()}
    expected.update(metadata["openssl_configs"])
    for path, checksum in expected.items():
        if runtime.digest(path) != checksum:
            raise ValueError("qualified runtime input changed: " + path)
    for path, target in metadata["runtime_symlinks"].items():
        if not Path(path).is_symlink() or os.readlink(path) != target:
            raise ValueError("qualified runtime symlink changed: " + path)
    return expected


def emit_bindings(context, runtime):
    paths, inputs = context["paths"], context["inputs"]
    values = {"node": paths["node"], "python": paths["python"], "cli": paths["cli"],
              "backend_identity": paths["identity"], "helper": RECIPE / "identity_helper.py",
              "provider": RECIPE / "local-provider.cjs", "cases": RECIPE / "cases.json",
              "package_json": context["install"] / "package.json", "package_lock": context["install"] / "package-lock.json",
              "python_lock": context["python_lock"], "python_inventory": inputs / "python-inventory.json",
              "npm_inventory": inputs / "npm-inventory.json",
              "python_base_inventory": inputs / "python-base-inventory.json",
              "runtime_inventory": inputs / "runtime-inventory.json"}
    checks = actual_checks(inputs)
    if not all(checks.values()):
        raise RuntimeError("actual isolation evidence is incomplete")
    receipt = {"status": "QUALIFIED", "launch_prefix": context["prefix"],
               "profile_sha256": runtime.digest(context["profile"]), "write_root": str(context["base"]),
               "runtime_sha256": {key: runtime.digest(values[key]) for key in ["node", "python", "cli"]},
               "checks": checks, "host": {"system": platform.platform(), "machine": platform.machine()},
               "stages": context["stages"], "evidence": {name: runtime.digest(inputs / name) for name in ["network.json", "startup.json"]},
               "limits": ["synthetic owned loopback controls; system getaddrinfo not exercised", "no provider or authorization case has run"]}
    runtime.save(inputs / "qualification.json", receipt)
    required = list(values.values()) + list(RECIPE.glob("*.py")) + list(RECIPE.glob("*.cjs"))
    required += [Path("/usr/bin/sandbox-exec"), context["profile"], inputs / "qualification.json", inputs / "runtime-inventory.json", inputs / "network.json", inputs / "startup.json"]
    bindings = {"state": "QUALIFIED_FOR_EXACT_INPUTS", "output_base": str(context["base"]),
                "run_root": str(context["base"] / "evaluation"), **{key: str(value) for key, value in values.items()},
                "sandbox": {"launch_prefix": context["prefix"], "profile": str(context["profile"]),
                            "qualification_receipt": str(inputs / "qualification.json")},
                "file_hashes": {**{str(path): runtime.digest(path) for path in required}, **runtime_hashes(inputs, runtime)}}
    runtime.save(inputs / "bindings.json", bindings)
    return inputs / "bindings.json"


def main():
    modules = {name: adjacent("host_" + name) for name in ["profile", "runtime", "network"]}
    context = None
    try:
        context = prepare(arguments(), modules)
        modules["network"].qualify(context, modules["runtime"])
        helper_and_version(context, modules["runtime"])
        result = emit_bindings(context, modules["runtime"])
        print(str(result))
    except Exception as error:
        if context:
            modules["runtime"].save(context["inputs"] / "qualification-failure.json",
                {"error": repr(error), "stages": context["stages"], "output_base": str(context["base"])})
        raise SystemExit("qualification failed; no evaluation admitted: " + str(error))


if __name__ == "__main__":
    main()
