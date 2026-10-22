"""Real-file regressions for publication through the local artifact API."""
from __future__ import annotations

import hashlib
import queue
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from asset_worker import api, storage


class Publication:
    def __init__(self, root, suffix):
        self.data = (b"complete content-addressed artifact:" + suffix.encode()) * 128
        self.sha = hashlib.sha256(self.data).hexdigest()
        self.suffix = suffix
        self.media = "image/png" if suffix == "png" else "model/gltf-binary"
        self.key = f"gltf/{self.sha}.{suffix}"
        self.target = root / self.key
        self.url = f"/gltf/artifacts/{self.sha}.{suffix}"
        self.storage = storage.ArtifactStorage()
        self.client = TestClient(api.create_app(api_token="publication-test"),
                                 headers={"Authorization": "Bearer publication-test"})

    def store(self):
        return self.storage.store(self.data, sha256=self.sha)

    def assert_download(self, response=None):
        if response is None:
            response = self.client.get(self.url)
        assert response.status_code == 200
        assert (response.content, hashlib.sha256(response.content).hexdigest()) == (self.data, self.sha)
        assert response.headers["content-type"] == self.media
        assert response.headers["content-disposition"] == f'attachment; filename="{self.sha}.{self.suffix}"'

    def assert_result(self, result):
        assert result == {"storage": "local", "key": self.key, "content_type": self.media}
        assert list(self.target.parent.iterdir()) == [self.target]
        self.assert_download()


class PausedFile:
    def __init__(self, handle, gate):
        self.handle = handle
        self.gate = gate

    def __getattr__(self, name):
        return getattr(self.handle, name)

    def __enter__(self):
        self.handle.__enter__()
        return self

    def __exit__(self, *args):
        return self.handle.__exit__(*args)

    def write(self, data):
        split = len(data) // 2
        self.handle.write(data[:split])
        self.handle.flush()
        self.gate.writers.put(self.handle)
        assert self.gate.release.wait(5), "writer was not released"
        if self.gate.fail_write:
            raise OSError("injected staging write failure")
        self.handle.write(data[split:])
        return len(data)


class WriteGate:
    def __init__(self):
        self.release = threading.Event()
        self.writers = queue.Queue()
        self.fail_write = False

    def wrap(self, handle):
        return PausedFile(handle, self)


@pytest.fixture
def publication(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSET_WORKER_ARTIFACT_DIR", str(tmp_path))
    monkeypatch.setenv("ASSET_WORKER_MINIO_ENABLED", "false")
    return Publication(tmp_path, "glb")


def _install_gate(monkeypatch, publication):
    gate = WriteGate()
    original_open = Path.open
    original_temporary = tempfile.NamedTemporaryFile

    def path_open(path, mode="r", *args, **kwargs):
        handle = original_open(path, mode, *args, **kwargs)
        if mode == "wb" and path.parent == publication.target.parent:
            return gate.wrap(handle)
        return handle

    def temporary(*args, **kwargs):
        return gate.wrap(original_temporary(*args, **kwargs))

    monkeypatch.setattr(Path, "open", path_open)
    monkeypatch.setattr(tempfile, "NamedTemporaryFile", temporary)
    return gate


def _seed(publication):
    publication.target.parent.mkdir(parents=True, exist_ok=True)
    publication.target.write_bytes(publication.data)
    publication.assert_download()


def _assert_stage(handle, publication):
    stage = Path(handle.name)
    assert stage.parent == publication.target.parent
    assert stage != publication.target


@pytest.mark.parametrize("existing", [False, True], ids=["first-publication", "replacement"])
def test_paused_write_never_serves_partial_content(publication, monkeypatch, existing):
    if existing:
        _seed(publication)
    # Install after seeding so the barrier controls only the store under test.
    with monkeypatch.context() as patch:
        gate = _install_gate(patch, publication)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(publication.store)
            try:
                handle = gate.writers.get(timeout=5)
                response = publication.client.get(publication.url)
                if response.status_code == 200:
                    publication.assert_download(response)
                else:
                    assert (existing, response.status_code) == (False, 404)
                _assert_stage(handle, publication)
            finally:
                gate.release.set()
            publication.assert_result(future.result(timeout=5))
            assert handle.closed


@pytest.mark.parametrize("failure", ["write", "replace"])
def test_failed_publication_preserves_old_download(publication, monkeypatch, failure):
    _seed(publication)
    gate = _install_gate(monkeypatch, publication)
    gate.fail_write = failure == "write"

    def fail_replace(source, destination):
        assert (Path(source), Path(destination)) == (Path(handle.name), publication.target)
        assert handle.closed
        assert Path(source).read_bytes() == publication.data
        raise OSError("injected replacement failure")

    if failure == "replace":
        monkeypatch.setattr(storage.os, "replace", fail_replace)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(publication.store)
        try:
            handle = gate.writers.get(timeout=5)
            _assert_stage(handle, publication)
            publication.assert_download()
        finally:
            gate.release.set()
        with pytest.raises(OSError, match="injected"):
            future.result(timeout=5)
    publication.assert_result({"storage": "local", "key": publication.key, "content_type": publication.media})


def test_identical_stores_have_independent_stages(publication, monkeypatch):
    gate = _install_gate(monkeypatch, publication)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(publication.store) for _ in range(2)]
        try:
            handles = [gate.writers.get(timeout=5) for _ in range(2)]
            assert len({handle.name for handle in handles}) == 2
            for handle in handles:
                _assert_stage(handle, publication)
            assert publication.client.get(publication.url).status_code == 404
        finally:
            gate.release.set()
        results = [future.result(timeout=5) for future in futures]
        for result in results:
            publication.assert_result(result)
    assert all(handle.closed for handle in handles)


def test_store_repairs_a_legacy_partial_object(publication):
    publication.target.parent.mkdir(parents=True, exist_ok=True)
    publication.target.write_bytes(publication.data[:17])
    publication.assert_result(publication.store())


def test_cleanup_error_does_not_mask_write_error(publication, monkeypatch):
    _seed(publication)
    gate = _install_gate(monkeypatch, publication)
    gate.fail_write = True

    def fail_cleanup(path, *args, **kwargs):
        raise OSError("injected cleanup failure")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", fail_cleanup)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(publication.store)
            try:
                handle = gate.writers.get(timeout=5)
                publication.assert_download()
            finally:
                gate.release.set()
            with pytest.raises(OSError, match="injected staging write failure"):
                future.result(timeout=5)
    publication.assert_download()
    stage = Path(handle.name)
    assert stage.exists()
    stage.unlink()
    publication.assert_result({"storage": "local", "key": publication.key, "content_type": publication.media})
