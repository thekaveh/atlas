"""Scratch probe for #1299. Deliberately red; this file never merges."""


def test_scratch_probe_fails_on_purpose() -> None:
    assert "early red" == "probe attempt 1", "#1299 scratch probe: deliberate failure"
