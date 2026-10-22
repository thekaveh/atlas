"""Build the tested macOS ARM64 recipe profile from actual local runtime inputs."""
from pathlib import Path
import hashlib
import json
import os
import platform
import subprocess
import sys


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def otool(path, flag):
    result = subprocess.run(["/usr/bin/otool", flag, str(path)], capture_output=True, timeout=5)
    if result.returncode or len(result.stdout) > 1024 * 1024:
        raise RuntimeError("bounded otool inspection failed: " + str(path))
    return result.stdout.decode().splitlines()


def load_commands(path):
    paths, identity = [], None
    lines = otool(path, "-l")
    for index, line in enumerate(lines):
        command = line.strip()
        if command not in {"cmd LC_RPATH", "cmd LC_ID_DYLIB"}:
            continue
        prefix = "path " if command == "cmd LC_RPATH" else "name "
        for detail in lines[index + 1:index + 4]:
            detail = detail.strip()
            if detail.startswith(prefix):
                value = detail[len(prefix):].split(" (offset")[0]
                if command == "cmd LC_RPATH":
                    paths.append(value)
                else:
                    identity = value
    dependencies = [line.strip().split(" (compatibility")[0] for line in otool(path, "-L") if line.startswith("\t")]
    return {"rpaths": paths, "identity": identity, "dependencies": dependencies}


def dependency_path(name, owner, node, commands):
    aliases = {"@loader_path": str(owner.parent), "@executable_path": str(node.parent)}
    candidates = [name]
    if name.startswith("@rpath/"):
        roots = commands["rpaths"] + [str(owner.parent), str(node.parent.parent / "lib")]
        candidates = [root + "/" + name[7:] for root in roots]
    expanded = []
    for candidate in candidates:
        for key, value in aliases.items():
            candidate = candidate.replace(key, value)
        if Path(candidate).is_file():
            expanded.append(Path(candidate))
    if not expanded:
        raise RuntimeError("unresolved actual runtime dependency: " + name)
    return expanded[0]


def runtime_closure(executables, node):
    queue, files, logical = list(executables), {}, set()
    while queue:
        original = queue.pop()
        path = original.resolve(strict=True)
        logical.update((str(original), os.path.normpath(str(original))))
        if str(path) in files:
            continue
        commands = load_commands(path)
        files[str(path)] = {"sha256": digest(path), "load_commands": commands}
        names = [name for name in commands["dependencies"] if name != commands["identity"]]
        for name in names:
            if name.startswith(("/System/", "/usr/lib/")):
                continue
            child = dependency_path(name, path, node, commands)
            logical.update((str(child), os.path.normpath(str(child))))
            queue.append(child)
    return files, logical


def symlink_metadata(path):
    current, metadata = str(path), set()
    for _ in range(40):
        parts = Path(current).parts
        changes = []
        for index in range(1, len(parts)):
            prefix = Path(*parts[:index + 1])
            if prefix.is_symlink():
                target = os.readlink(prefix)
                target = target if os.path.isabs(target) else str(prefix.parent / target)
                changes.append((str(prefix), os.path.normpath(os.path.join(target, *parts[index + 1:]))))
                break
        if not changes:
            return metadata
        prefix, current = changes[0]
        metadata.update((prefix, current))
    raise RuntimeError("runtime symlink chain exceeds bound")


def rule(operation, kind, path):
    return "(allow " + operation + " (" + kind + " " + json.dumps(str(path)) + "))"


def required_paths(arguments, recipe):
    if sys.platform != "darwin" or platform.machine() != "arm64":
        raise RuntimeError("only macOS ARM64 is supported by this qualified recipe")
    node = Path(arguments.node).absolute()
    python = Path(arguments.python).absolute()
    install = Path(arguments.install_root).resolve(strict=True)
    venv = python.parent.parent
    config = venv / "pyvenv.cfg"
    if "include-system-site-packages = false" not in config.read_text().lower():
        raise RuntimeError("isolated auth venv required")
    cli = install / "node_modules/promptfoo/dist/src/entrypoint.js"
    identity = recipe.parents[1] / "services/backend/app/app/backend_identity.py"
    for path in [node, python, cli, identity]:
        if not path.is_file():
            raise RuntimeError("missing local input: " + str(path))
    base = python.resolve(strict=True).parent.parent
    app = base / "Resources/Python.app/Contents/MacOS/Python"
    executables = [node, python] + ([app] if app.is_file() else [])
    return {"node": node, "python": python, "venv": venv, "base": base,
            "cli": cli, "npm": install / "node_modules", "identity": identity,
            "executables": executables, "recipe": recipe}


def root_rules(paths, roots):
    lines = []
    system = ["/System/Library", "/usr/lib", "/System/Volumes/Preboot/Cryptexes/OS/System/Library/dyld"]
    mapped = [Path(name) for name in system if Path(name).exists()]
    mapped += [paths["base"], paths["venv"], paths["npm"]]
    for path in mapped:
        lines += [rule("file-read-metadata", "path-ancestors", path), rule("file-read* file-map-executable", "subpath", path)]
    for path in [paths["recipe"], roots["inputs"]]:
        lines += [rule("file-read-metadata", "path-ancestors", path), rule("file-read*", "subpath", path)]
    lines += [rule("file-read-metadata", "path-ancestors", paths["identity"]), rule("file-read*", "literal", paths["identity"])]
    lines += ['(allow file-read-metadata (path-ancestors (param "OUTPUT")))', '(allow file-read* file-write* (subpath (param "OUTPUT")))']
    return lines


def file_rules(files, aliases):
    lines = []
    for path in sorted(set(files) | aliases):
        lines += [rule("file-read-metadata", "path-ancestors", path), rule("file-read* file-map-executable", "literal", path)]
        lines += [rule("file-read-metadata", "literal", item) for item in sorted(symlink_metadata(path))]
    return lines


def native_owners(paths):
    stdlib = list((paths["base"] / "lib/python3.12/lib-dynload").glob("*.so"))
    auth = list(paths["venv"].rglob("*.so"))
    npm = list(paths["npm"].rglob("*.node")) + list(paths["npm"].rglob("*.dylib"))
    return stdlib + auth + npm


def runtime_links(files, aliases):
    links = {}
    for path in sorted(set(files) | aliases):
        for item in symlink_metadata(path) | {str(path)}:
            if Path(item).is_symlink():
                links[item] = os.readlink(item)
    return links


def profile_for(paths, roots):
    owners = paths["executables"] + native_owners(paths)
    files, aliases = runtime_closure(owners, paths["node"].resolve())
    lines = ["(version 1)", "(deny default)", "(allow process-fork)",
             "(allow signal (target self))", "(allow sysctl-read)"]
    executables = paths["executables"] + [path.resolve() for path in paths["executables"]]
    lines += [rule("process-exec", "literal", path) for path in executables]
    for path in ["/", "/dev/null", "/dev/random", "/dev/urandom"]:
        lines.append(rule("file-read*", "literal", path))
    lines.append(rule("file-write-data", "literal", "/dev/null"))
    lines += root_rules(paths, roots) + file_rules(files, aliases)
    configs = openssl_configs(aliases)
    for path in configs:
        lines += [rule("file-read-metadata", "path-ancestors", path), rule("file-read*", "literal", path)]
    return "\n".join(dict.fromkeys(lines)) + "\n", {"runtime_files": files, "openssl_configs": {str(p): digest(p) for p in configs}, "runtime_symlinks": runtime_links(files, aliases)}


def openssl_configs(aliases):
    configs = set()
    for name in aliases:
        path = Path(name)
        if "/opt/openssl" not in name:
            continue
        prefix = path.parents[3]
        package = path.parents[1].name
        candidate = prefix / "etc" / package / "openssl.cnf"
        if candidate.is_file():
            active = [line for line in candidate.read_text().splitlines() if not line.lstrip().startswith("#")]
            if any(line.lstrip().startswith(".include") for line in active):
                raise RuntimeError("OpenSSL include graph requires separate qualification")
            configs.add(candidate)
    return sorted(configs)
