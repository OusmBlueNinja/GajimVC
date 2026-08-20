"""Load pure plugin modules without importing GTK/Gajim on headless CI."""

from __future__ import annotations

from pathlib import Path
import sys
import types


PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "gajim_calls"
package = types.ModuleType("gajim_calls")
package.__path__ = [str(PACKAGE_ROOT)]
package.__package__ = "gajim_calls"
sys.modules["gajim_calls"] = package
