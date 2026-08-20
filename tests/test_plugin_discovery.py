from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types


def test_package_dir_only_exposes_plugin_class(monkeypatch):
    """Match Gajim 2.5's _load_plugin_module() discovery loop."""
    package_root = Path(__file__).resolve().parents[1] / "gajim_calls"
    package_name = "gajim_calls_discovery_test"

    fake_plugin = types.ModuleType(f"{package_name}.plugin")

    class GajimCallsPlugin:
        pass

    fake_plugin.GajimCallsPlugin = GajimCallsPlugin
    monkeypatch.setitem(sys.modules, f"{package_name}.plugin", fake_plugin)

    spec = spec_from_file_location(
        package_name,
        package_root / "__init__.py",
        submodule_search_locations=[str(package_root)],
    )
    assert spec is not None
    assert spec.loader is not None

    module = module_from_spec(spec)
    monkeypatch.setitem(sys.modules, package_name, module)
    spec.loader.exec_module(module)

    assert dir(module) == ["GajimCallsPlugin"]

    # Gajim 2.5 does this without checking whether module_attr is a class.
    # This must therefore never receive strings, lists, functions, modules, etc.
    for module_attr_name in dir(module):
        module_attr = getattr(module, module_attr_name)
        assert issubclass(module_attr, object)
