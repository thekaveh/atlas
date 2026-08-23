"""The LOC/complexity reporter is committed so the #535 before/after
numbers are reproducible rather than asserted."""

from __future__ import annotations

from pathlib import Path

from scripts.loc_report import count_layer, format_report

ROOT = Path(__file__).resolve().parents[1]


def test_count_layer_counts_python_lines(tmp_path: Path):
    (tmp_path / "a.py").write_text("x = 1\ny = 2\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("z = 3\n", encoding="utf-8")
    assert count_layer(tmp_path)["lines"] == 3


def test_count_layer_ignores_pycache(tmp_path: Path):
    cache = tmp_path / "__pycache__"
    cache.mkdir()
    (cache / "junk.py").write_text("noise = 1\n", encoding="utf-8")
    (tmp_path / "real.py").write_text("real = 1\n", encoding="utf-8")
    assert count_layer(tmp_path)["lines"] == 1


def test_count_layer_reports_worst_complexity(tmp_path: Path):
    (tmp_path / "branchy.py").write_text(
        "def f(n):\n"
        + "".join(f"    if n == {i}: return {i}\n" for i in range(12)),
        encoding="utf-8",
    )
    result = count_layer(tmp_path)
    assert result["max_complexity"] >= 12


def test_count_layer_on_missing_dir_is_zero(tmp_path: Path):
    assert count_layer(tmp_path / "nope")["lines"] == 0


def test_format_report_includes_every_layer():
    text = format_report(ROOT)
    for layer in ("wizard/model", "wizard/viewmodel", "wizard/view", "ui/textual"):
        assert layer in text
