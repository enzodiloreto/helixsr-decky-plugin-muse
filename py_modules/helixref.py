"""Dual-generation dispatch for HelixSR game operations (plugin code).

HelixSR <= 1.8.1 shipped its reference logic under Apache-2.0, vendored here
as helixscan and used directly. HelixSR 1.9.0+ is under the HelixSR Freeware
License (redistribution only unmodified and complete; no modification, no
derivative works), so its setup files are imported UNMODIFIED from the
downloaded version directory and driven through the small wrappers below
(stdout capture, signature normalization). Nothing in this file copies
upstream code; pure file helpers keep using the legacy module (identical
semantics in both generations).

Two modern generations exist: 1.9.x keeps everything in
setup/helixsr_install.py (games/Game/install/uninstall/install_scaler);
2.0+ split that into setup/helixsr_games.py (same games/Game/install/
uninstall surface, the DLL now under HelixSR-fsrfg/) plus
setup/helixsr_bridge.py (scaler installs with frame generation, removal,
settings). Both are loaded unmodified; the bridge's messages come back as
data via its _messages list.
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
# 2.0+ markers: the installer split into games module + bridge.
G3_GAMES = Path("setup/helixsr_games.py")
G3_BRIDGE = Path("setup/helixsr_bridge.py")
G3_DLL_DIR = "HelixSR-fsrfg"

_mod_cache: dict = {}
_bridge_cache: dict = {}


def generation(d) -> int:
    """1 for <=1.8.1 (vendored reference), 2 for 1.9.x, 3 for 2.0+."""
    p = Path(d)
    if (p / G3_GAMES).is_file() and (p / G3_BRIDGE).is_file():
        return 3
    return 2 if (p / MODERN_SETUP).is_file() else 1


def layout_of(d) -> str:
    """'modern' for 1.9.0+ version dirs, 'legacy' for older ones."""
    return "legacy" if generation(d) == 1 else "modern"


def dll_parent(d):
    """The version dir the games module's install/uninstall expect as
    `here`: HelixSR-fsrfg/ on 2.0+ (its DLL lives there), the root before."""
    d = Path(d)
    fsrfg = d / G3_DLL_DIR
    return fsrfg if (fsrfg / "amd_fidelityfx_dx12.dll").is_file() else d


def release_dll(d):
    """The release's HelixSR DLL (HelixSR-fsrfg/ on 2.0+, root before)."""
    return dll_parent(d) / "amd_fidelityfx_dx12.dll"


def install_module(d):
    """Reference install module for a version dir: vendored helixscan for
    legacy layouts, the release's own unmodified file for modern ones
    (imported once per dir; the module only defines constants/functions).
    2.0+ loads setup/helixsr_games.py — the file that kept the old
    games/Game/install/uninstall surface."""
    if generation(d) == 1:
        return helixscan
    key = str(Path(d).resolve())
    mod = _mod_cache.get(key)
    if mod is None:
        rel = MODERN_INSTALL if generation(d) == 2 else G3_GAMES
        path = Path(d).resolve() / rel
        name = "helixsr_install_%d" % (abs(hash(key)) % 10**8)
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise OSError(f"cannot load {path}")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        _mod_cache[key] = mod
    return mod


def bridge(d):
    """(bridge, games) for a 2.0+ dir: the release's own unmodified bridge
    module (loaded once per dir; INSTALLED_BY set to this plugin) plus the
    games module with its say() routed into the bridge's _messages, so op
    logs come back as data. Drain via _drain()."""
    if generation(d) != 3:
        raise OSError("this HelixSR has no setup/helixsr_bridge.py (2.0+ only)")
    key = str(Path(d).resolve())
    mod = _bridge_cache.get(key)
    if mod is None:
        path = Path(d).resolve() / G3_BRIDGE
        name = "helixsr_bridge_%d" % (abs(hash(key)) % 10**8)
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise OSError(f"cannot load {path}")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        mod.INSTALLED_BY = "the HelixSR Muse plugin"
        _bridge_cache[key] = mod
    hi = install_module(d)
    hi.say = lambda m: mod._messages.append(str(m).strip())
    return mod, hi


def reset_bridge(d):
    """Drop the cached bridge module for a version dir (its own --status
    cache goes with it): upstream caches the verdict for the life of the
    module, so a build would leave it stale until the plugin reloads."""
    _bridge_cache.pop(str(Path(d).resolve()), None)


def _drain(mod) -> str:
    """The bridge's messages since the last call (its op log)."""
    out = "\n".join(str(m) for m in getattr(mod, "_messages", []))
    mod._messages.clear()
    return out


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
    """Installable routes for a scanned game. Objects reporting their own
    ways (1.9.3+) win; legacy defers to the reference; other modern games
    only install the drop-in (legacy installs are removal-only)."""
    ways = getattr(g, "ways", None)
    if callable(ways):
        try:
            return list(ways())
        except Exception:  # noqa: BLE001
            pass
    mod = install_module(d)
    if mod is helixscan:
        return helixscan.available_routes(g, d)
    if getattr(g, "fsr_targets", None):
        return ["direct"]
    return []


def fg_engine_available(d) -> bool:
    """Whether this version can build HelixSR's frame generation: 1.9.0-1.9.2
    ship setup/fg/fg_setup.py; 1.9.3+ withdrew the FG engine (its setup says
    upscaling only, Scaler providers cover FG instead)."""
    return (Path(d) / "setup" / "fg" / "fg_setup.py").is_file()


def install(g, d):
    """Install via the reference implementation. Returns the captured log.
    Legacy honors a route pre-set on a copy of the game; modern switches
    legacy installs internally and takes no route. 2.0+ hands the games
    module the DLL's folder (HelixSR-fsrfg), not the version root."""
    mod = install_module(d)
    if mod is helixscan:
        helixscan.take_log()
        mod.install(g, Path(d))
        return "\n".join(helixscan.take_log())
    here = dll_parent(d) if generation(d) == 3 else Path(d)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        mod.install(g, here)
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


def has_scaler_support(d) -> bool:
    """Whether this version's reference implements HelixSR Scaler itself
    (1.9.3+: install_scaler); older modern versions need the plugin's own
    installer driven by the separate Scaler download."""
    try:
        return hasattr(install_module(d), "install_scaler")
    except Exception:  # noqa: BLE001
        return False


def uninstall_upstream_scaler(t, d):
    """Remove an upstream-recorded Scaler install via the reference
    implementation (its manifest, backups and remover). Returns the log."""
    mod = install_module(d)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        mod.uninstall_scaler(Path(t))
    return buf.getvalue()


def loader_name(d) -> str:
    """DLSS loader file name next to the game's exe (constant removed in the
    modern reference; its LOADER_NAMES tuple starts with the same value)."""
    mod = install_module(d)
    names = getattr(mod, "LOADER_NAMES", None)
    if isinstance(names, (tuple, list)) and names:
        return str(names[0])
    return str(getattr(mod, "LOADER", "dxgi.dll"))


# --- 2.0+ (generation 3) specific flows, via the release's own bridge ------

def g3_scaler_package(d):
    """(path, version) of the Scaler bundled in a 2.0+ release zip, per the
    bridge's own pairing logic; (None, None) when the folder is missing."""
    pkg = Path(d) / "helixsr-scaler"
    if not (pkg / "OptiScaler.dll").is_file():
        return None, None
    b, _hi = bridge(d)
    try:
        v = b.scaler_pkg_version(pkg)
    except Exception:  # noqa: BLE001
        v = None
    return pkg, ".".join(map(str, v)) if v else None


def g3_install(g, d, way: str = "scaler", fg: bool | None = None,
               anticheat_ok: bool = False) -> str:
    """Install one game through the release's bridge: 'scaler' (with frame
    generation when fg=True) or 'fsr' (the drop-in). Raises BridgeError /
    OSError on refusal; returns the bridge's message log."""
    b, hi = bridge(d)
    a = {"game": str(g.folder), "way": way}
    if way == "scaler":
        pkg, sv = g3_scaler_package(d)
        if pkg is None:
            raise OSError("this release zip has no helixsr-scaler folder: re-download it")
        a["scaler"] = str(pkg)
        if sv:
            a["scaler-version"] = sv
        if fg is not None:
            a["fg"] = "on" if fg else "off"
        if anticheat_ok:
            a["anticheat-ok"] = "yes"
    b.cmd_install(hi, Path(d), a)
    return _drain(b)


def g3_remove(g, d) -> str:
    """Remove every HelixSR install (Scaler, drop-in, earlier versions) from
    one game via the release's bridge. Returns the bridge's message log."""
    b, hi = bridge(d)
    b.cmd_remove(hi, Path(d), {"game": str(g.folder)})
    return _drain(b)
