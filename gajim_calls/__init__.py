"""Gajim Calls plugin package.

Gajim 2.5 discovers a plugin by iterating ``dir()`` on this module and calling
``issubclass()`` on every returned attribute. Keep the public directory limited
to the plugin class so non-class package attributes cannot crash discovery.
"""

from __future__ import annotations

from typing import Any

__all__ = ["GajimCallsPlugin"]


def __dir__() -> list[str]:
    """Expose only the class Gajim's plugin loader is expected to inspect."""
    return __all__


def __getattr__(name: str) -> Any:
    if name != "GajimCallsPlugin":
        raise AttributeError(name)

    from . import plugin as plugin_module
    from .call_waiting import with_call_waiting

    # Keep call waiting isolated from the core runtime controller. The plugin
    # class looks this global up when init() runs, so replacing it here keeps
    # Gajim's discovery surface unchanged while composing the extra behaviour.
    plugin_module.RuntimeCallController = with_call_waiting(
        plugin_module.RuntimeCallController
    )
    return plugin_module.GajimCallsPlugin
