"""HelixSR 2.0+ bridge client (plugin code).

Runs the release's own setup/helixsr_bridge.py as a subprocess and parses
the single JSON object it prints. The release's files are never imported,
so upstream code runs in its own process and cannot take down the plugin
backend. The CLI contract (commands, --key value args, one JSON object on
stdout with {protocol, ok, ..., error?, messages[]}) is the release's own
documented interface.
"""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

BRIDGE_REL = Path("setup/helixsr_bridge.py")
SETUP_REL = Path("setup/helixsr_setup.py")
DLL_REL = Path("HelixSR-fsrfg/amd_fidelityfx_dx12.dll")


class BridgeError(Exception):
    """The bridge refused or could not answer (message is user-facing)."""


_python = None


def python_path() -> str:
    """A Python 3 interpreter for the bridge. Never the running one: inside
    Decky that is the frozen PluginLoader binary, and running it with a script
    argument starts a second Decky loader whose startup restarts Steam's web UI
    (decky_loader/main.py: inject_javascript -> restart_webhelper)."""
    global _python
    if _python is None:
        for cand in (shutil.which("python3"), "/usr/bin/python3"):
            if cand and os.access(cand, os.X_OK):
                _python = cand
                break
        else:
            raise BridgeError("no Python 3 found: HelixSR's bridge needs one (install python3)")
    return _python


def script_of(release_dir) -> Path:
    return Path(release_dir) / BRIDGE_REL


def is_release(d) -> bool:
    """Whether this dir is a HelixSR 2.0+ release (bridge + built DLL dir)."""
    d = Path(d)
    return (d / BRIDGE_REL).is_file() and (d / DLL_REL).is_file()


def release_dll(d) -> Path:
    return Path(d) / DLL_REL


def run(release_dir, command: str, args: dict | None = None, timeout: int = 300) -> dict:
    """Run one bridge command, return its JSON object (with messages).

    args map to --key value (lists repeat the flag, None skips it).
    Raises BridgeError with the bridge's own error text when it refuses.
    """
    script = script_of(release_dir)
    if not script.is_file():
        raise BridgeError(f"not a HelixSR 2.0+ release (no {BRIDGE_REL}): {release_dir}")
    argv = [python_path(), "-I", str(script), str(Path(release_dir).resolve()), command]
    for key, value in (args or {}).items():
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            for item in value:
                argv += [f"--{key}", str(item)]
        else:
            argv += [f"--{key}", str(value)]
    env = dict(os.environ)
    home = os.environ.get("DECKY_USER_HOME")
    if home:
        # The scan locates the user via HOME; a host process may run with
        # a different one (Decky sets DECKY_USER_HOME for exactly this).
        env["HOME"] = home
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        raise BridgeError(f"{command} timed out after {timeout}s")
    except OSError as e:
        raise BridgeError(f"could not run {command}: {e}")
    lines = [line for line in (proc.stdout or "").splitlines() if line.strip()]
    try:
        out = json.loads(lines[-1]) if lines else None
    except ValueError:
        out = None
    if not isinstance(out, dict) or "ok" not in out:
        tail = (proc.stderr or "").strip().splitlines()[-3:]
        detail = " ".join(tail).strip() or (lines[-1][-200:] if lines else "no output")
        raise BridgeError(f"{command} gave no answer ({detail})")
    if not out.get("ok"):
        raise BridgeError(out.get("error") or f"{command} failed")
    out.setdefault("messages", [])
    return out


def status(release_dir) -> dict:
    """{version, network_ready, fg_ready, fg_reason, release}."""
    return run(release_dir, "status", timeout=120)


def games(release_dir, extra: list | None = None) -> dict:
    """{games: [...]}. extra = hand-picked game folders added to the scan."""
    return run(release_dir, "games", {"folder": list(extra or [])}, timeout=600)


def dll_version(dll) -> str:
    """The build version stamped in HelixSR's DLL footer (same bytes the
    release's own version_of reads: NUL HelixSR X.Y.Z NUL)."""
    try:
        m = re.search(rb"\x00HelixSR (\d+\.\d+\.\d+)\x00", Path(dll).read_bytes()[:4 << 20])
        return m.group(1).decode() if m else "?"
    except OSError:
        return "?"


def bundled_scaler_version(release_dir):
    """(pkg_dir, version|None) of the Scaler shipped inside the release zip:
    its own version stamp (HelixSR Scaler vX.Y.Z) read from its DLL."""
    pkg = Path(release_dir) / "helixsr-scaler"
    if not (pkg / "OptiScaler.dll").is_file():
        return None, None
    try:
        data = (pkg / "OptiScaler.dll").read_bytes()[:64 << 20]
        m = re.search(rb"HelixSR Scaler v(\d+\.\d+\.\d+)", data)
        return pkg, (m.group(1).decode() if m else None)
    except OSError:
        return pkg, None
