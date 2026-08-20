"""Gajim Calls plugin package.

Keep the heavy Gajim/GTK entrypoint lazy so protocol and media modules can be
imported by headless tests and tooling without requiring the full Gajim UI.
"""

from __future__ import annotations

from typing import Any

__all__ = ["GajimCallsPlugin"]


def __getattr__(name: str) -> Any:
    if name != "GajimCallsPlugin":
        raise AttributeError(name)
    from .plugin import GajimCallsPlugin

    return GajimCallsPlugin
