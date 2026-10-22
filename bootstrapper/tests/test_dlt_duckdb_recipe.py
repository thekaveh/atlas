"""Default-suite recipe contracts; optional engines and OS processes are doubled."""

from __future__ import annotations

import copy
import builtins
import ctypes
import errno
import importlib.util
import json
from pathlib import Path
import signal
import sys
from types import ModuleType, SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "examples/dlt-duckdb"
VARIANT = "amount__v_observed_text"


def _module(name, filename):
    spec = importlib.util.spec_from_file_location(name, SOURCE / filename)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def modules(monkeypatch):
    original_import = builtins.__import__

    def stdlib_import(name, *args, **kwargs):
        if name.partition(".")[0] in {"dlt", "duckdb"}:
            raise AssertionError("optional engine imported while loading pure recipe contracts")
        return original_import(name, *args, **kwargs)

    with monkeypatch.context() as imports:
        imports.setattr(builtins, "__import__", stdlib_import)
        recipe = _module("atlas_dlt_recipe", "recipe.py")
        monkeypatch.setitem(sys.modules, "recipe", recipe)
        qualify = _module("atlas_dlt_qualify", "qualify.py")
    return SimpleNamespace(recipe=recipe, qualify=qualify)


@pytest.fixture
def private(tmp_path):
    root = tmp_path.resolve() / "private"
    root.mkdir(mode=0o700)
    return root


def _row(**changes):
    return {"id": 1, "revision": 1, "label": "alpha", "amount": 10, **changes}


def _input(root, value):
    path = root / "revisions.json"
    path.write_bytes(value if isinstance(value, bytes) else json.dumps(value).encode())
    return path


def _category(recipe, expected, action):
    with pytest.raises(recipe.RecipeError) as caught:
        action()
    assert caught.value.category == expected


@pytest.mark.parametrize("revision", [1, 2, 3, 4, 5])
def test_fixture_revisions_are_valid_bounded_batches(modules, revision):
    path = SOURCE / "fixtures/revisions.json"
    document = json.loads(path.read_text())
    assert modules.recipe.read_revision(path, revision) == document[str(revision)]


@pytest.mark.parametrize("payload", [b"{", b"\xff", b"[]", b"null"])
def test_malformed_document_is_rejected(modules, private, payload):
    path = _input(private, payload)
    _category(modules.recipe, "malformed-input", lambda: modules.recipe.read_revision(path, 1))


@pytest.mark.parametrize("batch", [[], None, {}, [_row(id=index) for index in range(1, 10)], [_row(), _row()]])
def test_invalid_batch_size_shape_or_duplicate_ids_is_rejected(modules, private, batch):
    path = _input(private, {"1": batch})
    _category(modules.recipe, "malformed-input", lambda: modules.recipe.read_revision(path, 1))


def test_eight_record_batch_is_accepted(modules, private):
    batch = [_row(id=index) for index in range(1, 9)]
    path = _input(private, {"1": batch})
    assert modules.recipe.read_revision(path, 1) == batch


@pytest.mark.parametrize("changes", [
    {"id": True}, {"revision": True}, {"amount": False}, {"id": 0},
    {"revision": 2}, {"label": None}, {"region": 7}, {"extra": "unsupported"},
])
def test_invalid_record_values_are_rejected(modules, private, changes):
    path = _input(private, {"1": [_row(**changes)]})
    _category(modules.recipe, "malformed-input", lambda: modules.recipe.read_revision(path, 1))


@pytest.mark.parametrize("row", [None, {}, {"id": 1}])
def test_missing_record_fields_are_rejected(modules, private, row):
    path = _input(private, {"1": [row]})
    _category(modules.recipe, "malformed-input", lambda: modules.recipe.read_revision(path, 1))


def test_unknown_revision_has_a_distinct_category(modules, private):
    path = _input(private, {"1": [_row()]})
    _category(modules.recipe, "unknown-revision", lambda: modules.recipe.read_revision(path, 9))


def test_input_byte_limit_accepts_boundary_and_rejects_overflow(modules, private):
    value = json.dumps({"1": [_row()]}).encode()
    path = _input(private, value.ljust(64 * 1024, b" "))
    assert modules.recipe.read_revision(path, 1) == [_row()]
    path.write_bytes(path.read_bytes() + b" ")
    _category(modules.recipe, "input-oversize", lambda: modules.recipe.read_revision(path, 1))


def test_private_root_requires_owned_0700_directory(modules, private, monkeypatch):
    recipe = modules.recipe
    assert recipe.private_root(private) == private
    private.chmod(0o755)
    _category(recipe, "private-path", lambda: recipe.private_root(private))
    private.chmod(0o700)
    monkeypatch.setattr(recipe.os, "getuid", lambda: private.stat().st_uid + 1)
    _category(recipe, "private-path", lambda: recipe.private_root(private))


def test_private_root_rejects_file_missing_and_symlink_components(modules, private):
    recipe = modules.recipe
    file = private / "file"
    file.write_text("data")
    file.chmod(0o700)
    alias = private.parent / "alias"
    alias.symlink_to(private, target_is_directory=True)
    nested = private / "nested"
    nested.mkdir(mode=0o700)
    for value in (file, private / "missing", alias, alias / "nested"):
        _category(recipe, "private-path", lambda: recipe.private_root(value))


def test_owned_paths_reject_escape_and_internal_symlinks(modules, private):
    recipe = modules.recipe
    target = private / "target"
    target.mkdir()
    (private / "alias").symlink_to(target, target_is_directory=True)
    for relative in ("../outside", "alias/file"):
        _category(recipe, "private-path", lambda: recipe.owned_path(private, relative))
    assert recipe.owned_path(private, "new/deep/file") == private / "new/deep/file"


def test_input_symlink_and_directory_are_rejected(modules, private):
    path = _input(private, {"1": [_row()]})
    link = private / "input-link"
    link.symlink_to(path)
    for value in (link, private):
        _category(modules.recipe, "private-path", lambda: modules.recipe.read_document(value))


def test_engine_settings_configure_actual_resource_and_extension_controls(modules, private):
    assert modules.recipe.engine_settings(private) == {
        "threads": 1, "max_memory": "256MB", "temp_directory": str(private / "temp"),
        "max_temp_directory_size": "128MB", "autoinstall_known_extensions": False,
        "autoload_known_extensions": False, "enable_external_access": False,
    }


@pytest.mark.parametrize("setting_value", [
    ("threads", 2), ("enable_external_access", True), ("autoload_known_extensions", True),
    ("autoinstall_known_extensions", True), ("max_memory", "300MB"),
    ("max_temp_directory_size", "200MB"), ("temp_directory", "/elsewhere"),
])
def test_effective_settings_reject_unapplied_engine_limits(modules, private, setting_value):
    setting, value = setting_value
    values = modules.recipe.engine_settings(private)
    values[setting] = value
    sql = SimpleNamespace(execute_sql=lambda query, key: [(values[key],)])
    with pytest.raises(AssertionError):
        modules.qualify.effective_settings(sql, private)


def test_effective_settings_accept_equivalent_engine_byte_units(modules, private):
    values = modules.recipe.engine_settings(private)
    values.update(max_memory="244.1 MiB", max_temp_directory_size="122.0 MiB")
    sql = SimpleNamespace(execute_sql=lambda query, key: [(values[key],)])
    assert modules.qualify.effective_settings(sql, private) == values


def test_checkpoint_allows_replay_and_rejects_stale_or_changed_revision(modules, monkeypatch):
    state = {}
    dlt = ModuleType("dlt")
    dlt.current = SimpleNamespace(resource_state=lambda: state)
    monkeypatch.setitem(sys.modules, "dlt", dlt)
    check = modules.recipe.check_source_revision
    check([_row(revision=2)])
    original = copy.deepcopy(state)
    check([_row(revision=2)])
    assert state == original
    _category(modules.recipe, "stale-revision", lambda: check([_row()]))
    _category(modules.recipe, "revision-conflict", lambda: check([_row(revision=2, amount=12)]))
    assert state == original


def _business_rows():
    initial = [_row(), _row(id=2, label="beta", amount=20)]
    delta = [_row(revision=2, label="alpha2", amount=11), initial[1],
             _row(id=3, revision=2, label="gamma", amount=30)]
    column = [{**delta[0], "region": None}, {**initial[1], "revision": 3, "region": "eu"},
              {**delta[2], "region": None}]
    variant = [{**column[0], "revision": 4, "amount": None, VARIANT: "unknown"},
               {**column[1], VARIANT: None}, {**column[2], VARIANT: None}]
    recovered = [variant[0], {**variant[1], "revision": 5, "amount": 21}, variant[2],
                 _row(id=4, revision=5, label="delta", amount=40, region=None, **{VARIANT: None})]
    return {1: initial, 2: delta, 3: column, 4: variant, 5: recovered}


def _snapshot(rows, revision):
    columns = [["id", "BIGINT", "NO"], ["revision", "BIGINT", "YES"],
               ["label", "VARCHAR", "YES"], ["amount", "BIGINT", "YES"]]
    dlt_columns = {"amount": {"data_type": "bigint"}}
    if revision >= 3:
        columns.append(["region", "VARCHAR", "YES"])
    if revision >= 4:
        columns.append([VARIANT, "VARCHAR", "YES"])
        dlt_columns[VARIANT] = {"variant": True, "data_type": "text"}
    return {"rows": [{**row, "_dlt_id": str(row["id"]),
                      "_dlt_load_id": "load-" + str(row["revision"])} for row in rows],
            "columns": columns, "dlt_columns": dlt_columns, "local_cursor": revision,
            "committed_cursor": revision, "pending": [], "packages": [], "loads": []}


@pytest.fixture
def matrix():
    rows = _business_rows()
    phases = {"initial": 1, "replay": 1, "delta": 2, "column": 3, "variant": 4,
              "resume": 5, "final-replay": 5}
    results = {name: {"snapshot": _snapshot(rows[rev], rev)} for name, rev in phases.items()}
    for name in ("resume", "final-replay"):
        results[name]["snapshot"].update(loads=[["load-5", 0]], packages=[{
            "load_id": "load-5", "state": "loaded", "jobs": [{"state": "completed_jobs"}],
            "exceptions": [{"name": "retained"}],
        }])
    failed = copy.deepcopy(results["variant"]["snapshot"])
    failed.update(local_cursor=5, pending=["load-5"])
    results["fault"] = {"snapshot": copy.deepcopy(failed), "category": "retry-failure"}
    results["prepare-poison"] = {"snapshot": copy.deepcopy(failed), "load_id": "load-5"}
    results["repair"] = {"snapshot": failed, "load_id": "load-5", "retry_count": 1}
    return results


def test_complete_incremental_evolution_and_recovery_observations_are_accepted(modules, matrix):
    modules.qualify.assert_matrix(matrix)


@pytest.mark.parametrize("mutation", [
    ("resume", 3, "revision", 999), ("resume", 3, "region", "wrong"),
    ("resume", 3, VARIANT, "wrong"), ("resume", 1, VARIANT, "wrong"),
    ("resume", 3, "_dlt_unrecognized", "wrong"), ("delta", 1, "revision", 2),
    ("prepare-poison", 0, "region", "wrong"), ("repair", 0, "revision", 999),
    ("fault", 0, "label", "wrong"), ("final-replay", 3, "label", "wrong"),
    ("delta", 1, "_dlt_load_id", "rewritten"),
])
def test_full_business_row_corruption_is_rejected(modules, matrix, mutation):
    phase, index, key, value = mutation
    matrix[phase]["snapshot"]["rows"][index][key] = value
    with pytest.raises(AssertionError):
        modules.qualify.assert_matrix(matrix)


def test_missing_nullable_column_is_rejected(modules, matrix):
    del matrix["resume"]["snapshot"]["rows"][3]["region"]
    with pytest.raises(AssertionError):
        modules.qualify.assert_matrix(matrix)


@pytest.mark.parametrize("defect", ["duplicate-id", "committed-cursor", "pending", "unfinished-job"])
def test_incomplete_destination_outcomes_are_rejected(modules, matrix, defect):
    snapshot = matrix["resume"]["snapshot"]
    if defect == "duplicate-id":
        snapshot["rows"].append(copy.deepcopy(snapshot["rows"][0]))
    elif defect == "committed-cursor":
        snapshot["committed_cursor"] = 4
    elif defect == "pending":
        snapshot["pending"] = ["load-5"]
    else:
        snapshot["packages"][0]["jobs"][0]["state"] = "failed_jobs"
    with pytest.raises(AssertionError):
        modules.qualify.assert_matrix(matrix)


@pytest.mark.parametrize("defect", ["region-type", "variant-type", "variant-flag", "lost-null"])
def test_schema_evolution_corruption_is_rejected(modules, matrix, defect):
    if defect == "region-type":
        matrix["column"]["snapshot"]["columns"][-1][1] = "BIGINT"
    elif defect == "variant-type":
        matrix["variant"]["snapshot"]["dlt_columns"][VARIANT]["data_type"] = "bigint"
    elif defect == "variant-flag":
        matrix["variant"]["snapshot"]["dlt_columns"][VARIANT]["variant"] = False
    else:
        matrix["column"]["snapshot"]["rows"][0]["region"] = "unexpected"
    with pytest.raises(AssertionError):
        modules.qualify.assert_matrix(matrix)


class _Resource:
    def __init__(self, harness, name):
        self.harness, self.name, self.closed = harness, name, False
        self.fd = {"stdout": 11, "stderr": 12}.get(name)

    def close(self):
        self.closed = True
        self.harness.fail(self.name + "-close")


class _Log(_Resource):
    def write(self, payload):
        self.harness.fail("write")
        self.harness.output.extend(payload)


class _Selector(_Resource):
    def __init__(self, harness):
        super().__init__(harness, "selector")
        self.mapping = {}

    def register(self, stream, events):
        self.harness.fail("register")
        self.mapping[stream.fd] = SimpleNamespace(fd=stream.fd, fileobj=stream)

    def unregister(self, stream):
        self.mapping.pop(stream.fd)

    def get_map(self):
        return self.mapping

    def select(self, timeout):
        self.harness.fail("select")
        return [(key, 1) for key in list(self.mapping.values())]


class _Supervisor:
    def __init__(self, qualify, monkeypatch):
        self.qualify, self.failure = qualify, None
        self.events, self.output, self.resources = [], bytearray(), []
        self.groups = {12345: {12345, 12346}, 54321: {54321}}
        self.chunks, self.clock, self.denied = [b"complete\n", b"", b""], 0, False
        self.process = SimpleNamespace(pid=12345, returncode=None,
                                       stdout=self.resource("stdout"), stderr=self.resource("stderr"))
        monkeypatch.setattr(qualify.subprocess, "Popen", self.launch)
        monkeypatch.setattr(qualify.selectors, "DefaultSelector", self.selector)
        monkeypatch.setattr(qualify.os, "read", self.read)
        monkeypatch.setattr(qualify.os, "killpg", self.killpg)
        monkeypatch.setattr(qualify.os, "wait4", self.wait4)
        monkeypatch.setattr(qualify.time, "monotonic", lambda: self.clock)

    def fail(self, point):
        failures = {self.failure} if self.failure != "write-close" else {"write", "log-close"}
        if point in failures:
            raise OSError("injected " + point)

    def resource(self, name):
        resource = _Resource(self, name)
        self.resources.append(resource)
        return resource

    def launch(self, command, **kwargs):
        assert kwargs["start_new_session"] is True
        self.events.append(("launch", tuple(command)))
        return self.process

    def selector(self):
        self.fail("create")
        resource = _Selector(self)
        self.resources.append(resource)
        return resource

    def open(self, mode):
        assert mode == "wb"
        self.fail("open")
        resource = _Log(self, "log")
        self.resources.append(resource)
        return resource

    def read(self, fd, size):
        self.fail("read")
        return self.chunks.pop(0)

    def killpg(self, pid, sig):
        self.events.append(("killpg", pid, sig))
        if self.denied:
            raise PermissionError(errno.EPERM, "injected group denial")
        self.groups[pid].clear()

    def wait4(self, pid, flags):
        self.events.append(("wait4", pid, flags))
        if self.denied:
            return 0, 0, SimpleNamespace(ru_maxrss=0)
        return pid, 0, SimpleNamespace(ru_maxrss=17)

    def run(self):
        return self.qualify.supervised(["owned-python", "synthetic-phase"], self, 1)

    def assert_cleaned(self, result):
        assert all(resource.closed for resource in self.resources)
        assert not self.groups[12345] and self.groups[54321] == {54321}
        assert self.events[-2:] == [("killpg", 12345, signal.SIGKILL), ("wait4", 12345, 0)]
        assert sum(event[0] == "wait4" for event in self.events) == 1
        assert result["exit_code"] == 0 and result["peak_rss_bytes"] > 0


@pytest.mark.parametrize("failure", [
    "open", "write", "write-close", "read", "create", "register", "select",
    "stdout-close", "stderr-close", "selector-close", "log-close",
])
def test_supervisor_errors_keep_receipts_and_close_owned_resources(modules, monkeypatch, failure):
    harness = _Supervisor(modules.qualify, monkeypatch)
    harness.failure = failure
    result = harness.run()
    harness.assert_cleaned(result)
    assert result["category"] == "supervisor-error"
    assert result["error"]["type"] == "OSError"
    if failure == "write-close":
        assert result["error"]["detail"] == "injected write"
        assert result["cleanup_errors"][0]["detail"] == "injected log-close"


def test_normal_eof_stops_owned_descendants_before_single_reap(modules, monkeypatch):
    harness = _Supervisor(modules.qualify, monkeypatch)
    result = harness.run()
    harness.assert_cleaned(result)
    assert result["category"] is None
    assert result["output_bytes"] == len(b"complete\n")
    assert harness.output == b"complete\n"


def test_deadline_stops_inherited_pipe_group_and_preserves_unrelated(modules, monkeypatch):
    harness = _Supervisor(modules.qualify, monkeypatch)
    harness.clock, harness.chunks = 2, [b"ready", b"ready"]
    result = harness.run()
    harness.assert_cleaned(result)
    assert result["category"] == "deadline"


def test_output_cap_bounds_written_log_when_stream_exceeds_cap(modules, monkeypatch):
    harness = _Supervisor(modules.qualify, monkeypatch)
    harness.chunks = [b"x" * 4096] * 16 + [b"x", b"", b""]
    result = harness.run()
    harness.assert_cleaned(result)
    assert result["category"] == "report-cap" and result["output_bytes"] == 65537
    assert len(harness.output) == 65536


def test_denied_group_cleanup_is_visible_and_never_blocks_wait(modules, monkeypatch):
    harness = _Supervisor(modules.qualify, monkeypatch)
    harness.denied = True
    monkeypatch.setattr(modules.qualify.sys, "platform", "linux")
    result = harness.run()
    assert result["category"] == "supervisor-error"
    assert result["error"]["type"] == "PermissionError"
    assert result["exit_code"] is None and result["peak_rss_bytes"] is None
    assert harness.events[-1] == ("wait4", 12345, modules.qualify.os.WNOHANG)
    assert all(resource.closed for resource in harness.resources)
    assert harness.groups[54321] == {54321}


class _NativeProof:
    def __init__(self):
        self.live, self.descendant = False, False
        self.member, self.returned, self.query_errno = 12345, 4, 0
        self.lookup_errno, self.group_errno = errno.ESRCH, errno.EPERM
        self.library_error, self.query_error = None, None
        self.events = []

    def getpgid(self, pid):
        self.events.append("leader")
        if self.live:
            return pid
        raise ProcessLookupError(self.lookup_errno, "leader unavailable")

    def listing(self, *args):
        kind, pid, values, size = args
        assert (kind, pid, size) == (2, 12345, ctypes.sizeof(ctypes.c_int) * 2)
        self.events.append("snapshot")
        if self.query_error:
            raise self.query_error
        values[0] = self.member
        returned = 8 if self.descendant else self.returned
        if self.live:
            self.live, self.descendant = False, True
        ctypes.set_errno(self.query_errno)
        return returned

    def library(self, filename, **kwargs):
        if self.library_error:
            raise self.library_error
        listing = lambda *args: self.listing(*args)
        return SimpleNamespace(proc_listpids=listing)

    def killpg(self, pid, sig):
        raise PermissionError(self.group_errno, "original group denial")

    def install(self, qualify, monkeypatch):
        monkeypatch.setattr(qualify.sys, "platform", "darwin")
        monkeypatch.setattr(qualify.os, "getpgid", self.getpgid)
        monkeypatch.setattr(qualify.os, "killpg", self.killpg)
        monkeypatch.setattr(ctypes, "CDLL", self.library)


def test_darwin_exact_exited_singleton_accepts_denial_without_native_library(modules, monkeypatch):
    proof = _NativeProof()
    proof.install(modules.qualify, monkeypatch)
    modules.qualify.stop_group(12345)
    assert proof.events == ["leader", "snapshot"]


@pytest.mark.parametrize("defect", [
    "live-fork-exit-race", "existing-descendant", "zero", "saturated", "other-member",
    "query-errno", "wrong-group-errno", "wrong-lookup-errno", "library-error", "query-error",
])
def test_darwin_unproven_group_absence_keeps_original_signal_error(modules, monkeypatch, defect):
    proof = _NativeProof()
    changes = {
        "live-fork-exit-race": ("live", True), "existing-descendant": ("descendant", True),
        "zero": ("returned", 0), "saturated": ("returned", 8), "other-member": ("member", 54321),
        "query-errno": ("query_errno", errno.EPERM), "wrong-group-errno": ("group_errno", errno.EACCES),
        "wrong-lookup-errno": ("lookup_errno", errno.EIO),
        "library-error": ("library_error", OSError("injected library")),
        "query-error": ("query_error", OSError("injected query")),
    }
    key, value = changes[defect]
    setattr(proof, key, value)
    proof.install(modules.qualify, monkeypatch)
    with pytest.raises(PermissionError, match="original group denial") as caught:
        modules.qualify.stop_group(12345)
    if defect in {"library-error", "query-error"}:
        assert isinstance(caught.value.__cause__, OSError)
