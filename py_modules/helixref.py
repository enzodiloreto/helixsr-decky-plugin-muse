"""Dual-generation dispatch for HelixSR game operations (plugin code).

HelixSR <= 1.8.1 shipped its reference logic under Apache-2.0, vendored here
as helixscan and used directly. HelixSR 1.9.0+ is under the HelixSR Freeware
License (redistribution only unmodified and complete; no modification, no
derivative works), so its setup/helixsr_install.py is imported UNMODIFIED
from the downloaded version directory and driven through the small wrappers
below (stdout capture, signature normalization). Nothing in this file copies
upstream code; pure file helpers keep using the legacy module (identical
semantics in both generations).
"""
import contextlib
import importlib.util
import io
import os
import sys
from pathlib import Path

import helixscan

# Positive marker of the 1.9.0+ layout (absent in older releases).
MODERN_SETUP = Path("setup/helixsr_setup.py")
MODERN_INSTALL = Path("setup/helixsr_install.py")

_mod_cache: dict = {}


def layout_of(d) -> str:
    """'modern' for 1.9.0+ version dirs, 'legacy' for older ones."""
    return "modern" if (Path(d) / MODERN_SETUP).is_file() else "legacy"


def install_module(d):
    """Reference install module for a version dir: vendored helixscan for
    legacy layouts, the release's own unmodified file for modern ones
    (imported once per dir; the module only defines constants/functions)."""
    if layout_of(d) == "legacy":
        return helixscan
    key = str(Path(d).resolve())
    mod = _mod_cache.get(key)
    if mod is None:
        path = Path(d).resolve() / MODERN_INSTALL
        name = "helixsr_install_%d" % (abs(hash(key)) % 10**8)
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise OSError(f"cannot load {path}")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        _mod_cache[key] = mod
    return mod


def scan(d, extra, stop=None):
    """(games, skipped, log). Legacy honors the stop callback; the modern
    reference has no stop hook, so cancelling only discards its results."""
    mod = install_module(d)
    if mod is helixscan:
        helixscan.take_log()
        gl, skipped = helixscan.games(Path(d), list(extra or []), stop)
        return gl, skipped, "\n".join(helixscan.take_log())
    # The modern reference locates the user via Path.home(); the legacy
    # adaptation (kept) prefers DECKY_USER_HOME for a host process running
    # with a different home. Point HOME there for the call (all scan call
    # sites hold _ops_lock, so no other scan can interleave).
    home = os.environ.get("DECKY_USER_HOME")
    old_home = os.environ.get("HOME")
    if home:
        os.environ["HOME"] = home
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            gl = mod.games(list(extra or []))
    finally:
        if home:
            if old_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = old_home
    return gl, 0, buf.getvalue()


def available_routes(g, d):
    """Installable routes for a scanned game. Legacy defers to the reference;
    modern only installs the drop-in (legacy installs are removal-only)."""
    mod = install_module(d)
    if mod is helixscan:
        return helixscan.available_routes(g, d)
    if getattr(g, "fsr_targets", None):
        return ["direct"]
    return []


def install(g, d):
    """Install via the reference implementation. Returns the captured log.
    Legacy honors a route pre-set on a copy of the game; modern switches
    legacy installs internally and takes no route."""
    mod = install_module(d)
    if mod is helixscan:
        helixscan.take_log()
        mod.install(g, Path(d))
        return "\n".join(helixscan.take_log())
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        mod.install(g, Path(d))
    return buf.getvalue()


def uninstall(g, d):
    """Uninstall via the reference implementation. Returns the captured log."""
    mod = install_module(d)
    if mod is helixscan:
        helixscan.take_log()
        mod.uninstall(g)
        return "\n".join(helixscan.take_log())
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        mod.uninstall(g)
    return buf.getvalue()


def loader_name(d) -> str:
    """DLSS loader file name next to the game's exe (constant removed in the
    modern reference; its LOADER_NAMES tuple starts with the same value)."""
    mod = install_module(d)
    names = getattr(mod, "LOADER_NAMES", None)
    if isinstance(names, (tuple, list)) and names:
        return str(names[0])
    return str(getattr(mod, "LOADER", "dxgi.dll"))
