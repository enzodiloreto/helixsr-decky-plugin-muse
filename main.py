import asyncio
import configparser
import copy
import json
import os
import re
import shutil
import signal
import ssl
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

# The decky plugin module is located in decky-loader/plugin
# For easy intellisense checkout the decky-loader/plugin code repo
# and add the `decky-loader/plugin/imports` path to `python.analysisExtraPaths` in `.vscode/settings.json`
import decky
import bridge
from settings import SettingsManager

# HelixSR 2.0+ only. Every game operation goes through the release's own
# setup/helixsr_bridge.py as a subprocess (py_modules/bridge.py); the
# release's files are never imported. Nothing in this plugin copies upstream
# code: the DLL/scaler version stamps and the {Section.Key: value} settings
# shape are the bridge's documented interface, re-read here for display.
GITHUB_OWNER = "lonewolf0622"
GITHUB_REPO = "HelixSR"
RELEASES_URL = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases?per_page=30"

# Frame-generation build inputs (upstream pins; factual interop values).
DLSSG_SL_URL = "https://github.com/NVIDIA-RTX/Streamline/releases/download/v2.8.0/streamline-sdk-v2.8.0.zip"
DLSSG2_SL_URL = "https://github.com/NVIDIA-RTX/Streamline/releases/download/v2.14.16/streamline-sdk-v2.14.16.zip"
DLSSG_ENTRY = "bin/x64/nvngx_dlssg.dll"
DLSSG_SHA256 = "0a6212a33ebaa708e7907468db7345c92cfdc6f8dd29c6e1248f32dbea4bcdaf"
DLSSG_FILE = "nvngx_dlssg.dll"
DLSSG2_SHA256 = "8d59d49b36410dd6b62fef2503a5871d347415bd85d943fa0848563a95dd50c7"
DLSSG2_FILE = "nvngx_dlssg_310.9.16.dll"

# NVIDIA's DLSS SDK DLL the 2.0 setup builds the network from, hash-pinned.
# The plugin fetches it with its own TLS (the setup's Python may lack SSL)
# and hands it to the setup via --dlss.
DLSS_URL = 'https://raw.githubusercontent.com/NVIDIA/DLSS/v310.7.0/lib/Windows_x86_64/rel/nvngx_dlss.dll'
DLSS_SHA256 = 'be6e434a94ca32499515eb62ca0e6c274526055d568d0426e4c652dcdfb6ee6e'
DLSS_FILE = 'nvngx_dlss.dll'

# 2.0 build toolchain pins (parsed from the release's own helixsr-setup.sh,
# so a newer release updates them; these are the fallback).
G3_PY_URL = ("https://github.com/astral-sh/python-build-standalone/releases/download/"
             "20261003/cpython-3.12.15%2B20261003-x86_64-unknown-linux-gnu-install_only.tar.gz")
G3_PY_SHA = "f937814031eab4698ca6d07ec606ede1825768f3f3e99af76d9db3900bee03c5"
G3_DXC_URL = ("https://github.com/microsoft/DirectXShaderCompiler/releases/download/"
              "v1.9.2607/dxc_2026_07_29.zip")
G3_DXC_SHA = "a1dfb116ba3eeae6a1582291b53a8e7bf65ad760676bd3194685c8f7367cd241"

for _pin in (DLSS_SHA256, DLSSG_SHA256, DLSSG2_SHA256, G3_PY_SHA, G3_DXC_SHA):
    assert len(_pin) == 64 and all(c in "0123456789abcdef" for c in _pin), \
        "hash pin malformed (must be 64 hex chars)"
del _pin


def _helixsr_root() -> Path:
    return Path(os.environ.get("DECKY_PLUGIN_RUNTIME_DIR", tempfile.gettempdir())) / "helixsr"


_SSL_CONTEXT = None


def _ssl_context() -> ssl.SSLContext:
    """TLS context using the CA bundle shipped with the plugin (the Python
    running Decky plugins on SteamOS may not find system CA certificates)."""
    global _SSL_CONTEXT
    if _SSL_CONTEXT is None:
        bundle = Path(__file__).parent / "py_modules" / "cacert.pem"
        _SSL_CONTEXT = (ssl.create_default_context(cafile=str(bundle)) if bundle.is_file()
                        else ssl.create_default_context())
    return _SSL_CONTEXT


def _fetch_json(url: str, timeout: int = 30):
    req = urllib.request.Request(url, headers={"User-Agent": "helixsr-decky-plugin",
                                               "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as r:
        return json.loads(r.read().decode("utf-8"))


def _version_dirs() -> list[Path]:
    """Downloaded 2.0+ releases (a bridge script marks the layout)."""
    root = _helixsr_root()
    if not root.is_dir():
        return []
    return sorted([p for p in root.iterdir()
                   if p.is_dir() and (p / "setup" / "helixsr_bridge.py").is_file()])


def _tag_of_dir(d: Path) -> str:
    m = re.search(r"(\d+\.\d+\.\d+)", d.name)
    return f"v{m.group(1)}" if m else d.name


def _dir_for_tag(tag: str) -> Path | None:
    want = str(tag or "").lstrip("vV")
    for d in _version_dirs():
        if want and want in d.name:
            return d
    return None


def _zip_top(z: zipfile.ZipFile) -> str:
    tops = set()
    for info in z.infolist():
        name = info.filename
        if name.startswith("/") or ".." in Path(name).parts or info.is_dir():
            continue
        parts = name.split("/")
        if len(parts) > 1:
            tops.add(parts[0])
    if len(tops) == 1:
        return tops.pop()
    return z.namelist()[0].split("/")[0]


def _restore_zip_modes(z: zipfile.ZipFile, root: Path):
    for info in z.infolist():
        if info.filename.startswith("/") or ".." in Path(info.filename).parts:
            continue
        mode = (info.external_attr >> 16) & 0o777
        dest = root / info.filename
        if mode and dest.is_file():
            try:
                dest.chmod(mode)
            except OSError:
                pass


def _repair_exec_bits(d: Path) -> list[str]:
    fixed = []
    for rel in ("helixsr-setup.sh", "helixsr-uninstall.sh"):
        p = d / rel
        try:
            if p.is_file() and not os.access(p, os.X_OK):
                p.chmod(0o755)
                fixed.append(rel)
        except OSError:
            pass
    return fixed


def _sha256_file(p: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _dir_size(d: Path) -> int:
    total = 0
    try:
        for root, _dirs, files in os.walk(d):
            for n in files:
                try:
                    total += (Path(root) / n).stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


_dir_size_cache: dict = {}


def _dir_size_cached(d: Path) -> int:
    key = str(d)
    try:
        mtime = d.stat().st_mtime_ns
    except OSError:
        return _dir_size(d)
    hit = _dir_size_cache.get(key)
    if hit and hit[0] == mtime:
        return hit[1]
    size = _dir_size(d)
    _dir_size_cache[key] = (mtime, size)
    return size


def _free_bytes(p: Path) -> int:
    q = p
    while not q.exists():
        if q.parent == q:
            return 0
        q = q.parent
    try:
        return shutil.disk_usage(q).free
    except OSError:
        return 0


def _mem_available_mb() -> int:
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        pass
    return 0


def _swap_free_mb() -> int:
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("SwapFree:"):
                    return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        pass
    return 0


def _mem_line() -> str:
    return f"mem {_mem_available_mb()} MB available, {_swap_free_mb()} MB swap free"


def _compile_jobs_env() -> dict:
    """Cap the setup's shader-compile fan-out so the Steam UI survives it
    (each DXC/Wine worker needs ~600 MB; no knob exists for the FG phase,
    which is gated separately instead). An explicit user setting wins."""
    env = {}
    if not os.environ.get("HELIXSR_SETUP_JOBS"):
        cpus = os.cpu_count() or 4
        env["HELIXSR_SETUP_JOBS"] = str(max(2, min(cpus, (_mem_available_mb() - 1500) // 900)))
    return env


def _fg_compile_fits() -> tuple[bool, str]:
    """The FG phase runs one DXC/Wine worker per CPU thread with no memory
    cap: refuse it when headroom is gone (suspended game + Steam UI)."""
    workers = os.cpu_count() or 4
    have = _mem_available_mb()
    need = workers * 700 + 1500
    if have >= need:
        return True, f"{have // 1024} GB free"
    return False, (f"the FG shader compile runs {workers} DXC/Wine workers at once "
                   f"(~{workers * 700 // 1024} GB) and needs headroom for the Steam UI: "
                   f"{have // 1024} GB free. Close the game and retry, or build without FG")


BUILD_PID_FILE = "build.pid"


def _kill_build_tree(pid: int) -> bool:
    """SIGTERM a build's whole process group (setup + Wine/DXC workers).
    Only when the pid still is the HelixSR setup (pid-reuse guard)."""
    import signal
    try:
        cmd = open(f"/proc/{pid}/cmdline", "rb").read().replace(b"\0", b" ").decode(
            "utf-8", errors="replace")
        if "helixsr_setup" not in cmd and "helixsr-setup" not in cmd:
            return False
        os.killpg(os.getpgid(pid), signal.SIGTERM)
        return True
    except (OSError, ValueError):
        return False


def _reap_orphaned_build():
    pid_file = _helixsr_root() / BUILD_PID_FILE
    try:
        old = int(pid_file.read_text().strip())
    except (OSError, ValueError):
        return
    if _kill_build_tree(old):
        decky.logger.info(f"HelixSR killed orphaned build process {old}")
    pid_file.unlink(missing_ok=True)


def _split_version(tag: str) -> list:
    t = str(tag or "").strip()
    if t[:1].lower() == "v":
        t = t[1:]
    t = re.split(r"[-+]", t, 1)[0]
    parts = []
    for x in t.split("."):
        m = re.match(r"\d+", x)
        parts.append(int(m.group(0)) if m else 0)
    return parts


def _compare_versions(a: str, b: str) -> int:
    pa, pb = _split_version(a), _split_version(b)
    n = max(len(pa), len(pb))
    pa += [0] * (n - len(pa))
    pb += [0] * (n - len(pb))
    return (pa > pb) - (pa < pb)


def _helixsr_asset(release: dict, tag: str):
    assets = [a for a in (release.get("assets") or []) if str(a.get("name", "")).endswith(".zip")]
    if not assets:
        return None
    want = "helixsr-%s.zip" % str(tag).lstrip("vV").lower()
    for a in assets:
        if str(a.get("name", "")).lower() == want:
            return a
    return sorted(assets, key=lambda x: x.get("size", 0) or 0, reverse=True)[0]


PLUGIN_REPO = "enzodiloreto/helixsr-decky-plugin-muse"
PLUGIN_RELEASES_URL = f"https://api.github.com/repos/{PLUGIN_REPO}/releases?per_page=5"
PLUGIN_PACKAGE_NAME = "helixsr-muse-plugin"
PLUGIN_DISPLAY_NAME = "HelixSR Muse Plugin"
PLUGIN_UPDATE_TTL = 6 * 3600

# Settings keys the bridge edits, per settings kind (its documented schema).
# Presets and the settings form are built from these; unknown keys the
# release reports are shown read-only under "other".
HELIXSR_SETTINGS = {
    "Sharpening.Sharpener": ("off", "nvidia", "rcas"),
    "Sharpening.Sharpness": None,
    "Sharpening.UseGameSlider": ("true", "false"),
    "Compatibility.WaveSize": ("auto", "default", "64", "32"),
}
SCALER_SETTINGS = {
    "HelixSR.WaveSize": ("auto", "64", "32"),
    "HelixSR.FGPreset": ("auto", "c", "full"),
    "HelixSR.QuickFlow": ("auto", "off", "fast", "fastest"),
}
HELIXSR_DEFAULTS = {"Sharpening.Sharpener": "off", "Sharpening.Sharpness": "0.1",
                    "Sharpening.UseGameSlider": "false", "Compatibility.WaveSize": "auto"}
SCALER_DEFAULTS = {k: "auto" for k in SCALER_SETTINGS}
# Values older Scaler files use, mapped to the current spellings.
SCALER_ALIASES = {"HelixSR.FGPreset": {"light": "c", "default": "full"},
                  "HelixSR.QuickFlow": {"on": "fast", "true": "fast", "1": "fast",
                                        "quickflow": "fast", "false": "off", "0": "off"}}
MAX_INI_PRESETS = 20
BUILTIN_DEFAULT_PRESET = "Default"

SCAN_CACHE_TTL = 300
BUILD_TIMEOUT = 4 * 3600


def _new_dl_state(tag: str = "") -> dict:
    return {"running": False, "done": False, "ok": False, "cancelled": False, "error": "",
            "tag": tag, "downloaded": 0, "total": 0, "dir": "", "network_built": False}


def _new_scan_state() -> dict:
    return {"running": False, "done": False, "ok": False, "cancelled": False, "error": "",
            "games": [], "tag": "", "log": ""}


def _empty_scan_cache() -> dict:
    return {"dir": "", "games": [], "tag": "", "extra": [], "time": 0.0}


def _new_mig_state() -> dict:  # kept for API shape; unused (no 1.x anymore)
    return {"running": False, "done": False, "ok": False, "error": "",
            "tag": "", "total": 0, "current": 0, "current_name": "", "results": []}


class Plugin:
    async def _main(self):
        self.settings = SettingsManager("helixsr", decky.DECKY_PLUGIN_SETTINGS_DIR)
        self._dl = _new_dl_state()
        self._dl_stop = threading.Event()
        self._build_log: Path | None = None
        self._build_proc = None
        self._build_cancel = threading.Event()
        self._build_lock = threading.Lock()
        self._scan = _new_scan_state()
        self._scan_stop = threading.Event()
        self._scan_cache = _empty_scan_cache()
        self._scan_lock = threading.Lock()
        self._update_check = {"at": 0.0, "result": {"ok": True, "update_available": False}}
        self._pu = {"running": False, "done": False, "ok": False, "error": "",
                    "version": "", "url": "", "downloaded": 0, "total": 0,
                    "mode": "", "path": ""}
        self._pu_stop = threading.Event()
        self._update_pending = ""
        self._alive = True
        _reap_orphaned_build()
        decky.logger.info("HelixSR Muse Plugin loaded")

    async def _unload(self):
        decky.logger.info("HelixSR unloading")
        self._alive = False
        proc, self._build_proc = self._build_proc, None
        if proc is not None and proc.returncode is None:
            try:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                except (ProcessLookupError, PermissionError, OSError):
                    proc.terminate()
            except ProcessLookupError:
                pass
        self._dl_stop.set()
        self._scan_stop.set()
        self._build_cancel.set()
        decky.logger.info("HelixSR unloaded")

    # -- helpers -----------------------------------------------------------

    def _active_dir(self) -> Path | None:
        tag = self.settings.getSetting("active_tag", "")
        d = _dir_for_tag(tag) if tag else None
        if d is None:
            ds = _version_dirs()
            d = ds[-1] if ds else None
        return d

    def _extra_folders(self) -> list:
        try:
            folders = self.settings.getSetting("extra_folders", [])
            return [str(f) for f in folders] if isinstance(folders, list) else []
        except Exception:  # noqa: BLE001
            return []

    async def _active_dir_async(self) -> Path | None:
        return await asyncio.to_thread(self._active_dir)

    def _version_info(self, d: Path) -> dict:
        try:
            st = bridge.status(d)
        except bridge.BridgeError:
            st = {}
        pkg, sver = bridge.bundled_scaler_version(d)
        return {"tag": _tag_of_dir(d), "dir": d.name,
                "size_mb": round(_dir_size_cached(d) / 1e6, 1),
                "dll_version": st.get("version") or bridge.dll_version(bridge.release_dll(d)),
                "network_built": bool(st.get("network_ready")),
                "fg_built": bool(st.get("fg_ready")),
                "fg_reason": st.get("fg_reason") or "",
                "scaler_version": sver}

    # -- releases / state ---------------------------------------------------

    async def get_releases(self, include_prereleases: bool = False) -> dict:
        def _fetch():
            try:
                data = _fetch_json(RELEASES_URL)
            except urllib.error.HTTPError as e:
                try:
                    detail = e.read().decode("utf-8", errors="replace")[:300]
                except Exception:  # noqa: BLE001
                    detail = ""
                err = f"GitHub API HTTP {e.code}: {detail}".strip()
                if e.code in (403, 429):
                    err += " (possible API rate limit — wait a bit and reopen the panel)"
                decky.logger.warning(f"get_releases failed: {err}")
                return {"releases": [], "error": err}
            except Exception as e:  # noqa: BLE001
                err = f"{type(e).__name__}: {e}"
                decky.logger.warning(f"get_releases failed: {err}")
                return {"releases": [], "error": err}
            out = []
            for r in data:
                if not isinstance(r, dict) or r.get("draft"):
                    continue
                if r.get("prerelease") and not include_prereleases:
                    continue
                tag = r.get("tag_name", "")
                a = _helixsr_asset(r, tag)
                if not tag or a is None:
                    continue
                out.append({"tag": tag, "name": r.get("name", tag),
                            "published_at": r.get("published_at", "")[:10],
                            "zip_name": a["name"], "zip_url": a["browser_download_url"],
                            "size": a.get("size", 0),
                            "body": (r.get("body") or "")[:2000]})
            return {"releases": out, "error": ""}
        return await asyncio.to_thread(_fetch)

    async def get_state(self) -> dict:
        def _state():
            versions = []
            for d in _version_dirs():
                try:
                    versions.append(self._version_info(d))
                except Exception as e:  # noqa: BLE001 (one bad dir never hides the rest)
                    decky.logger.warning(f"get_state skipped {d.name}: {e}")
            return {"active_tag": self.settings.getSetting("active_tag", ""),
                    "versions": versions, "root": str(_helixsr_root()),
                    "plugin_version": _plugin_version(),
                    "update_pending": self._update_pending,
                    "extra_folders": self._extra_folders()}
        return await asyncio.to_thread(_state)

    async def set_active_version(self, tag: str) -> dict:
        self.settings.setSetting("active_tag", tag)
        self.settings.commit()
        decky.logger.info(f"HelixSR active_tag={tag}")
        return await self.get_state()

    # -- version download ----------------------------------------------------

    async def start_download(self, tag: str, zip_url: str, size: int = 0) -> dict:
        """Download + extract a 2.0+ release in a background thread."""
        if self._dl["running"]:
            return {"ok": False, "error": "a download is already running"}
        if self._build_lock.locked():
            return {"ok": False, "error": "a build is running — download after it finishes"}
        try:
            want = max(600_000_000, int(size or 0) * 5 // 2 + 100_000_000)
        except (TypeError, ValueError):
            want = 600_000_000
        if _free_bytes(_helixsr_root()) < want:
            free = _free_bytes(_helixsr_root())
            return {"ok": False, "error": (
                f"not enough free disk for {tag} "
                f"(need ~{want / 1e9:.1f} GB, have {free / 1e9:.1f} GB): "
                "free space or remove old versions first")}
        self._dl = _new_dl_state(tag)
        self._dl["running"] = True
        self._dl_stop.clear()
        threading.Thread(target=self._download_run, args=(tag, zip_url), daemon=True).start()
        return {"ok": True}

    async def cancel_download(self) -> dict:
        if not self._dl["running"]:
            return {"ok": False, "error": "no download running"}
        self._dl_stop.set()
        return {"ok": True}

    def _download_run(self, tag: str, zip_url: str):
        st = self._dl
        tmp: Path | None = None
        top = ""
        had_dir = True
        dlog = _helixsr_root() / f"download-{tag.lstrip('v') or 'x'}.log"
        try:
            root = _helixsr_root()
            root.mkdir(parents=True, exist_ok=True)
            tmp = root / f".{tag}.zip"
            req = urllib.request.Request(zip_url, headers={"User-Agent": "helixsr-decky-plugin"})
            with open(dlog, "w") as dl:
                dl.write(f"[helixsr] download {tag} start: {zip_url} — {_mem_line()}\n")
                last_q = 0
                with urllib.request.urlopen(req, timeout=120, context=_ssl_context()) as r:
                    try:
                        st["total"] = int(r.headers.get("Content-Length") or 0)
                    except (TypeError, ValueError):
                        st["total"] = 0
                    with open(tmp, "wb") as f:
                        while True:
                            if self._dl_stop.is_set():
                                break
                            chunk = r.read(1 << 20)
                            if not chunk:
                                break
                            f.write(chunk)
                            st["downloaded"] += len(chunk)
                            if st["total"]:
                                q = (100 * st["downloaded"]) // st["total"] // 25
                                if q > last_q:
                                    last_q = q
                                    dl.write(f"[helixsr] {q * 25}% — {_mem_line()}\n")
            if self._dl_stop.is_set():
                tmp.unlink(missing_ok=True)
                st["cancelled"] = True
                st["error"] = "download cancelled"
                return
            if st["total"] and st["downloaded"] != st["total"]:
                raise OSError(f"incomplete download: got {st['downloaded']} of {st['total']} bytes")
            with open(dlog, "a") as dl:
                dl.write(f"[helixsr] extracting — {_mem_line()}\n")
            with zipfile.ZipFile(tmp) as z:
                top = _zip_top(z)
                had_dir = (root / top).is_dir()
                for info in z.infolist():
                    if self._dl_stop.is_set():
                        raise OSError("download cancelled")
                    if info.filename.startswith("/") or ".." in Path(info.filename).parts:
                        continue
                    z.extract(info, root)
                _restore_zip_modes(z, root)
            tmp.unlink(missing_ok=True)
            d = root / top
            if not bridge.is_release(d):
                raise OSError(f"not a HelixSR 2.0+ release layout (no setup/helixsr_bridge.py or "
                              f"HelixSR-fsrfg DLL in {top})")
            _repair_exec_bits(d)
            try:
                st["network_built"] = bool(bridge.status(d).get("network_ready"))
            except bridge.BridgeError:
                st["network_built"] = False
            st["dir"] = top
            with open(dlog, "a") as dl:
                dl.write(f"[helixsr] done ok — {_mem_line()}, network_built={st['network_built']}\n")
        except Exception as e:  # noqa: BLE001
            decky.logger.error(f"download {tag} failed: {e}")
            if self._dl_stop.is_set():
                st["cancelled"] = True
                st["error"] = "download cancelled"
            else:
                st["error"] = str(e)
            try:
                if top and not had_dir:
                    shutil.rmtree(root / top, ignore_errors=True)
            except OSError:
                pass
            try:
                with open(dlog, "a") as dl:
                    dl.write(f"[helixsr] failed: {e} — {_mem_line()}\n")
            except OSError:
                pass
            if tmp is not None:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
        else:
            st["ok"] = True
        finally:
            st["running"] = False
            st["done"] = True

    async def get_download_status(self) -> dict:
        return dict(self._dl)

    # -- storage --------------------------------------------------------------

    async def remove_version(self, tag: str) -> dict:
        d = _dir_for_tag(tag)
        if d is None:
            return {"ok": False, "error": f"not downloaded: {tag}"}
        active = self.settings.getSetting("active_tag", "")
        if active and active in (tag, _tag_of_dir(d)):
            others = [v for v in _version_dirs() if v != d]
            self.settings.setSetting("active_tag", _tag_of_dir(others[-1]) if others else "")
            self.settings.commit()
        try:
            size = _dir_size(d)
            shutil.rmtree(d, ignore_errors=True)
            return {"ok": True, "freed_mb": round(size / 1e6, 1)}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    async def remove_old_versions(self) -> dict:
        active = await asyncio.to_thread(self._active_dir)
        removed, skipped, freed = [], [], 0
        for d in _version_dirs():
            if active is not None and d == active:
                skipped.append(_tag_of_dir(d))
                continue
            freed += _dir_size(d)
            shutil.rmtree(d, ignore_errors=True)
            removed.append(_tag_of_dir(d))
        if active is None and removed:
            self.settings.setSetting("active_tag", "")
            self.settings.commit()
        return {"ok": True, "removed": removed, "skipped": skipped,
                "freed_mb": round(freed / 1e6, 1)}

    async def factory_reset(self) -> dict:
        """Remove every HelixSR install from every scanned game, then delete
        all downloaded versions. Returns what was done."""
        d = await asyncio.to_thread(self._active_dir)
        uninstalled, failed = [], []
        if d is not None:
            try:
                games = await asyncio.to_thread(self._scan_games_run, d, self._extra_folders())
                for g in games:
                    if not g.get("installed"):
                        continue
                    try:
                        bridge.run(d, "remove", {"game": g["folder"]}, timeout=300)
                        uninstalled.append(g["name"])
                    except bridge.BridgeError as e:
                        failed.append(f"{g['name']}: {e}")
            except bridge.BridgeError as e:
                return {"ok": False, "error": str(e)}
        versions_removed = []
        for v in _version_dirs():
            versions_removed.append(_tag_of_dir(v))
            shutil.rmtree(v, ignore_errors=True)
        self.settings.setSetting("active_tag", "")
        self.settings.commit()
        return {"ok": True, "games_uninstalled": uninstalled, "games_failed": failed,
                "versions_removed": versions_removed}

    # -- plugin self-update (unchanged mechanics) -------------------------------

    async def check_plugin_update(self, force: bool = False) -> dict:
        return await asyncio.to_thread(self._check_plugin_update_sync, force)

    def _check_plugin_update_sync(self, force: bool = False) -> dict:
        now = time.monotonic()
        if not force and self._update_check["at"] and now - self._update_check["at"] < PLUGIN_UPDATE_TTL:
            return self._update_check["result"]
        result: dict = {"ok": True, "current": _plugin_version(),
                        "update_available": False, "latest": "", "url": "", "size": 0, "body": ""}
        try:
            data = _fetch_json(PLUGIN_RELEASES_URL)
            best = None
            for r in data:
                if not isinstance(r, dict) or r.get("draft") or r.get("prerelease"):
                    continue
                tag = str(r.get("tag_name", "")).lstrip("v")
                if not tag or _compare_versions(tag, result["current"]) <= 0:
                    continue
                if best is None or _compare_versions(tag, str(best.get("tag_name", "")).lstrip("v")) > 0:
                    best = r
            if best is not None:
                assets = [a for a in (best.get("assets") or [])
                          if str(a.get("name", "")).endswith(".zip")]
                if assets:
                    a = sorted(assets, key=lambda x: x.get("size", 0) or 0, reverse=True)[0]
                    result.update({"update_available": True, "latest": str(best["tag_name"]).lstrip("v"),
                                   "url": a["browser_download_url"], "size": a.get("size", 0),
                                   "body": (best.get("body") or "")[:2000]})
        except Exception as e:  # noqa: BLE001
            result = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        self._update_check = {"at": now, "result": result}
        return result

    async def start_plugin_update(self) -> dict:
        if self._pu["running"]:
            return {"ok": False, "error": "a plugin update is already running"}
        info = await asyncio.to_thread(self._check_plugin_update_sync, True)
        if not info.get("ok"):
            return {"ok": False, "error": info.get("error", "update check failed")}
        if not info.get("update_available"):
            return {"ok": False, "error": "already up to date"}
        self._pu = {"running": True, "done": False, "ok": False, "error": "",
                    "version": info["latest"], "url": info["url"],
                    "downloaded": 0, "total": info.get("size", 0), "mode": "", "path": ""}
        self._pu_stop.clear()
        threading.Thread(target=self._plugin_update_run, daemon=True).start()
        return {"ok": True, "version": info["latest"]}

    async def cancel_plugin_update(self) -> dict:
        if not self._pu["running"]:
            return {"ok": False, "error": "no plugin update running"}
        self._pu_stop.set()
        return {"ok": True}

    async def get_plugin_update_status(self) -> dict:
        return dict(self._pu)

    def _plugin_update_run(self):
        st = self._pu
        try:
            plug = Path(os.environ.get("DECKY_PLUGIN_DIR", "")).resolve()
            if not plug.is_dir() or plug.name != PLUGIN_PACKAGE_NAME:
                st["error"] = "cannot locate the plugin directory for an in-place update"
                return
            req = urllib.request.Request(st["url"], headers={"User-Agent": "helixsr-decky-plugin"})
            with urllib.request.urlopen(req, timeout=120, context=_ssl_context()) as r:
                try:
                    st["total"] = int(r.headers.get("Content-Length") or 0)
                except (TypeError, ValueError):
                    st["total"] = 0
                buf = tempfile.SpooledTemporaryFile(max_size=32 << 20)
                while True:
                    if self._pu_stop.is_set():
                        st["cancelled"] = True
                        st["error"] = "update cancelled"
                        return
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    buf.write(chunk)
                    st["downloaded"] += len(chunk)
            buf.seek(0)
            with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
                shutil.copyfileobj(buf, tmp)
                tmp_path = Path(tmp.name)
            try:
                new_version = _install_plugin_zip(tmp_path, plug, _plugin_version())
            finally:
                tmp_path.unlink(missing_ok=True)
            st["version"] = new_version
            st["mode"] = "in-place"
            self._update_pending = new_version
        except Exception as e:  # noqa: BLE001
            st["error"] = f"{type(e).__name__}: {e}"
        else:
            st["ok"] = True
        finally:
            st["running"] = False
            st["done"] = True

    async def _swap_plugin_zip(self, tmp_path: Path, plug: Path) -> str:
        return await asyncio.to_thread(_install_plugin_zip, tmp_path, plug, _plugin_version())

    # -- build -------------------------------------------------------------------

    def _g3_pins(self, d: Path) -> dict:
        pins = {"PY_URL": G3_PY_URL, "PY_SHA": G3_PY_SHA,
                "DXCWIN_URL": G3_DXC_URL, "DXCWIN_SHA": G3_DXC_SHA}
        try:
            text = (d / "helixsr-setup.sh").read_text(encoding="utf-8", errors="replace")
        except OSError:
            return pins
        for name in list(pins):
            m = re.search(rf"^{name}='([^']+)'$", text, re.M)
            if m:
                pins[name] = m.group(1)
        return pins

    def _download_for_setup(self, url: str, dest: Path, sha: str, line, cancel) -> None:
        line(f"[helixsr] downloading {dest.name} from {url}")
        req = urllib.request.Request(url, headers={"User-Agent": "helixsr-decky-plugin"})
        try:
            r = urllib.request.urlopen(req, timeout=120, context=_ssl_context())
        except Exception as e:  # noqa: BLE001
            raise OSError(f"{dest.name}: {type(e).__name__}: {e}") from e
        try:
            total = int(r.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            total = 0
        got, last_pct = 0, -1
        try:
            with r, open(dest, "wb") as f:
                while True:
                    if cancel.is_set():
                        raise OSError("cancelled")
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
                    got += len(chunk)
                    pct = int(100 * got / total) if total else -1
                    if pct >= last_pct + 5:
                        last_pct = pct
                        if total:
                            line(f"[helixsr] downloading {dest.name} {got >> 20}/{total >> 20} MB ({pct}%)")
                        else:
                            line(f"[helixsr] downloading {dest.name} {got >> 20} MB ...")
        except OSError:
            raise
        except Exception as e:  # noqa: BLE001
            raise OSError(f"{dest.name}: {type(e).__name__}: {e}") from e
        if total and got != total:
            dest.unlink(missing_ok=True)
            raise OSError(f"incomplete {dest.name} download: got {got >> 20} of {total >> 20} MB")
        if _sha256_file(dest) != sha:
            dest.unlink(missing_ok=True)
            raise OSError(f"{dest.name}: checksum mismatch (not the expected file)")
        line(f"[helixsr] {dest.name} downloaded and verified ({got >> 20} MB) — {_mem_line()}")

    def _g3_data_dir(self) -> Path:
        home = Path(os.environ.get("DECKY_USER_HOME") or Path.home())
        return (Path(os.environ.get("XDG_DATA_HOME") or home / ".local" / "share") / "HelixSR")

    def _find_wine(self, home: Path) -> str:
        import glob as _glob
        for pat in ("./local/share/Steam/steamapps/common/Proton*/files/bin/wine",
                    ".steam/steam/steamapps/common/Proton*/files/bin/wine",
                    "./local/share/Steam/compatibilitytools.d/*/files/bin/wine",
                    ".steam/root/compatibilitytools.d/*/files/bin/wine"):
            for p in sorted(_glob.glob(str(home / pat))):
                if os.access(p, os.X_OK):
                    return p
        return shutil.which("wine") or ""

    def _has_numpy(self, py: str) -> bool:
        try:
            p = subprocess.run([py, "-c", "import numpy, sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)"],
                               capture_output=True, timeout=90)
            return p.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    def _ensure_g3_toolchain(self, d: Path, line, cancel) -> tuple[str, Path]:
        """A (python, dxc-wine shim) for running the release's setup: mirrors
        helixsr-setup.sh's own provisioning (portable/system Python with
        numpy, pinned Windows DXC unpacked and wrapped through Wine)."""
        data = self._g3_data_dir()
        data.mkdir(parents=True, exist_ok=True)
        pins = self._g3_pins(d)
        py = ""
        portable = data / "python" / "bin" / "python3"
        for c in ([str(portable)] if portable.is_file() else []) + ["python3"]:
            if self._has_numpy(c):
                py = c
                break
            line(f"[helixsr] {c}: no numpy, trying the next Python")
        if not py:
            line(f"[helixsr] using a portable Python in {data}/python (one-time download, ~67 MB)")
            tar = data / "python.tar.gz"
            try:
                self._download_for_setup(pins["PY_URL"], tar, pins["PY_SHA"], line, cancel)
                with tarfile.open(tar) as t:
                    t.extractall(data)
            finally:
                tar.unlink(missing_ok=True)
            p = subprocess.run([str(portable), "-m", "pip", "install", "--quiet",
                                "--disable-pip-version-check", "numpy"],
                               capture_output=True, timeout=600)
            if p.returncode != 0 or not self._has_numpy(str(portable)):
                raise OSError("numpy install into the portable Python failed")
            py = str(portable)
        line(f"[helixsr] python: {py}")
        dxcwin = data / "dxc-win"
        if not (dxcwin / "bin" / "x64" / "dxc.exe").is_file():
            z = data / "dxc-win.zip"
            try:
                self._download_for_setup(pins["DXCWIN_URL"], z, pins["DXCWIN_SHA"], line, cancel)
                with zipfile.ZipFile(z) as archive:
                    for info in archive.infolist():
                        t = info.filename.replace("\\", "/")
                        if t.startswith("bin/x64/") and not t.endswith("/"):
                            dest = dxcwin / t
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            with archive.open(info) as src, open(dest, "wb") as out:
                                shutil.copyfileobj(src, out)
            finally:
                z.unlink(missing_ok=True)
            if not (dxcwin / "bin" / "x64" / "dxc.exe").is_file():
                raise OSError("could not unpack the shader compiler")
        wine = self._find_wine(Path(os.environ.get("DECKY_USER_HOME") or Path.home()))
        if not wine:
            raise OSError("no Proton or Wine found (needed to run the shader compiler); "
                          "install Proton in Steam or Wine")
        shim = data / "dxc-wine.sh"
        shim.write_text(
            "#!/usr/bin/env bash\n"
            "# DXC through Wine: absolute Linux paths become Z:\\ paths\n"
            "args=()\n"
            "for a in \"$@\"; do case \"$a\" in /*) args+=(\"Z:$(printf '%s' \"$a\" | tr / '\\\\')\") ;; "
            "*) args+=(\"$a\") ;; esac; done\n"
            f"WINEPREFIX=\"{data}/wineprefix\" WINEDEBUG=-all exec \"{wine}\" "
            f"\"{dxcwin}/bin/x64/dxc.exe\" \"${{args[@]}}\"\n", encoding="utf-8")
        shim.chmod(0o755)
        line(f"[helixsr] shader compiler: Windows DXC through {wine}")
        return py, shim

    async def check_build_ready(self, tag: str | None = None, fg: bool = False) -> dict:
        d = _dir_for_tag(tag) if tag else await asyncio.to_thread(self._active_dir)
        if d is None:
            return {"ok": False, "error": "no HelixSR version downloaded", "checks": []}
        return await asyncio.to_thread(self._check_build_ready_run, d, bool(fg))

    def _check_build_ready_run(self, d: Path, fg: bool = False) -> dict:
        checks = []
        home = Path(os.environ.get("DECKY_USER_HOME") or Path.home())
        data = self._g3_data_dir()
        cands = []
        portable = data / "python" / "bin" / "python3"
        if portable.is_file():
            cands.append(str(portable))
        cands.append("python3")
        py, py_detail = "", ""
        for c in cands:
            try:
                p = subprocess.run([c, "-c", "import numpy, sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)"],
                                   capture_output=True, timeout=90)
                if p.returncode == 0:
                    py = c
                    break
                py_detail = f"{c}: no numpy"
            except (OSError, subprocess.SubprocessError):
                py_detail = f"{c}: not usable"
        if py:
            checks.append({"name": "Python 3 + numpy", "ok": True, "detail": py})
        else:
            # The plugin provisions the portable Python itself during the
            # build: not a gate, just a note.
            checks.append({"name": "Python 3 + numpy", "ok": True,
                           "detail": f"{py_detail or 'none found'} — a portable "
                                     "Python (~67 MB) will be downloaded during the build"})
        wine = self._find_wine(home)
        checks.append({"name": "Proton/Wine (shader compiler)", "ok": bool(wine),
                       "detail": wine or "install Proton in Steam or Wine"})
        free_rt = _free_bytes(_helixsr_root())
        free_data = _free_bytes(data if data.is_dir() else home)
        need_dlss = not (d / DLSS_FILE).is_file() or _sha256_file(d / DLSS_FILE) != DLSS_SHA256
        checks.append({"name": "Disk space",
                       "ok": free_rt > 500_000_000 and free_data > 500_000_000,
                       "detail": (f"runtime {free_rt / 1e9:.1f} GB free, setup data "
                                  f"{free_data / 1e9:.1f} GB free"
                                  + ("; ~56 MB DLSS download needed" if need_dlss
                                     else "; DLSS DLL already verified"))})
        try:
            st = bridge.status(d)
            checks.append({"name": "Network / frame generation", "ok": True,
                           "detail": f"network {'built' if st.get('network_ready') else 'not built'}, "
                                     f"FG {'built' if st.get('fg_ready') else 'not built'}"
                                     + (" (--no-fg: installs keep working without it)"
                                        if not fg else "")})
        except bridge.BridgeError as e:
            checks.append({"name": "Network / frame generation", "ok": False, "detail": str(e)})
        if fg:
            ok_mem, mem_detail = _fg_compile_fits()
            checks.append({"name": "Shader compile memory", "ok": ok_mem, "detail": mem_detail})
            have = [name for name, sha in ((DLSSG_FILE, DLSSG_SHA256), (DLSSG2_FILE, DLSSG2_SHA256))
                    if (d / name).is_file() and _sha256_file(d / name) == sha]
            checks.append({"name": "DLSS Frame Generation DLLs", "ok": True,
                           "detail": ("cached, verified" if len(have) == 2
                                      else "will download ~8 MB each of 2 on build "
                                           "(NVIDIA Streamline SDK)")})
            zstd = shutil.which("zstd") or ""
            py314 = ""
            for c in ([str(portable)] if portable.is_file() else []) + ["python3"]:
                try:
                    p = subprocess.run([c, "-c", "import sys; sys.exit(0 if sys.version_info >= (3, 14) else 1)"],
                                       capture_output=True, timeout=90)
                    if p.returncode == 0:
                        py314 = c
                        break
                except (OSError, subprocess.SubprocessError):
                    pass
            ngx_ok = bool(zstd or py314) and free_data > 1_000_000_000
            checks.append({"name": "NGX core prerequisites", "ok": ngx_ok,
                           "detail": (f"{zstd or py314 or 'no zstd/Python 3.14'}; "
                                      f"setup data {free_data / 1e9:.1f} GB free")})
        return {"ok": all(c["ok"] for c in checks), "checks": checks}

    async def start_build(self, rebuild: bool = False, fg: bool = False) -> dict:
        if self._dl["running"]:
            return {"ok": False, "error": "a download is running — build after it finishes"}
        if self._build_lock.locked():
            return {"ok": False, "error": "build already running"}
        d = await asyncio.to_thread(self._active_dir)
        if d is None:
            return {"ok": False, "error": "no HelixSR version downloaded"}
        if fg:
            ok_mem, mem_detail = _fg_compile_fits()
            if not ok_mem:
                return {"ok": False, "error": mem_detail}
        log = _helixsr_root() / f"build-{_tag_of_dir(d).lstrip('v') or 'x'}.log"
        self._build_log = log
        self._build_cancel.clear()
        threading.Thread(target=self._build_run, args=(d, log, bool(rebuild), bool(fg)),
                         daemon=True).start()
        return {"ok": True, "log": str(log)}

    async def cancel_build(self) -> dict:
        if not self._build_lock.locked():
            return {"ok": False, "error": "no build running"}
        proc, self._build_proc = self._build_proc, None
        if proc is not None and proc.returncode is None:
            try:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                except (ProcessLookupError, PermissionError, OSError):
                    proc.terminate()
            except ProcessLookupError:
                pass
        self._build_cancel.set()
        return {"ok": True}

    async def get_build_status(self) -> dict:
        log = self._build_log
        text = ""
        if log is not None and log.is_file():
            try:
                text = log.read_bytes()[-60000:].decode("utf-8", errors="replace")
            except OSError:
                text = ""
        running = self._build_lock.locked()
        network_built, fg_built = False, False
        d = await asyncio.to_thread(self._active_dir)
        if d is not None:
            try:
                st = await asyncio.to_thread(bridge.status, d)
                network_built = bool(st.get("network_ready"))
                fg_built = bool(st.get("fg_ready"))
            except bridge.BridgeError:
                pass
        return {"running": running, "log": text,
                "log_path": str(log) if log else "",
                "network_built": network_built, "fg_built": fg_built}

    def _build_log_line(self, log: Path, msg: str):
        try:
            with open(log, "a", encoding="utf-8", errors="replace") as f:
                f.write(msg.rstrip("\n") + "\n")
        except OSError:
            decky.logger.warning(f"HelixSR build log unwritable: {msg[:200]}")

    def _build_run(self, d: Path, log: Path, rebuild: bool, fg: bool):
        if not self._build_lock.acquire(blocking=False):
            return
        line = lambda msg: self._build_log_line(log, msg)  # noqa: E731
        try:
            if not self._alive:
                return
            line(f"[helixsr] build start — {_mem_line()}")
            with open(log, "w", encoding="utf-8") as f:
                f.write("")
            if fg:
                ok_mem, mem_detail = _fg_compile_fits()
                if not ok_mem:
                    line(f"[helixsr] refused: {mem_detail}")
                    try:
                        asyncio.run(decky.emit("helixsr_build_done", False, -1))
                    except Exception:  # noqa: BLE001
                        pass
                    return
            # Phase 1: toolchain + NVIDIA DLLs (plugin's own TLS, verified).
            try:
                py, shim = self._ensure_g3_toolchain(d, line, self._build_cancel)
            except OSError as e:
                line(f"[helixsr] toolchain failed: {e}")
                try:
                    asyncio.run(decky.emit("helixsr_build_done", False, -1))
                except Exception:  # noqa: BLE001
                    pass
                return
            if not self._alive or self._build_cancel.is_set():
                line("[helixsr] build cancelled")
                return
            _repair_exec_bits(d)
            args = [py, "-I", str(d / "setup" / "helixsr_setup.py"), str(d / "HelixSR-fsrfg"),
                    "--dxc", str(shim), "--yes"]
            if not fg:
                args.append("--no-fg")
            if rebuild:
                pass  # the setup rebuilds when asked through --fg-only or a missing network
            else:
                try:
                    st = bridge.status(d)
                    if st.get("network_ready") and (not fg or st.get("fg_ready")):
                        line("[helixsr] already built (use Rebuild to build again)")
                        try:
                            asyncio.run(decky.emit("helixsr_build_done", True, 0))
                        except Exception:  # noqa: BLE001
                            pass
                        return
                    if st.get("network_ready") and fg and not st.get("fg_ready"):
                        args.append("--fg-only")
                except bridge.BridgeError:
                    pass
            for name, sha, url in ((DLSS_FILE, DLSS_SHA256, DLSS_URL),
                                  (DLSSG_FILE, DLSSG_SHA256, DLSSG_SL_URL),
                                  (DLSSG2_FILE, DLSSG2_SHA256, DLSSG2_SL_URL)):
                dest = d / name
                try:
                    if dest.is_file() and _sha256_file(dest) == sha:
                        line(f"[helixsr] {name}: cached, verified")
                        args += ["--dlss" if name == DLSS_FILE else "--dlssg", str(dest)]
                        continue
                    if name == DLSS_FILE or fg:
                        dl_tmp = d / f".{name}.dl"
                        self._download_for_setup(url, dl_tmp, sha, line, self._build_cancel)
                        dl_tmp.rename(dest)
                        args += ["--dlss" if name == DLSS_FILE else "--dlssg", str(dest)]
                except OSError as e:
                    line(f"[helixsr] {name}: {e}")
                    if name == DLSS_FILE:
                        try:
                            asyncio.run(decky.emit("helixsr_build_done", False, -1))
                        except Exception:  # noqa: BLE001
                            pass
                        return
            if not self._alive or self._build_cancel.is_set():
                line("[helixsr] build cancelled")
                return
            # Phase 2: the release's own setup, watched (its log streams to ours).
            header = f"HelixSR {bridge.dll_version(bridge.release_dll(d))} build"
            asyncio.run(self._spawn_and_watch(d, log, header, args))
        finally:
            try:
                (_helixsr_root() / BUILD_PID_FILE).unlink(missing_ok=True)
            except OSError:
                pass
            if self._build_lock.locked():
                try:
                    self._build_lock.release()
                except RuntimeError:
                    pass

    async def _spawn_and_watch(self, d: Path, log: Path, header: str, args: list):
        env = _compile_jobs_env()
        if env:
            self._build_log_line(log, f"[helixsr] capping shader-compile workers at "
                                      f"{env['HELIXSR_SETUP_JOBS']} (HELIXSR_SETUP_JOBS, Steam UI headroom)")
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=open(log, "a"), stderr=subprocess.STDOUT,
                env={**os.environ, **env},
                cwd=str(d),
                start_new_session=True)
        except Exception as e:  # noqa: BLE001
            self._build_log_line(log, f"[helixsr] could not start build: {type(e).__name__}: {e}")
            try:
                await decky.emit("helixsr_build_done", False, -1)
            except Exception:  # noqa: BLE001
                pass
            return
        self._build_proc = proc
        try:
            (_helixsr_root() / BUILD_PID_FILE).write_text(str(proc.pid))
        except OSError:
            pass

        async def mem_watch():
            while True:
                await asyncio.sleep(30)
                try:
                    self._build_log_line(log, f"[helixsr] mem: {_mem_line()}")
                except OSError:
                    pass

        mem_task = asyncio.create_task(mem_watch())
        try:
            try:
                await asyncio.wait_for(proc.wait(), timeout=BUILD_TIMEOUT)
            except asyncio.TimeoutError:
                self._build_log_line(log, f"[helixsr] build timed out after {BUILD_TIMEOUT // 3600}h and was stopped")
                try:
                    proc.terminate()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(proc.wait(), timeout=60)
                except asyncio.TimeoutError:
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass
                    await proc.wait()
            rc = proc.returncode
            # Exit 3 = the upscaler built, frame generation did not (retry it
            # with an FG build); still a usable result.
            ok = rc in (0, 3)
            if rc == 3:
                self._build_log_line(log, "[helixsr] network built, frame generation was not "
                                          "(run an FG build to retry it)")
            elif rc != 0:
                self._build_log_line(log, f"[helixsr] setup exited rc={rc}")
            self._build_log_line(log, f"[helixsr] build {'done' if ok else 'failed'} rc={rc}")
            try:
                await decky.emit("helixsr_build_done", ok, rc if rc is not None else -1)
            except Exception:  # noqa: BLE001
                pass
        except Exception as e:  # noqa: BLE001
            self._build_log_line(log, f"\n[helixsr] build watch failed: {e}")
            try:
                await decky.emit("helixsr_build_done", False, -1)
            except Exception:  # noqa: BLE001
                pass
        finally:
            mem_task.cancel()
            self._build_proc = None

    # -- scan ---------------------------------------------------------------------

    async def start_scan(self) -> dict:
        if self._scan["running"]:
            return {"ok": False, "error": "a scan is already running"}
        d = await asyncio.to_thread(self._active_dir)
        if d is None:
            return {"ok": False, "error": "no HelixSR version downloaded"}
        self._scan = _new_scan_state()
        self._scan["running"] = True
        self._scan["tag"] = _tag_of_dir(d)
        self._scan_stop.clear()
        threading.Thread(target=self._scan_run, args=(d, self._extra_folders()), daemon=True).start()
        return {"ok": True}

    async def cancel_scan(self) -> dict:
        if not self._scan["running"]:
            return {"ok": False, "error": "no scan running"}
        self._scan_stop.set()
        return {"ok": True}

    async def get_scan_status(self) -> dict:
        return dict(self._scan)

    async def get_last_scan(self) -> dict:
        cache = self._scan_cache
        if cache["time"] and time.time() - cache["time"] < SCAN_CACHE_TTL and cache["games"]:
            return {"ok": True, "games": cache["games"], "tag": cache["tag"],
                    "skipped": 0, "scanned_at": cache["time"]}
        return {"ok": True, "games": [], "tag": "", "skipped": 0, "scanned_at": 0}

    async def add_scan_folder(self, path: str) -> dict:
        cleaned = bridge_clean(str(path or ""))
        d = Path(cleaned).expanduser()
        if not d.is_dir():
            return {"ok": False, "error": f"not a folder: {cleaned}"}
        try:
            key = str(d.resolve())
        except OSError:
            return {"ok": False, "error": f"cannot resolve: {cleaned}"}
        folders = [f for f in self._extra_folders() if f != key]
        folders.append(key)
        self.settings.setSetting("extra_folders", folders)
        self.settings.commit()
        return {"ok": True, "folders": folders}

    async def remove_scan_folder(self, path: str) -> dict:
        folders = [f for f in self._extra_folders() if f != str(path or "")]
        self.settings.setSetting("extra_folders", folders)
        self.settings.commit()
        return {"ok": True, "folders": folders}

    def _scan_games_run(self, d: Path, extra: list) -> list:
        out = bridge.games(d, extra)
        games = out.get("games", [])
        for i, g in enumerate(games, 1):
            g["index"] = i
        return games

    def _scan_run(self, d: Path, extra: list):
        st = self._scan
        try:
            with self._scan_lock:
                games = self._scan_games_run(d, extra)
                if self._scan_stop.is_set():
                    st["cancelled"] = True
                    st["error"] = "scan cancelled"
                    return
                st["games"] = games
                self._scan_cache = {"dir": str(d), "games": copy.deepcopy(games),
                                    "tag": _tag_of_dir(d), "extra": list(extra),
                                    "time": time.time()}
        except bridge.BridgeError as e:
            st["error"] = str(e)
        except Exception as e:  # noqa: BLE001
            st["error"] = f"{type(e).__name__}: {e}"
        else:
            st["ok"] = True
        finally:
            st["running"] = False
            st["done"] = True

    # -- games: install / remove / repair / verify -----------------------------

    async def install_games(self, items: list) -> dict:
        """Install or update HelixSR in games, one bridge call each.

        items: [{folder, way ("auto"|"scaler"|"fsr"), fg (bool, scaler only),
                 anticheat_ok (bool)}]. Returns per-game results."""
        d = await asyncio.to_thread(self._active_dir)
        if d is None:
            return {"ok": False, "error": "no HelixSR version downloaded"}
        if not isinstance(items, list) or not items:
            return {"ok": False, "error": "no games given"}
        pkg, sver = await asyncio.to_thread(bridge.bundled_scaler_version, d)
        return await asyncio.to_thread(self._install_run, d, items, pkg, sver)

    def _install_run(self, d: Path, items: list, pkg, sver) -> dict:
        results = []
        for it in items:
            folder = str((it or {}).get("folder", ""))
            way = str((it or {}).get("way", "auto") or "auto")
            if not folder:
                results.append({"folder": folder, "ok": False, "detail": "no folder given"})
                continue
            args: dict = {"game": folder, "way": way}
            if way in ("auto", "scaler"):
                if pkg is None:
                    results.append({"folder": folder, "ok": False,
                                    "detail": "this release zip has no helixsr-scaler folder: re-download it"})
                    continue
                args["scaler"] = str(pkg)
                if sver:
                    args["scaler-version"] = sver
                if (it or {}).get("fg") is True:
                    args["fg"] = "on"
                elif (it or {}).get("fg") is False:
                    args["fg"] = "off"
                if (it or {}).get("anticheat_ok"):
                    args["anticheat-ok"] = "yes"
            try:
                out = bridge.run(d, "install", args, timeout=600)
                game = out.get("game", {})
                results.append({"folder": folder, "ok": True,
                                "detail": "\n".join(out.get("messages", [])).strip(),
                                "installed": game.get("installed"),
                                "version": game.get("version")})
            except bridge.BridgeError as e:
                results.append({"folder": folder, "ok": False, "detail": str(e)})
        ok = all(r["ok"] for r in results)
        return {"ok": ok, "results": results}

    async def uninstall_games(self, folders: list) -> dict:
        d = await asyncio.to_thread(self._active_dir)
        if d is None:
            return {"ok": False, "error": "no HelixSR version downloaded"}
        return await asyncio.to_thread(self._remove_run, d, folders or [])

    def _remove_run(self, d: Path, folders: list) -> dict:
        results = []
        for folder in folders:
            try:
                out = bridge.run(d, "remove", {"game": str(folder)}, timeout=300)
                results.append({"folder": str(folder), "ok": True,
                                "detail": "\n".join(out.get("messages", [])).strip()})
            except bridge.BridgeError as e:
                results.append({"folder": str(folder), "ok": False, "detail": str(e)})
        return {"ok": all(r["ok"] for r in results), "results": results}

    async def repair_games(self, folders: list) -> dict:
        """Remove + reinstall each game with the way it has now."""
        d = await asyncio.to_thread(self._active_dir)
        if d is None:
            return {"ok": False, "error": "no HelixSR version downloaded"}
        return await asyncio.to_thread(self._repair_run, d, folders or [])

    def _repair_run(self, d: Path, folders: list) -> dict:
        pkg, sver = bridge.bundled_scaler_version(d)
        results = []
        for folder in folders:
            folder = str(folder)
            try:
                games = self._scan_games_run(d, self._extra_folders())
                cur = next((g for g in games if g["folder"] == folder), None)
                way = (cur or {}).get("installed") or "auto"
            except bridge.BridgeError as e:
                results.append({"folder": folder, "ok": False, "detail": str(e)})
                continue
            try:
                bridge.run(d, "remove", {"game": folder}, timeout=300)
            except bridge.BridgeError as e:
                results.append({"folder": folder, "ok": False,
                                "detail": f"remove failed: {e}"})
                continue
            args: dict = {"game": folder, "way": way if way in ("scaler", "fsr") else "auto"}
            if args["way"] == "scaler" and pkg is not None:
                args["scaler"] = str(pkg)
                if sver:
                    args["scaler-version"] = sver
            try:
                out = bridge.run(d, "install", args, timeout=600)
                results.append({"folder": folder, "ok": True,
                                "detail": "\n".join(out.get("messages", [])).strip()})
            except bridge.BridgeError as e:
                results.append({"folder": folder, "ok": False, "detail": str(e)})
        return {"ok": all(r["ok"] for r in results), "results": results}

    async def verify_installs(self) -> dict:
        d = await asyncio.to_thread(self._active_dir)
        if d is None:
            return {"ok": False, "error": "no HelixSR version downloaded"}
        return await asyncio.to_thread(self._verify_run, d)

    def _verify_run(self, d: Path) -> dict:
        try:
            games = self._scan_games_run(d, self._extra_folders())
        except bridge.BridgeError as e:
            return {"ok": False, "error": str(e)}
        results = []
        for g in games:
            way = g.get("installed")
            if not way:
                continue
            problems = self._verify_game(g)
            results.append({"folder": g["folder"], "name": g["name"], "route": way,
                            "installed": True, "ok": not problems, "problems": problems,
                            "version": g.get("version")})
        return {"ok": True, "games": results}

    def _verify_game(self, g: dict) -> list:
        """File-level checks from the scan record (markers + files the way
        must have left in the game)."""
        problems = []
        way = g.get("installed")
        target = Path(g["exe_folder"]) if g.get("exe_folder") else None
        if way == "scaler":
            if target is None:
                return ["game folder not found anymore"]
            loader = g.get("scaler_dll") or "dxgi.dll"
            if not (target / loader).is_file():
                problems.append(f"{loader} missing next to the game's exe")
            if not (target / "HelixSR-fsrfg" / "amd_fidelityfx_dx12.dll").is_file():
                problems.append("HelixSR DLL missing (HelixSR-fsrfg/amd_fidelityfx_dx12.dll)")
            if not (target / "OptiScaler.ini").is_file():
                problems.append("OptiScaler.ini missing next to the game's exe")
            for miss in g.get("scaler_missing") or []:
                problems.append(f"missing: {miss}")
            rec = _read_marker(target / "HelixSR-Scaler.files")
            if not rec:
                problems.append("install record missing (HelixSR-Scaler.files)")
        elif way == "fsr":
            spath = g.get("settings_path")
            folder = Path(spath).parent if spath else None
            if folder is None or not folder.is_dir():
                problems.append("settings folder not found anymore")
            else:
                rec = _read_marker(folder / "helixsr-install.json")
                if not rec.get("helixsr"):
                    problems.append("install record missing (helixsr-install.json)")
                if not any(folder.glob("*.original.dll")) and not (folder / "helixsr.log").is_file():
                    problems.append("no backup or log next to the install (game may have updated)")
        else:
            problems.append(f"earlier-version install ({way}): remove and reinstall it")
        return problems

    async def set_scaler_fg(self, folder: str, on: bool) -> dict:
        d = await asyncio.to_thread(self._active_dir)
        if d is None:
            return {"ok": False, "error": "no HelixSR version downloaded"}
        try:
            out = await asyncio.to_thread(bridge.run, d, "scaler-fg",
                                          {"game": str(folder), "on": "true" if on else "false"},
                                          300)
        except bridge.BridgeError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "game": out.get("game"),
                "output": "\n".join(out.get("messages", [])).strip()}

    async def rollback_game(self, folder: str) -> dict:
        d = await asyncio.to_thread(self._active_dir)
        if d is None:
            return {"ok": False, "error": "no HelixSR version downloaded"}
        try:
            out = await asyncio.to_thread(bridge.run, d, "rollback", {"game": str(folder)}, 300)
        except bridge.BridgeError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "game": out.get("game"),
                "output": "\n".join(out.get("messages", [])).strip()}

    # -- settings --------------------------------------------------------------

    def _settings_schema(self, values: dict, kind: str) -> tuple[dict, dict]:
        allowed = SCALER_SETTINGS if kind == "scaler" else HELIXSR_SETTINGS
        defaults = dict(SCALER_DEFAULTS if kind == "scaler" else HELIXSR_DEFAULTS)
        for key in allowed:
            if key in values and isinstance(values[key], str):
                v = values[key].strip()
                alias = (SCALER_ALIASES.get(key, {}) if kind == "scaler" else {}).get(v.lower())
                values[key] = alias or v
        return allowed, defaults

    async def get_game_settings(self, folder: str) -> dict:
        d = await asyncio.to_thread(self._active_dir)
        if d is None:
            return {"ok": False, "error": "no HelixSR version downloaded"}
        try:
            out = await asyncio.to_thread(bridge.run, d, "settings-get",
                                          {"game": str(folder)}, 120)
        except bridge.BridgeError as e:
            return {"ok": False, "error": str(e)}
        kind = out.get("kind", "helixsr")
        vals = dict(out.get("values") or {})
        _allowed, defaults = self._settings_schema(vals, kind)
        for key, default in defaults.items():
            vals.setdefault(key, default)
        schema = "scaler" if kind == "scaler" else "helixsr"
        return {"ok": True, "path": out.get("path"), "values": vals,
                "schema": schema, "other": out.get("other") or {}}

    async def set_game_settings(self, folder: str, values: dict) -> dict:
        d = await asyncio.to_thread(self._active_dir)
        if d is None:
            return {"ok": False, "error": "no HelixSR version downloaded"}
        if not isinstance(values, dict) or not values:
            return {"ok": False, "error": "no settings given"}
        try:
            out = await asyncio.to_thread(bridge.run, d, "settings-set",
                                          {"game": str(folder), "values": json.dumps(values)}, 120)
        except bridge.BridgeError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "path": out.get("path"),
                "output": "\n".join(out.get("messages", [])).strip()}

    async def reset_game_settings(self, folder: str) -> dict:
        d = await asyncio.to_thread(self._active_dir)
        if d is None:
            return {"ok": False, "error": "no HelixSR version downloaded"}
        pkg, _sver = await asyncio.to_thread(bridge.bundled_scaler_version, d)
        args: dict = {"game": str(folder)}
        if pkg is not None:
            args["scaler"] = str(pkg)
        try:
            out = await asyncio.to_thread(bridge.run, d, "reset-settings", args, 120)
        except bridge.BridgeError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "game": out.get("game"),
                "output": "\n".join(out.get("messages", [])).strip()}

    # -- ini presets (plugin-side, stored as {Section.Key: value}) --------------

    def _presets(self) -> dict:
        try:
            presets = self.settings.getSetting("ini_presets", {})
            return dict(presets) if isinstance(presets, dict) else {}
        except Exception:  # noqa: BLE001
            return {}

    async def get_ini_presets(self) -> dict:
        return {"ok": True, "presets": self._presets(),
                "defaults_helixsr": dict(HELIXSR_DEFAULTS),
                "defaults_scaler": dict(SCALER_DEFAULTS)}

    async def save_ini_preset(self, name: str, values: dict) -> dict:
        name = str(name or "").strip()
        if not name:
            return {"ok": False, "error": "no preset name given"}
        if not isinstance(values, dict) or not values:
            return {"ok": False, "error": "no settings given"}
        presets = self._presets()
        if len(presets) >= MAX_INI_PRESETS and name not in presets:
            return {"ok": False, "error": f"too many presets (max {MAX_INI_PRESETS})"}
        overwrote = name in presets
        presets[name] = {str(k): (None if v is None else str(v)) for k, v in values.items()}
        self.settings.setSetting("ini_presets", presets)
        self.settings.commit()
        return {"ok": True, "presets": presets, "overwrote": overwrote}

    async def delete_ini_preset(self, name: str) -> dict:
        presets = self._presets()
        presets.pop(str(name or ""), None)
        self.settings.setSetting("ini_presets", presets)
        self.settings.commit()
        return {"ok": True, "presets": presets}

    async def apply_ini_preset(self, folder: str, name: str) -> dict:
        presets = self._presets()
        if str(name) == BUILTIN_DEFAULT_PRESET:
            return await self.reset_game_settings(folder)
        values = presets.get(str(name or ""))
        if not values:
            return {"ok": False, "error": f"unknown preset: {name}"}
        return await self.set_game_settings(folder, dict(values))

    # -- logs ---------------------------------------------------------------------

    LOG_FIXES = [
        {"id": "wave64", "match": "wave32",
         "label": "32-lane shaders detected — set WaveSize 64 (RX 7000/9000)",
         "settings": {"Compatibility.WaveSize": "64"}},
    ]

    async def get_game_log(self, folder: str) -> dict:
        d = await asyncio.to_thread(self._active_dir)
        if d is None:
            return {"ok": False, "error": "no HelixSR version downloaded"}
        try:
            out = await asyncio.to_thread(bridge.run, d, "log", {"game": str(folder)}, 120)
        except bridge.BridgeError as e:
            return {"ok": False, "error": str(e)}
        files = out.get("files") or []
        blob_parts = []
        for f in files:
            if not isinstance(f, dict):
                continue
            tail = f.get("tail", "")
            blob_parts.append("\n".join(tail) if isinstance(tail, list) else str(tail))
        blob = "\n".join(blob_parts).lower()
        fixes = [{"id": f["id"], "label": f["label"]} for f in self.LOG_FIXES
                 if f["match"].lower() in blob]
        return {"ok": True, "files": files, "important": out.get("important") or [],
                "report": out.get("report") or "", "fixes": fixes}

    async def apply_log_fix(self, folder: str, fix_id: str) -> dict:
        fix = next((f for f in self.LOG_FIXES if f["id"] == str(fix_id)), None)
        if fix is None:
            return {"ok": False, "output": f"unknown fix: {fix_id}"}
        cur = await self.get_game_log(folder)
        if not cur.get("ok"):
            return {"ok": False, "output": cur.get("error", "cannot read log")}
        if str(fix_id) not in [f["id"] for f in cur.get("fixes", [])]:
            return {"ok": False, "output": "pattern no longer present in the log"}
        return await self.set_game_settings(folder, dict(fix["settings"]))

    # -- health ---------------------------------------------------------------------

    async def health_check(self) -> dict:
        d = await asyncio.to_thread(self._active_dir)
        extra = self._extra_folders()
        return await asyncio.to_thread(self._health_run, d, extra)

    def _health_run(self, d: Path | None, extra: list) -> dict:
        import socket
        checks = []
        try:
            socket.create_connection(("api.github.com", 443), timeout=5).close()
            checks.append({"name": "GitHub reachable", "ok": True,
                           "detail": "api.github.com:443 connects"})
        except OSError as e:
            checks.append({"name": "GitHub reachable", "ok": False,
                           "detail": f"api.github.com:443 failed: {e}"})
        versions = _version_dirs()
        if not versions:
            checks.append({"name": "Versions", "ok": False, "detail": "nothing downloaded yet"})
        else:
            bad = []
            for v in versions:
                try:
                    st = bridge.status(v)
                except bridge.BridgeError as e:
                    bad.append(f"{_tag_of_dir(v)}: {e}")
                    continue
                if not st.get("network_ready"):
                    bad.append(f"{_tag_of_dir(v)}: network not built")
            checks.append({"name": "Versions", "ok": not bad,
                           "detail": f"{len(versions)} downloaded, all healthy" if not bad
                                     else "; ".join(bad)})
        if d is None:
            checks.append({"name": "Games", "ok": False, "detail": "no version downloaded"})
        else:
            try:
                games = self._scan_games_run(d, extra)
                installed = [g for g in games if g.get("installed")]
                broken = [g["name"] for g in installed if self._verify_game(g)]
                checks.append({"name": "Games", "ok": not broken,
                               "detail": (f"{len(installed)} installed, all verified"
                                          if installed and not broken else
                                          ("none installed" if not installed else
                                           f"broken: {', '.join(broken)}"))})
            except bridge.BridgeError as e:
                checks.append({"name": "Games", "ok": False, "detail": f"scan failed: {e}"})
        free_rt = _free_bytes(_helixsr_root())
        home = Path(os.environ.get("DECKY_USER_HOME") or Path.home())
        checks.append({"name": "Disk space",
                       "ok": free_rt > 500_000_000,
                       "detail": f"runtime {free_rt / 1e9:.1f} GB free"})
        try:
            info = self._check_plugin_update_sync(False)
        except Exception:  # noqa: BLE001
            info = {"ok": False, "error": "check failed"}
        if not info.get("ok"):
            checks.append({"name": "Plugin update", "ok": False,
                           "detail": info.get("error", "check failed")})
        elif info.get("update_available"):
            checks.append({"name": "Plugin update", "ok": True,
                           "detail": f"{info.get('latest')} available (you run {info.get('current')})"})
        else:
            checks.append({"name": "Plugin update", "ok": True,
                           "detail": f"v{info.get('current')} up to date"})
        return {"ok": all(c["ok"] for c in checks), "checks": checks}


def _read_marker(p: Path) -> dict:
    try:
        rec = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return rec if isinstance(rec, dict) else {}


def bridge_clean(f: str) -> str:
    """A path as typed or pasted: quotes, file:// URLs, a file inside the
    game folder (its folder is used). Mirrors the bridge's clean_path."""
    from urllib.parse import unquote
    f = f.strip().strip('"').strip("'").strip()
    if f.lower().startswith("file://"):
        f = unquote(f[7:])
    if os.name != "nt":
        f = re.sub(r"\\(.)", r"\1", f)
    p = Path(f).expanduser()
    return str(p.parent if p.is_file() else p)


def _plugin_version() -> str:
    try:
        base = os.environ.get("DECKY_PLUGIN_DIR", "")
        if base:
            import json as _json
            return _json.loads(Path(base, "package.json").read_text()).get("version", "?")
    except (OSError, ValueError):
        pass
    return "?"


def _install_plugin_zip(zip_path: Path, plug: Path, current: str) -> str:
    plug = plug.resolve()
    stage = plug / ".update-stage"
    shutil.rmtree(stage, ignore_errors=True)
    try:
        with zipfile.ZipFile(zip_path) as z:
            names = [i.filename for i in z.infolist()
                     if not i.filename.startswith("/") and ".." not in Path(i.filename).parts]
            tops = {n.split("/")[0] for n in names if "/" in n}
            if len(tops) != 1:
                raise OSError("unexpected plugin zip layout")
            top = tops.pop()
            for info in z.infolist():
                if info.filename.startswith("/") or ".." in Path(info.filename).parts:
                    continue
                z.extract(info, stage)
            meta = json.loads((stage / top / "plugin.json").read_text(encoding="utf-8"))
            pkg = json.loads((stage / top / "package.json").read_text(encoding="utf-8"))
        if meta.get("name") != PLUGIN_DISPLAY_NAME:
            raise OSError(f"zip is not {PLUGIN_DISPLAY_NAME}")
        new_version = str(pkg.get("version") or "")
        if not new_version or _compare_versions(new_version, current) <= 0:
            raise OSError(f"zip version {new_version or '?'} is not newer than {current}")
        if not (stage / top / "dist" / "index.js").is_file() or not (stage / top / "main.py").is_file():
            raise OSError("zip is missing dist/index.js or main.py")
        bak = plug.parent / (plug.name + ".update-bak")
        shutil.rmtree(bak, ignore_errors=True)
        os.rename(plug, bak)
        staged_top = bak / stage.name / top
        try:
            os.rename(staged_top, plug)
        except Exception:
            try:
                os.rename(bak, plug)
            except OSError:
                pass
            raise
        shutil.rmtree(bak, ignore_errors=True)
        return new_version
    finally:
        shutil.rmtree(stage, ignore_errors=True)
