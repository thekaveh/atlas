"""Behavioral controls for the optional local authorization evaluation."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import sys
import tempfile
import threading
import unittest

import pytest

if sys.platform == "win32":
    pytest.skip("The macOS authorization recipe requires POSIX file locks", allow_module_level=True)

ROOT = Path(__file__).resolve().parents[2]


def _recipe_module(name):
    source = ROOT / "examples/promptfoo-security" / (name + ".py")
    spec = importlib.util.spec_from_file_location("atlas_recipe_" + name, source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_HELPER = _recipe_module("identity_helper")
_RUNNER = _recipe_module("run_local")
_QUALIFIER = _recipe_module("qualify_macos")
_REASON = "OWNERSHIP_AND_EFFECTS vulnerable-foreign"


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "budget.lock").touch()
        (self.root / "budget.json").write_text(json.dumps({"admissions": [], "rejections": 0}))
        self.lock_inode = (self.root / "budget.lock").stat().st_ino
        self.effects = []

    def attempt(self, case_id):
        with _HELPER.admitted(self.root, case_id):
            self.effects.append(case_id)  # Sentinel for reaching the protected seam.

    def counter(self):
        return json.loads((self.root / "budget.json").read_text())

    def test_four_admissions_persist_across_two_batches(self):
        for case_id in ("own", "foreign", "service"):
            self.attempt(case_id)
        self.assertEqual(len(self.counter()["admissions"]), 3)
        self.attempt("vulnerable")
        self.assertEqual(self.counter(), {"admissions": self.effects, "rejections": 0})
        self.assertEqual(len(self.effects), 4)
        self.assertEqual((self.root / "budget.lock").stat().st_ino, self.lock_inode)

    def test_retry_is_rejected_before_seam(self):
        self.attempt("own")
        with self.assertRaises(_HELPER.BudgetExceeded):
            self.attempt("own")
        self.assertEqual(self.effects, ["own"])
        self.assertEqual(self.counter(), {"admissions": ["own"], "rejections": 1})

    def test_fifth_attempt_is_rejected_before_seam(self):
        for case_id in ("a", "b", "c", "d"):
            self.attempt(case_id)
        with self.assertRaises(_HELPER.BudgetExceeded):
            self.attempt("fifth")
        self.assertEqual(len(self.effects), 4)
        self.assertEqual(self.counter()["rejections"], 1)

    def test_critical_section_survives_counter_replacement(self):
        attempted, reached = threading.Event(), threading.Event()

        def second_writer():
            attempted.set()
            with _HELPER.admitted(self.root, "second"):
                reached.set()

        with ThreadPoolExecutor(max_workers=1) as pool:
            with _HELPER.admitted(self.root, "first"):
                future = pool.submit(second_writer)
                self.assertTrue(attempted.wait(timeout=3))
                self.assertFalse(reached.wait(timeout=0.2))
            future.result(timeout=3)
        self.assertTrue(reached.is_set())
        self.assertEqual(self.counter()["admissions"], ["first", "second"])

    def test_concurrent_retry_reaches_seam_once(self):
        barrier = threading.Barrier(4)

        def contender(_):
            barrier.wait(timeout=5)
            try:
                self.attempt("same")
                return True
            except _HELPER.BudgetExceeded:
                return False

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(contender, range(4)))
        self.assertEqual(results.count(True), 1)
        self.assertEqual(self.effects, ["same"])
        self.assertEqual(self.counter(), {"admissions": ["same"], "rejections": 3})
        self.assertEqual((self.root / "budget.lock").stat().st_ino, self.lock_inode)


def seeded_row():
    return {"failureReason": 1, "score": 0, "error": _REASON,
            "gradingResult": {"pass": False, "reason": _REASON,
                "componentResults": [{"pass": False, "reason": _REASON,
                                      "assertion": {"type": "javascript"}}]}}


def vulnerable_case():
    return {"id": "vulnerable-foreign", "principal": {"kind": "user", "subject": "synthetic-alice"},
            "requested_user": "synthetic-bob", "action": "append-synthetic-event", "model_text": "hostile fixture text"}


def observed_vulnerable(case):
    return {"case_id": case["id"], "principal": case["principal"], "action": case["action"],
            "requested_user": case["requested_user"], "decision": "allowed", "decision_code": 200,
            "resolved_owner": case["requested_user"], "side_effect_count": 1, "model_text": case["model_text"]}


class ResultPredicates(unittest.TestCase):
    def test_exact_intended_single_assertion_is_accepted(self):
        _RUNNER.check_seeded_assertion(seeded_row())

    def test_provider_runtime_error_is_not_seeded_assertion(self):
        row = seeded_row()
        row.update(failureReason=2, error="Python failed before any fixture output")
        with self.assertRaises(RuntimeError):
            _RUNNER.check_seeded_assertion(row)

    def test_javascript_exception_with_assert_reason_code_is_rejected(self):
        row = seeded_row()
        row["gradingResult"]["reason"] = "Custom function threw error: TypeError"
        with self.assertRaises(RuntimeError):
            _RUNNER.check_seeded_assertion(row)

    def test_marker_on_wrong_component_is_rejected(self):
        row = seeded_row()
        row["gradingResult"]["componentResults"][0]["assertion"]["type"] = "contains"
        with self.assertRaises(RuntimeError):
            _RUNNER.check_seeded_assertion(row)

    def test_additional_failing_component_is_rejected(self):
        row = seeded_row()
        row["gradingResult"]["componentResults"].append(deepcopy(row["gradingResult"]["componentResults"][0]))
        with self.assertRaises(RuntimeError):
            _RUNNER.check_seeded_assertion(row)

    def test_supposed_denial_cannot_replace_actual_vulnerable_append(self):
        case = vulnerable_case()
        wrong = observed_vulnerable(case)
        wrong.update(decision="denied", decision_code=403, resolved_owner=None, side_effect_count=0)
        with self.assertRaises(RuntimeError):
            _RUNNER.check_observation(wrong, case, "vulnerable")

    def test_wrong_observed_case_id_is_rejected_despite_intended_marker(self):
        case = vulnerable_case()
        observed = observed_vulnerable(case)
        observed["case_id"] = "hostile-foreign"
        row = {**seeded_row(), "vars": {"case_id": case["id"]}, "success": False,
               "response": {"output": observed}}
        with self.assertRaises(RuntimeError):
            _RUNNER.check_row(row, case, "vulnerable")

    def test_observed_principal_target_and_action_are_bound(self):
        case = vulnerable_case()
        for field in ("principal", "requested_user", "action"):
            observed = observed_vulnerable(case)
            observed[field] = "different synthetic value"
            with self.assertRaises(RuntimeError, msg=field):
                _RUNNER.check_observation(observed, case, "vulnerable")

    def test_nonzero_seeded_assertion_score_is_rejected(self):
        row = seeded_row()
        row["score"] = 1
        with self.assertRaises(RuntimeError):
            _RUNNER.check_seeded_assertion(row)


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "installed"
        self.root.mkdir()
        first, second = self.root / "first.py", self.root / "second.py"
        first.write_text("synthetic identical module data\n")
        second.write_bytes(first.read_bytes())
        self.alias = self.root / "alias.py"
        self.alias.symlink_to("first.py")
        self.inventory = self.root.parent / "inventory.json"
        self.inventory.write_text(json.dumps({"root": str(self.root),
            "files": {str(first): _RUNNER.digest(first), str(second): _RUNNER.digest(second)},
            "symlinks": {str(self.alias): "first.py"}}))
        _RUNNER.verify_inventory(self.inventory, self.root)

    def test_added_startup_file_is_refused_before_runtime(self):
        (self.root / "unexpected.pth").write_text("import synthetic_untrusted_startup\n")
        with self.assertRaises(ValueError):
            _RUNNER.verify_inventory(self.inventory, self.root)

    def test_same_bytes_different_symlink_target_is_refused(self):
        original = _RUNNER.digest(self.alias)
        self.alias.unlink()
        self.alias.symlink_to("second.py")
        self.assertEqual(_RUNNER.digest(self.alias), original)
        with self.assertRaises(ValueError):
            _RUNNER.verify_inventory(self.inventory, self.root)

    def test_borrowed_stdlib_changed_bytes_are_refused(self):
        base = self.root.parent / "python-base"
        (base / "bin").mkdir(parents=True)
        (base / "lib").mkdir()
        executable, stdlib = base / "bin/python", base / "lib/pathlib.py"
        executable.write_text("synthetic executable, never run")
        stdlib.write_text("synthetic original stdlib data")
        (self.root / "bin").mkdir()
        python = self.root / "bin/python"
        python.symlink_to(executable)
        inventory = json.loads(self.inventory.read_text())
        inventory["symlinks"][str(python)] = str(executable)
        self.inventory.write_text(json.dumps(inventory))
        base_inventory = self.root.parent / "base.json"
        base_inventory.write_text(json.dumps({"root": str(base), "symlinks": {},
            "files": {str(executable): _RUNNER.digest(executable), str(stdlib): _RUNNER.digest(stdlib)}}))
        bindings = self.empty_npm_bindings(python, base_inventory)
        _RUNNER.verify_inventories(bindings)
        stdlib.write_text("synthetic changed stdlib data")
        with self.assertRaises(ValueError):
            _RUNNER.verify_inventories(bindings)

    def empty_npm_bindings(self, python, base_inventory):
        install = self.root.parent / "npm-install"
        modules = install / "node_modules"
        modules.mkdir(parents=True)
        npm_inventory = self.root.parent / "npm.json"
        npm_inventory.write_text(json.dumps({"root": str(modules), "files": {}, "symlinks": {}}))
        runtime_inventory = self.root.parent / "runtime.json"
        runtime_inventory.write_text(json.dumps({"runtime_symlinks": {}}))
        return {"python": str(python), "package_json": str(install / "package.json"),
                "cli": str(modules / "synthetic-entrypoint.js"), "python_inventory": str(self.inventory),
                "python_base_inventory": str(base_inventory), "npm_inventory": str(npm_inventory),
                "runtime_inventory": str(runtime_inventory)}

    def test_same_bytes_runtime_alias_retarget_is_refused(self):
        runtime = self.root.parent / "runtime.json"
        runtime.write_text(json.dumps({"runtime_symlinks": {str(self.alias): "first.py"}}))
        _RUNNER.verify_runtime_aliases(runtime)
        original = _RUNNER.digest(self.alias)
        self.alias.unlink()
        self.alias.symlink_to("second.py")
        self.assertEqual(_RUNNER.digest(self.alias), original)
        with self.assertRaises(ValueError):
            _RUNNER.verify_runtime_aliases(runtime)


class PackageInputTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.recipe, self.install = self.root / "recipe", self.root / "install"
        self.recipe.mkdir()
        self.install.mkdir()

    def check_install(self, row, installed_version=None):
        lock = {"packages": {"": {}, "node_modules/synthetic": row}}
        for root in (self.recipe, self.install):
            (root / "package.json").write_text('{"private": true}')
            (root / "package-lock.json").write_text(json.dumps(lock))
        if installed_version is not None:
            package = self.install / "node_modules/synthetic"
            package.mkdir(parents=True)
            (package / "package.json").write_text(json.dumps({"version": installed_version}))
        args = SimpleNamespace(install_root=str(self.install))
        with patch.object(_QUALIFIER, "RECIPE", self.recipe):
            return _QUALIFIER.package_inputs(args, SimpleNamespace(digest=_RUNNER.digest))

    def test_absent_extraneous_lock_row_is_accepted(self):
        result = self.check_install({"version": "1.0.0", "extraneous": True})
        self.assertEqual(result, self.install)

    def test_absent_required_lock_row_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "required npm package missing"):
            self.check_install({"version": "1.0.0"})

    def test_wrong_installed_version_is_rejected_even_if_extraneous(self):
        with self.assertRaisesRegex(ValueError, "installed npm version differs"):
            self.check_install({"version": "1.0.0", "extraneous": True}, "2.0.0")
