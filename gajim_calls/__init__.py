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

    # Discovery tests and lightweight loaders can provide only the plugin
    # class. Compose runtime-only behaviour when the real controller exists.
    runtime_controller = getattr(plugin_module, "RuntimeCallController", None)
    if runtime_controller is not None:
        from .call_waiting import with_call_waiting

        plugin_module.RuntimeCallController = with_call_waiting(runtime_controller)
    return plugin_module.GajimCallsPlugin
