"""Endpoint modules live here, one file per feature.

Any module in this package that defines a module-level `router` is included
automatically, so a new endpoint is added as a new file without editing
existing ones.
"""

import importlib
import pkgutil
import sys
from types import ModuleType

from fastapi import APIRouter


def discover_routers(package: ModuleType | None = None) -> list[APIRouter]:
    package = package or sys.modules[__name__]
    routers = []
    for module_info in sorted(pkgutil.iter_modules(package.__path__), key=lambda m: m.name):
        module = importlib.import_module(f"{package.__name__}.{module_info.name}")
        router = getattr(module, "router", None)
        if isinstance(router, APIRouter):
            routers.append(router)
    return routers
