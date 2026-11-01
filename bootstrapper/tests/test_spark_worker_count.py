"""Wizard widget: Spark's source step carries a SecondaryNumberInput
for SPARK_WORKER_COUNT mirroring Ray's pattern."""
from pathlib import Path


def _wizard_steps():
    from core.config_parser import ConfigParser
    from ui.textual.integration import _build_steps_and_rows
    repo_root = Path(__file__).resolve().parent.parent.parent
    cp = ConfigParser(str(repo_root))
    cp.parse_env_file()
    steps, _rows, _info, _bp, _state, _cloud = _build_steps_and_rows(cp)
    return steps


def test_spark_source_step_has_worker_count_secondary():
    steps = _wizard_steps()
    spark_step = next(
        (s for s in steps if s.service_name == "Apache Spark" and "source" in s.title.lower()),
        None,
    )
    assert spark_step is not None, (
        f"Spark source step missing. Steps: {[(s.service_name, s.title) for s in steps]}"
    )
    container_opt = next((o for o in spark_step.options if o.value == "container"), None)
    assert container_opt is not None, "container option missing"
    cfg = container_opt.secondary_number
    assert cfg is not None, "container option must carry SecondaryNumberInput"
    assert cfg.env_var == "SPARK_WORKER_COUNT"
    assert cfg.unit_suffix == "workers"
    assert cfg.number_min == 1
    assert cfg.number_max == 8
    assert str(cfg.default_value) == "2"
