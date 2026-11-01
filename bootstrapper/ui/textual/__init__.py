"""
Textual-based TUI for Atlas.

Screens / app are not imported eagerly — pulling them in only when the
caller asks (e.g. ``from .integration import run_setup_flow``) keeps the
widget package importable on its own.
"""

__all__ = []
