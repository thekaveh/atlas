"""
Atlas — bootstrapper UI package.

The interactive bootstrapper UI is a Textual app under ``ui/textual/``
(``run_setup_flow`` and ``run_launch_flow`` in ``ui/textual/integration.py``).
The framework-agnostic data model (``state.py``, ``state_builder.py``,
``service_discovery.py``) now lives in ``wizard/model/`` (#535), where it is
consumed by both the Textual wizard and the ``--no-tui`` linear stdout flow
in ``start.py``. The top-level ``ui`` package itself holds the Textual view
(``ui/textual/``) plus ``term_caps.py``, which exposes the small
``is_tui_capable`` helper ``start.py`` uses to pick between the two flows.
"""
