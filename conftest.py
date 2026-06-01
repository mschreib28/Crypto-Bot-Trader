"""
Root conftest.py — runs before any test collection or import.

The backend/redis/ package directory shadows the 'redis' pip package.
We pre-load the real redis package here (before pytest collects any test
files) and store it in sys.modules under both 'redis' and '_redis_real' so
that backend/redis/__init__.py can find it without causing circular imports.
"""
import importlib.util
import os
import sys


def _preload_real_redis():
    """Load the real 'redis' pip package before any backend code imports it."""
    # Already done?
    if "_redis_real" in sys.modules:
        return

    # Find the site-packages directory that contains the real redis package.
    # We look for a redis/__init__.py that is NOT inside the project tree.
    project_root = os.path.dirname(__file__)
    project_backend_redis = os.path.join(project_root, "backend", "redis")

    for search_dir in sys.path:
        redis_init = os.path.join(search_dir, "redis", "__init__.py")
        if not os.path.isfile(redis_init):
            continue
        # Skip the project's backend/redis package
        redis_dir = os.path.join(search_dir, "redis")
        if os.path.realpath(redis_dir) == os.path.realpath(project_backend_redis):
            continue

        # Load it under a private name first to avoid touching sys.modules['redis']
        spec = importlib.util.spec_from_file_location(
            "_redis_real",
            redis_init,
            submodule_search_locations=[redis_dir],
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules["_redis_real"] = mod

        # Also register all the submodules redis needs under their real names
        # so that relative imports inside the package work correctly.
        # We do this by temporarily making sys.modules["redis"] point to our mod.
        sys.modules["redis"] = mod
        spec.loader.exec_module(mod)
        # Keep sys.modules["redis"] pointing to the real package.
        return

    raise ImportError(
        "conftest.py: Cannot find the real 'redis' pip package in sys.path. "
        "Install it with: pip install redis"
    )


_preload_real_redis()
