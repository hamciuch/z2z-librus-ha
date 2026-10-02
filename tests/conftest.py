"""Test setup: import the integration without a Home Assistant install.

Only the pure logic is tested (parsing, API mapping, merging, sensor
statistics, new-item detection). Home Assistant modules are replaced by
small stubs - enough for the modules to import and for the notifier to run.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "custom_components"))


def _stub(name: str, **attrs) -> types.ModuleType:
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)

    def __getattr__(attr):  # any other HA name: a distinct empty class
        cls = type(attr, (), {"__init__": lambda self, *a, **k: None})
        cls.__class_getitem__ = classmethod(lambda c, i: c)
        setattr(module, attr, cls)
        return cls

    module.__getattr__ = __getattr__
    sys.modules[name] = module
    return module


class MemoryStore:
    """Stand-in for homeassistant.helpers.storage.Store."""

    data: dict[str, object] = {}

    def __init__(self, hass, version, key):
        self.key = key

    async def async_load(self):
        return MemoryStore.data.get(self.key)

    async def async_save(self, data):
        MemoryStore.data[self.key] = data

    def async_delay_save(self, factory, delay=0):
        MemoryStore.data[self.key] = factory()

    async def async_remove(self):
        MemoryStore.data.pop(self.key, None)


class CoreState:
    running = "running"
    starting = "starting"


def callback(func):
    return func


for name in (
    "homeassistant",
    "homeassistant.components",
    "homeassistant.components.sensor",
    "homeassistant.components.calendar",
    "homeassistant.config_entries",
    "homeassistant.helpers",
    "homeassistant.helpers.entity",
    "homeassistant.helpers.update_coordinator",
):
    _stub(name)
_stub("homeassistant.const", EVENT_HOMEASSISTANT_STARTED="homeassistant_started")
_stub("homeassistant.core", CoreState=CoreState, callback=callback)
_stub("homeassistant.helpers.event", async_call_later=lambda *a, **k: None)
_stub("homeassistant.helpers.storage", Store=MemoryStore)

# The integration package, without running __init__.py (it needs real HA).
package = types.ModuleType("z2z_librus")
package.__path__ = [str(ROOT / "custom_components" / "z2z_librus")]
sys.modules["z2z_librus"] = package
