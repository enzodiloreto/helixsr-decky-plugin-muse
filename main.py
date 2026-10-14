import os
import asyncio
import configparser
import copy
import json
import re
import shutil
import ssl
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

# The decky plugin module is located at decky-loader/plugin
# For easy intellisense checkout the decky-loader code repo
# and add the `decky-loader/plugin/imports` path to `python.analysis.extraPaths` in `.vscode/settings.json`
import decky
import helixscan
from settings import SettingsManager

GITHUB_OWNER = "lonewolf0622"
GITHUB_REPO = "HelixSR"
RELEASES_URL = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases?per_page=30"


def _helixsr_root() -> Path:
    return Path(os.environ.get("DECKY_PLUGIN_RUNTIME_DIR", tempfile.gettempdir())) / "helixsr"


def _fetch_json(url: str, timeout: int = 30):
    req = urllib.request.Request(url, headers={"User-Agent": "helixsr-decky-plugin", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as r:
        return json.loads(r.read().decode("utf-8"))


_SSL_CONTEXT: ssl.SSLContext | None = None


def _ssl_context() -> ssl.SSLContext:
    """TLS context using the CA bundle shipped with the plugin.

    The Python running Decky plugins on SteamOS may not find any system
    CA certificates, which makes every HTTPS call fail with
    CERTIFICATE_VERIFY_FAILED. The vendored Mozilla bundle fixes that.
    """
    global _SSL_CONTEXT
    if _SSL_CONTEXT is None:
        bundle = Path(__file__).parent / "py_modules" / "cacert.pem"
        if bundle.is_file():
            _SSL_CONTEXT = ssl.create_default_context(cafile=str(bundle))
        else:
            _SSL_CONTEXT = ssl.create_default_context()
    return _SSL_CONTEXT


def _version_dirs() -> list[Path]:
    root = _helixsr_root()
    if not root.is_dir():
        return []
    # Each download extracts to <root>/HelixSR-<ver>/ (zip top-level folder)
    return sorted([p for p in root.iterdir() if p.is_dir() and (p / "helixsr-setup.sh").exists()])


def _tag_of_dir(d: Path) -> str:
    name = d.name
    # Folder is HelixSR-1.8.1 -> tag v1.8.1
    m = re.search(r"(\d+\.\d+\.\d+)", name)
    return f"v{m.group(1)}" if m else name


def _dir_for_tag(tag: str) -> Path | None:
    want = tag.lstrip("v")
    for d in _version_dirs():
        if want in d.name:
            return d
    return None


# NVIDIA's DLSS SDK DLL that HelixSR builds its network from, verified by hash.
# Same file and hash as the reference setup/helixsr_setup.py downloads
# (Apache-2.0, (c) lonewolf0622). The plugin fetches it with its own TLS
# stack because the system Python the setup picks may lack SSL support
# ("unknown url type: https"), then hands it to the setup via --dlss.
DLSS_URL = 'https://raw.githubusercontent.com/NVIDIA/DLSS/v310.7.0/lib/Windows_x86_64/rel/nvngx_dlss.dll'
DLSS_SHA256 = 'be6e434a94ca32499515eb62ca0e6c274526055d568d0426e4c652dcdfb6ee6e'
DLSS_LICENSE = 'https://github.com/NVIDIA/DLSS/blob/v310.7.0/LICENSE.txt'
DLSS_FILE = 'nvngx_dlss.dll'


def _sha256_file(p: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# Executables a HelixSR release needs (+x): ZipFile extraction drops stored
# modes, so these are (re-)applied after download and repaired at build start.
EXEC_FILES = ("helixsr-setup.sh", "helixsr-uninstall.sh", "setup/lib/model/launch_synth")


def _restore_zip_modes(z: zipfile.ZipFile, root: Path):
    """Re-apply the Unix modes stored in the zip (extractall ignores them)."""
    for info in z.infolist():
        name = info.filename
        if name.startswith("/") or ".." in Path(name).parts:
            continue
        dest = root / name
        mode = (info.external_attr >> 16) & 0o777
        if mode and dest.is_file():
            try:
                dest.chmod(mode)
            except OSError:
                pass


def _repair_exec_bits(d: Path) -> list[str]:
    """Ensure the release's executables are executable (heals installs
    extracted before _restore_zip_modes existed). Returns what was fixed."""
    fixed = []
    for rel in EXEC_FILES:
        p = d / rel
        try:
            if p.is_file() and not os.access(p, os.X_OK):
                p.chmod(0o755)
                fixed.append(rel)
        except OSError:
            pass
    return fixed


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


def _status_for(route, inputs):
    """Status text mirroring helixscan.Game.label (kept in sync by hand)."""
    if route == "dlss":
        return "DLSS (HelixSR as the game's DLSS, experimental: select DLSS in the game)"
    if route == "direct":
        return "FSR 3.1 (HelixSR replaces the game's FSR DLL: select AMD FSR in the game)"
    if route == "optiscaler":
        return " / ".join(inputs) + " (through OptiScaler: select DLSS in the game)"
    return route or "compatible"


def _marker_versions(g) -> set:
    """HelixSR versions recorded in a game's install markers (per route)."""
    out = set()
    paths = []
    if g.target is not None:
        paths.append(g.target / helixscan.FOLDER / helixscan.MARKER)
    for t in g.fsr_targets:
        paths.append(t.parent / helixscan.MARKER)
    for p in paths:
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(rec, dict) and rec.get("helixsr"):
            out.add(str(rec["helixsr"]))
    return out


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


def _plugin_version() -> str:
    try:
        base = os.environ.get("DECKY_PLUGIN_DIR", "")
        if base:
            import json as _json
            return _json.loads(Path(base, "package.json").read_text()).get("version", "?")
    except (OSError, ValueError):
        pass
    return "?"


def _new_dl_state(tag: str = "") -> dict:
    return {"running": False, "done": False, "ok": False, "error": "",
            "tag": tag, "downloaded": 0, "total": 0,
            "dir": "", "network_built": False}


def _zip_top(z: zipfile.ZipFile) -> str:
    """The release's top-level folder: the single directory all files share.
    Falls back to the legacy first-entry assumption for odd layouts."""
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


def _new_scan_state() -> dict:
    return {"running": False, "done": False, "ok": False, "cancelled": False, "error": "",
            "games": [], "tag": "", "skipped": 0}


def _empty_scan_cache() -> dict:
    return {"dir": "", "games": [], "tag": "", "extra": [], "time": 0.0}


# Reuse a scan for install/uninstall while it is fresh (a full library walk
# takes minutes on big collections; game folders rarely change).
SCAN_CACHE_TTL = 300

# Give up on a hung shader compile eventually instead of polling forever.
BUILD_TIMEOUT = 4 * 3600


# helixsr.ini settings the plugin edits (template key -> section).
INI_SECTIONS = {"Sharpener": "Sharpening", "Sharpness": "Sharpening",
                "UseGameSlider": "Sharpening", "WaveSize": "Compatibility"}
INI_DEFAULTS = {"Sharpener": "off", "Sharpness": "0.5",
                "UseGameSlider": "false", "WaveSize": "default"}


def _find_ini(g, create=False):
    """The helixsr.ini of an installed game, preferring the location its
    install marker points at. With create=True, the default location for a
    game that lost its file (caller must ensure the game is installed)."""
    if g.target is not None:
        folder = g.target / helixscan.FOLDER
        if (folder / helixscan.MARKER).is_file():
            return folder / helixscan.INI
        if (g.target / helixscan.INI).is_file():
            return g.target / helixscan.INI
    for t in g.fsr_targets:
        if (t.parent / helixscan.MARKER).is_file():
            return t.parent / helixscan.INI
    if not create:
        return None
    if g.route == "optiscaler" and g.target is not None:
        folder = g.target / helixscan.FOLDER
        folder.mkdir(parents=True, exist_ok=True)
        return folder / helixscan.INI
    if g.route == "dlss" and g.target is not None:
        return g.target / helixscan.INI
    if g.route == "direct" and g.fsr_targets:
        return g.fsr_targets[0].parent / helixscan.INI
    return None


def _read_ini_values(p: Path):
    """Current helixsr.ini values with README defaults for anything missing
    or invalid. Returns (values, note)."""
    vals = dict(INI_DEFAULTS)
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
        cp = configparser.ConfigParser()
        cp.optionxform = str
        cp.read_string(text)
    except (OSError, configparser.Error):
        return vals, "unreadable, showing defaults"
    try:
        if cp.has_option("Sharpening", "Sharpener"):
            v = cp.get("Sharpening", "Sharpener").strip().lower()
            if v in ("off", "nvidia", "rcas"):
                vals["Sharpener"] = v
        if cp.has_option("Sharpening", "Sharpness"):
            f = float(cp.get("Sharpening", "Sharpness").strip())
            if 0.0 <= f <= 1.0:
                vals["Sharpness"] = str(round(f, 2))
        if cp.has_option("Sharpening", "UseGameSlider"):
            vals["UseGameSlider"] = "true" if cp.getboolean("Sharpening", "UseGameSlider") else "false"
        if cp.has_option("Compatibility", "WaveSize"):
            v = cp.get("Compatibility", "WaveSize").strip()
            if v in ("default", "64", "32"):
                vals["WaveSize"] = v
    except (ValueError, configparser.Error):
        pass
    return vals, ""


def _normalize_ini_values(values: dict):
    """Validate RPC settings (template key names). Returns (normalized, error)."""
    norm = {}
    for key in ("Sharpener", "Sharpness", "UseGameSlider", "WaveSize"):
        if key not in values:
            norm[key] = INI_DEFAULTS[key]
            continue
        v = values[key]
        if key == "Sharpener":
            if not isinstance(v, str) or v.lower() not in ("off", "nvidia", "rcas"):
                return None, f"bad Sharpener: {v!r} (off, nvidia, rcas)"
            norm[key] = v.lower()
        elif key == "Sharpness":
            try:
                f = float(v)
            except (TypeError, ValueError):
                return None, f"bad Sharpness: {v!r} (0.0 to 1.0)"
            if not 0.0 <= f <= 1.0:
                return None, f"bad Sharpness: {v!r} (0.0 to 1.0)"
            norm[key] = str(round(f, 2))
        elif key == "UseGameSlider":
            if not isinstance(v, bool):
                return None, f"bad UseGameSlider: {v!r} (true or false)"
            norm[key] = "true" if v else "false"
        elif key == "WaveSize":
            s = str(v)
            if s not in ("default", "64", "32"):
                return None, f"bad WaveSize: {v!r} (default, 64, 32)"
            norm[key] = s
    for k in values:
        if k not in INI_SECTIONS:
            return None, f"unknown setting: {k}"
    return norm, ""


def _set_ini_value(text: str, section: str, key: str, value: str) -> str:
    """Set one key in helixsr.ini text, preserving comments and line endings.
    Replaces active and commented-out (`;Key =`) lines, collapsing duplicates;
    adds the key under [section] (creating the section if needed)."""
    nl = "\r\n" if "\r\n" in text else "\n"
    pat = re.compile(r"^[;#]?\s*%s\s*=.*$" % re.escape(key))
    out, replaced = [], False
    for ln in text.split("\n"):
        core = ln[:-1] if ln.endswith("\r") else ln
        if pat.match(core):
            if not replaced:
                out.append(f"{key} = {value}")
                replaced = True
            # drop further duplicates
        else:
            out.append(ln)
    if not replaced:
        for i, ln in enumerate(out):
            core = ln[:-1] if ln.endswith("\r") else ln
            if re.match(r"^\[%s\]\s*$" % re.escape(section), core):
                out.insert(i + 1, f"{key} = {value}")
                replaced = True
                break
        if not replaced:
            if out and out[-1].strip():
                out.append("")
            out.append(f"[{section}]")
            out.append(f"{key} = {value}")
    text2 = "\n".join(out)
    if nl == "\r\n":
        text2 = text2.replace("\n", "\r\n").replace("\r\r\n", "\r\n")
    return text2


def _new_mig_state() -> dict:
    return {"running": False, "done": False, "ok": False, "error": "",
            "tag": "", "total": 0, "current": 0, "current_name": "",
            "results": []}


class Plugin:
    async def get_releases(self) -> dict:
        def _fetch():
            try:
                data = _fetch_json(RELEASES_URL)
            except urllib.error.HTTPError as e:
                try:
                    detail = e.read().decode("utf-8", errors="replace")[:300]
                except Exception:
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
                tag = r.get("tag_name", "")
                assets = [a for a in r.get("assets", []) if a.get("name", "").endswith(".zip")]
                if not assets:
                    continue
                a = sorted(assets, key=lambda x: x.get("size", 0), reverse=True)[0]
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
                    dll_version = helixscan.version_of(d / "amd_fidelityfx_dx12.dll")
                except OSError:
                    dll_version = "?"
                versions.append({"tag": _tag_of_dir(d), "dir": d.name,
                                 "network_built": helixscan.network_ready(d),
                                 "dll_version": dll_version,
                                 "size_mb": round(_dir_size(d) / 1e6, 1)})
            return {"active_tag": self.settings.getSetting("active_tag", ""),
                    "versions": versions, "root": str(_helixsr_root()),
                    "plugin_version": _plugin_version(),
                    "extra_folders": self._extra_folders()}
        return await asyncio.to_thread(_state)

    async def add_scan_folder(self, path: str) -> dict:
        """Add a hand-picked game folder to every scan (persisted)."""
        cleaned = helixscan.clean_path(str(path or ""))
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

    async def set_active_version(self, tag: str) -> dict:
        self.settings.setSetting("active_tag", tag)
        self.settings.commit()
        decky.logger.info(f"HelixSR active_tag={tag}")
        return await self.get_state()

    async def start_download(self, tag: str, zip_url: str) -> dict:
        """Download + extract a release in a background thread; poll get_download_status()."""
        if self._dl["running"]:
            return {"ok": False, "error": "a download is already running"}
        self._dl = _new_dl_state(tag)
        self._dl["running"] = True
        threading.Thread(target=self._download_run, args=(tag, zip_url), daemon=True).start()
        return {"ok": True}

    def _download_run(self, tag: str, zip_url: str):
        st = self._dl
        tmp: Path | None = None
        try:
            root = _helixsr_root()
            root.mkdir(parents=True, exist_ok=True)
            tmp = root / f".{tag}.zip"
            req = urllib.request.Request(zip_url, headers={"User-Agent": "helixsr-decky-plugin"})
            with urllib.request.urlopen(req, timeout=120, context=_ssl_context()) as r:
                try:
                    st["total"] = int(r.headers.get("Content-Length") or 0)
                except (TypeError, ValueError):
                    st["total"] = 0
                with open(tmp, "wb") as f:
                    while True:
                        chunk = r.read(1 << 20)
                        if not chunk:
                            break
                        f.write(chunk)
                        st["downloaded"] += len(chunk)
            if st["total"] and st["downloaded"] != st["total"]:
                raise OSError(f"incomplete download: got {st['downloaded']} of {st['total']} bytes")
            with zipfile.ZipFile(tmp) as z:
                top = _zip_top(z)
                for info in z.infolist():
                    # extractall() is skipped: it would also write absolute
                    # paths and ".." entries outside root (Zip Slip)
                    if info.filename.startswith("/") or ".." in Path(info.filename).parts:
                        continue
                    z.extract(info, root)
                _restore_zip_modes(z, root)
            tmp.unlink(missing_ok=True)
            d = root / top
            (d / "helixsr-setup.sh").chmod(0o755)
            (d / "helixsr-uninstall.sh").chmod(0o755)
            st["dir"] = top
            st["network_built"] = helixscan.network_ready(d)
        except Exception as e:  # noqa: BLE001
            decky.logger.error(f"download {tag} failed: {e}")
            st["error"] = str(e)
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

    async def remove_version(self, tag: str) -> dict:
        def _rm():
            with self._ops_lock:
                d = _dir_for_tag(tag)
                if d is None:
                    return {"ok": False, "error": "not downloaded"}
                users = self._version_users(d)
                if users:
                    return {"ok": False, "error":
                            f"still used by {', '.join(users)}: remove HelixSR from those games first"}
                freed = _dir_size(d)
                shutil.rmtree(d, ignore_errors=True)
                if self.settings.getSetting("active_tag") == tag:
                    rest = _version_dirs()
                    self.settings.setSetting("active_tag", _tag_of_dir(rest[0]) if rest else "")
                    self.settings.commit()
                return {"ok": True, "freed_mb": round(freed / 1e6, 1)}
        return await asyncio.to_thread(_rm)

    def _version_users(self, d: Path) -> list:
        """Names of installed games whose markers record this version's DLL.
        Call with _ops_lock held. Unknown DLL version matches nothing."""
        try:
            want = helixscan.version_of(d / "amd_fidelityfx_dx12.dll")
        except OSError:
            return []
        if want == "?":
            return []
        users = []
        for g in self._resolve_games(d, self._extra_folders()):
            try:
                installed = g.installed()
            except Exception:  # noqa: BLE001
                installed = False
            if installed and want in _marker_versions(g):
                users.append(g.name)
        return users

    async def remove_old_versions(self, keep_tag: str | None = None) -> dict:
        """Delete downloaded versions except keep_tag (default: active)."""
        def _rm():
            with self._ops_lock:
                keep = keep_tag or self.settings.getSetting("active_tag", "")
                removed, skipped, freed = [], [], 0
                for d in _version_dirs():
                    tag = _tag_of_dir(d)
                    if keep and tag == keep:
                        continue
                    users = self._version_users(d)
                    if users:
                        skipped.append(f"{tag} (used by {', '.join(users)})")
                        continue
                    freed += _dir_size(d)
                    shutil.rmtree(d, ignore_errors=True)
                    removed.append(tag)
                tags = [_tag_of_dir(x) for x in _version_dirs()]
                active = self.settings.getSetting("active_tag", "")
                if active and active not in tags:
                    self.settings.setSetting("active_tag", tags[0] if tags else "")
                    self.settings.commit()
                return {"ok": True, "removed": removed, "skipped": skipped,
                        "freed_mb": round(freed / 1e6, 1)}
        return await asyncio.to_thread(_rm)

    async def clear_dlss_cache(self) -> dict:
        """Delete once-used NVIDIA DLLs kept for retry in every version dir."""
        def _rm():
            cleared, freed = [], 0
            for d in _version_dirs():
                p = d / DLSS_FILE
                try:
                    if p.is_file():
                        freed += p.stat().st_size
                        p.unlink()
                        cleared.append(f"{_tag_of_dir(d)}/{DLSS_FILE}")
                except OSError:
                    pass
            return {"ok": True, "cleared": cleared, "freed_mb": round(freed / 1e6, 1)}
        return await asyncio.to_thread(_rm)

    async def check_build_ready(self, tag: str | None = None) -> dict:
        """Fast pre-build checklist (no downloads, no writes)."""
        d = _dir_for_tag(tag) if tag else await self._active_dir()
        if d is None:
            return {"ok": False, "error": "no HelixSR version downloaded", "checks": []}
        return await asyncio.to_thread(self._check_build_ready_run, d)

    def _check_build_ready_run(self, d: Path) -> dict:
        import glob
        checks = []
        home = Path(os.environ.get("DECKY_USER_HOME") or Path.home())
        data = Path(os.environ.get("XDG_DATA_HOME") or home / ".local" / "share") / "HelixSR"
        # Python 3 + numpy, same preference order as helixsr-setup.sh.
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
            checks.append({"name": "Python 3 + numpy", "ok": False,
                           "detail": py_detail or "none found (setup downloads a portable Python)"})
        # Shader compiler: Proton/Wine, same search order as helixsr-setup.sh.
        wine = ""
        for pat in ("./local/share/Steam/steamapps/common/Proton*/files/bin/wine",
                    ".steam/steam/steamapps/common/Proton*/files/bin/wine",
                    "./local/share/Steam/compatibilitytools.d/*/files/bin/wine",
                    ".steam/root/compatibilitytools.d/*/files/bin/wine"):
            for p in sorted(glob.glob(str(home / pat))):
                if os.access(p, os.X_OK):
                    wine = p
                    break
            if wine:
                break
        if not wine:
            wine = shutil.which("wine") or ""
        checks.append({"name": "Proton/Wine (shader compiler)", "ok": bool(wine),
                       "detail": wine or "install Proton in Steam or Wine"})
        # Disk space on both filesystems the setup writes to.
        free_rt = _free_bytes(_helixsr_root())
        free_data = _free_bytes(data if data.is_dir() else home)
        need_dlss = not (d / DLSS_FILE).is_file() or _sha256_file(d / DLSS_FILE) != DLSS_SHA256
        detail = (f"runtime {free_rt / 1e9:.1f} GB free, setup data {free_data / 1e9:.1f} GB free"
                  + ("; ~56 MB DLSS download needed" if need_dlss else "; DLSS DLL already verified"))
        checks.append({"name": "Disk space", "ok": free_rt > 500_000_000 and free_data > 500_000_000,
                       "detail": detail})
        return {"ok": all(c["ok"] for c in checks), "checks": checks}

    async def start_migration(self, target_tag: str) -> dict:
        """Move every game installed with another version to target_tag's
        build, in a background thread; poll get_migration_status()."""
        if self._mig["running"]:
            return {"ok": False, "error": "a migration is already running"}
        if self._build_thread and self._build_thread.is_alive():
            return {"ok": False, "error": "a build is running: migrate after it finishes"}
        target = _dir_for_tag(target_tag)
        if target is None:
            return {"ok": False, "error": f"not downloaded: {target_tag}"}
        if not helixscan.network_ready(target):
            return {"ok": False, "error": f"the network is not built for {target_tag}: run Build first"}
        try:
            target_version = helixscan.version_of(target / "amd_fidelityfx_dx12.dll")
        except OSError:
            target_version = "?"
        self._mig = _new_mig_state()
        self._mig["running"] = True
        self._mig["tag"] = target_tag
        threading.Thread(target=self._migration_run,
                         args=(target, target_tag, target_version, self._extra_folders()),
                         daemon=True).start()
        return {"ok": True}

    async def get_migration_status(self) -> dict:
        st = dict(self._mig)
        st["results"] = list(st["results"])
        return st

    def _migration_run(self, target: Path, target_tag: str, target_version: str, extra: list):
        """Uninstall each outdated game (version-agnostic) and reinstall it
        into the new build. One game's failure never stops the rest. Markers
        preserve each game's chosen route, so fresh objects just work."""
        st = self._mig
        helixscan.take_log()
        try:
            with self._ops_lock:
                gl, _skipped = helixscan.games(target, extra)
                todo = []
                for g in gl:
                    try:
                        installed = g.installed()
                    except Exception:  # noqa: BLE001
                        continue
                    if not installed:
                        continue
                    have = _marker_versions(g)
                    if target_version != "?" and target_version in have:
                        continue
                    todo.append(g)
                st["total"] = len(todo)
                results = []
                for n, g in enumerate(todo, 1):
                    if not self._alive:
                        return
                    st["current"] = n
                    st["current_name"] = g.name
                    decky.logger.info(f"HelixSR migrating {g.name} to {target_tag}")
                    try:
                        helixscan.take_log()
                        helixscan.uninstall(g)
                        helixscan.install(g, target)
                        detail = "\n".join(helixscan.take_log())[-2000:]
                        results.append({"name": g.name, "ok": True, "detail": detail})
                        st["results"] = list(results)
                    except SystemExit as e:
                        results.append({"name": g.name, "ok": False,
                                        "detail": str(e) or "aborted"})
                        st["results"] = list(results)
                    except Exception as e:  # noqa: BLE001
                        decky.logger.error(f"HelixSR migration of {g.name} failed: {e}")
                        results.append({"name": g.name, "ok": False,
                                        "detail": f"{type(e).__name__}: {e}"})
                        st["results"] = list(results)
                st["ok"] = all(r["ok"] for r in results)
                if not results:
                    st["ok"] = True
                ok_names = {r["name"] for r in results if r["ok"]}
                self._update_scan_file(
                    [(g.name, str(g.folder)) for g in todo if g.name in ok_names],
                    True, [target_version] if target_version != "?" else None)
                self._scan_cache = _empty_scan_cache()
        except Exception as e:  # noqa: BLE001
            decky.logger.error(f"HelixSR migration failed: {e}")
            st["error"] = f"{type(e).__name__}: {e}"
        finally:
            st["running"] = False
            st["done"] = True

    async def _active_dir(self) -> Path | None:
        tag = self.settings.getSetting("active_tag", "")
        d = _dir_for_tag(tag) if tag else None
        if d is None:
            if tag:
                # Heal a stale selection instead of silently using another version.
                try:
                    self.settings.setSetting("active_tag", "")
                    self.settings.commit()
                except Exception:  # noqa: BLE001
                    pass
            rest = _version_dirs()
            d = rest[-1] if rest else None
        return d

    def _extra_folders(self) -> list:
        try:
            extra = self.settings.getSetting("extra_folders", [])
        except Exception:  # noqa: BLE001
            return []
        return [str(x) for x in extra] if isinstance(extra, list) else []

    async def start_scan(self) -> dict:
        """Scan Steam libraries for compatible games in a background thread; poll get_scan_status()."""
        if self._scan["running"]:
            return {"ok": False, "error": "a scan is already running"}
        d = await self._active_dir()
        if d is None:
            return {"ok": False, "error": "no HelixSR version downloaded"}
        extra = self._extra_folders()
        self._scan = _new_scan_state()
        self._scan["running"] = True
        self._scan_stop.clear()
        threading.Thread(target=self._scan_run,
                         args=(d, _tag_of_dir(d), extra), daemon=True).start()
        return {"ok": True}

    def _scan_run(self, d: Path, tag: str, extra: list):
        st = self._scan
        try:
            # Discard stale say() messages: games() narrates (e.g. folders
            # already listed) and nothing here surfaces them per-scan.
            helixscan.take_log()
            with self._ops_lock:
                gl, skipped = helixscan.games(d, extra, self._scan_stop.is_set)
                self._scan_cache = {"dir": str(d), "games": gl, "tag": tag,
                                    "extra": list(extra), "time": time.monotonic()}
            if self._scan_stop.is_set():
                st["cancelled"] = True
                st["error"] = "scan cancelled"
                return
            out = []
            for i, g in enumerate(gl, 1):
                try:
                    inst = g.installed()
                except Exception:  # noqa: BLE001
                    inst = False
                try:
                    status = "HelixSR installed" if inst else g.label()
                except Exception:  # noqa: BLE001
                    status = g.route or "compatible"
                try:
                    routes = helixscan.available_routes(g)
                except Exception:  # noqa: BLE001
                    routes = [g.route] if g.route else []
                out.append({"index": i, "name": g.name, "folder": str(g.folder),
                            "route": g.route, "available": routes, "inputs": list(g.inputs),
                            "installed": inst, "status": status,
                            "installed_versions": sorted(_marker_versions(g)) if inst else []})
            st["games"] = out
            st["tag"] = tag
            st["skipped"] = skipped
            st["ok"] = True
            try:
                self._write_scan_file(tag, out, skipped)
            except OSError as e:
                decky.logger.warning(f"helixsr scan cache write failed: {e}")
        except Exception as e:  # noqa: BLE001
            decky.logger.error(f"game scan failed: {e}")
            st["error"] = f"{type(e).__name__}: {e}"
        finally:
            st["running"] = False
            st["done"] = True

    async def cancel_scan(self) -> dict:
        """Ask a running scan to stop between games; poll get_scan_status()."""
        if not self._scan["running"]:
            return {"ok": False, "error": "no scan running"}
        self._scan_stop.set()
        return {"ok": True}

    def _scan_cache_path(self) -> Path:
        return _helixsr_root() / "scan-cache.json"

    def _write_scan_file(self, tag: str, games: list, skipped: int):
        payload = {"v": 1, "tag": tag, "scanned_at": time.time(),
                   "skipped": skipped, "games": games}
        tmp = self._scan_cache_path().with_suffix(".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp, self._scan_cache_path())

    async def get_last_scan(self) -> dict:
        """The persisted last scan so reopening the panel doesn't rescan."""
        try:
            payload = json.loads(self._scan_cache_path().read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or not isinstance(payload.get("games"), list):
                raise ValueError("bad cache")
            games = []
            for g in payload["games"]:
                if not isinstance(g, dict) or not g.get("name"):
                    continue
                g.setdefault("inputs", [])
                if not isinstance(g.get("available"), list) or not g["available"]:
                    g["available"] = [g["route"]] if g.get("route") else []
                if not isinstance(g.get("installed_versions"), list):
                    g["installed_versions"] = []
                g.setdefault("status", _status_for(g.get("route"), g.get("inputs") or []))
                g.setdefault("installed", False)
                games.append(g)
            return {"ok": True, "games": games, "tag": payload.get("tag", "") or "",
                    "skipped": int(payload.get("skipped", 0) or 0),
                    "scanned_at": float(payload.get("scanned_at", 0) or 0)}
        except (OSError, ValueError):
            return {"ok": True, "games": [], "tag": "", "skipped": 0, "scanned_at": 0}

    def _update_scan_file(self, targets: list[tuple], installed: bool, versions=None):
        """Optimistically refresh install flags in the persisted scan, matched
        by folder (indices may shift between scans)."""
        try:
            p = self._scan_cache_path()
            payload = json.loads(p.read_text(encoding="utf-8"))
            games = payload.get("games")
            if not isinstance(games, list):
                return
            by_folder = {g.get("folder"): g for g in games if isinstance(g, dict)}
            by_name = {g.get("name"): g for g in games if isinstance(g, dict)}
            changed = False
            for name, folder in targets:
                g = by_folder.get(folder) or by_name.get(name)
                if g is None:
                    continue
                g["installed"] = installed
                g["status"] = "HelixSR installed" if installed else _status_for(
                    g.get("route"), g.get("inputs") or [])
                if versions is not None:
                    g["installed_versions"] = list(versions)
                changed = True
            if changed:
                p.write_text(json.dumps(payload), encoding="utf-8")
        except (OSError, ValueError):
            pass

    async def get_scan_status(self) -> dict:
        st = dict(self._scan)
        st["games"] = list(st["games"])
        return st

    async def install_games(self, indices: list[int], route: str | None = None) -> dict:
        return await self._game_op("install", indices, route)

    async def uninstall_games(self, indices: list[int]) -> dict:
        return await self._game_op("uninstall", indices)

    def _resolve_games(self, d: Path, extra: list | None = None) -> list:
        """Cached or fresh Game objects for installs. Call with _ops_lock held."""
        if extra is None:
            extra = []
        cached = self._scan_cache
        if cached["dir"] == str(d) and cached.get("extra", []) == list(extra) \
                and cached["games"] and time.monotonic() - cached["time"] < SCAN_CACHE_TTL:
            return cached["games"]
        gl, _skipped = helixscan.games(d, extra)
        self._scan_cache = {"dir": str(d), "games": gl, "tag": _tag_of_dir(d),
                            "extra": list(extra), "time": time.monotonic()}
        return gl

    async def _game_op(self, op: str, indices: list[int], route: str | None = None) -> dict:
        d = await self._active_dir()
        if d is None:
            return {"ok": False, "output": "no HelixSR version downloaded"}
        try:
            clean = [int(i) for i in indices]
        except (TypeError, ValueError):
            return {"ok": False, "output": f"bad game numbers: {indices}"}
        if route is not None and not isinstance(route, str):
            return {"ok": False, "output": f"bad route: {route}"}
        route = route or None
        extra = self._extra_folders()
        return await asyncio.to_thread(self._game_op_run, op, d, clean, route, extra)

    def _game_op_run(self, op: str, d: Path, indices: list[int],
                     route: str | None = None, extra: list | None = None) -> dict:
        """Install/uninstall via the reference implementation, in-process (no
        child process, no prompts): fast file copies, guarded by _ops_lock.
        Reuses a fresh scan so every click doesn't re-walk the libraries."""
        try:
            with self._ops_lock:
                if not helixscan.network_ready(d):
                    return {"ok": False, "output": "the network is not built: run Build first"}
                gl = self._resolve_games(d, extra or [])
                targets = []
                for i in indices:
                    if not 1 <= i <= len(gl):
                        return {"ok": False, "output": f"not a game number: {i} (scan found {len(gl)})"}
                    targets.append(gl[i - 1])
                if op == "install" and route is not None:
                    picked = []
                    for g in targets:
                        if route not in helixscan.available_routes(g):
                            return {"ok": False, "output":
                                    f"{g.name} cannot use route '{route}'"}
                        # Same as the reference's per-game question: switch
                        # routes (install() removes the old way first) and
                        # remember OptiScaler where picked. A shallow copy
                        # keeps the scan cache pristine on success and failure.
                        c = copy.copy(g)
                        c.route = route
                        c.chosen = route == "optiscaler" and g.can_opti
                        picked.append(c)
                    targets = picked
                helixscan.take_log()
                for g in targets:
                    decky.logger.info(f"HelixSR {op} {g.name} (route {g.route})")
                    if op == "install":
                        helixscan.install(g, d)
                    else:
                        helixscan.uninstall(g)
                # Note: the cache is intentionally NOT invalidated here. Cached
                # Game objects only resolve index -> folder; installed() and
                # route-relevant markers are re-read live wherever they matter,
                # and mutating game A never changes game B's detection.
                self._update_scan_file([(g.name, str(g.folder)) for g in targets],
                                       op == "install")
                return {"ok": True, "output": "\n".join(helixscan.take_log())[-8000:]}
        except SystemExit as e:
            return {"ok": False, "output": str(e) or "aborted"}
        except Exception as e:  # noqa: BLE001
            decky.logger.error(f"HelixSR {op} failed: {e}")
            return {"ok": False, "output": f"{type(e).__name__}: {e}"}

    async def get_game_settings(self, index: int) -> dict:
        d = await self._active_dir()
        if d is None:
            return {"ok": False, "error": "no HelixSR version downloaded"}
        try:
            idx = int(index)
        except (TypeError, ValueError):
            return {"ok": False, "error": f"bad game number: {index}"}
        return await asyncio.to_thread(self._game_settings_run, "get", d, idx, None)

    async def set_game_settings(self, index: int, values: dict) -> dict:
        d = await self._active_dir()
        if d is None:
            return {"ok": False, "output": "no HelixSR version downloaded"}
        try:
            idx = int(index)
        except (TypeError, ValueError):
            return {"ok": False, "output": f"bad game number: {index}"}
        if not isinstance(values, dict):
            return {"ok": False, "output": "bad settings"}
        return await asyncio.to_thread(self._game_settings_run, "set", d, idx, values)

    def _game_settings_run(self, op: str, d: Path, index: int, values: dict | None) -> dict:
        """Read/write a game's helixsr.ini (sharpening, WaveSize...)."""
        try:
            with self._ops_lock:
                gl = self._resolve_games(d)
                if not 1 <= index <= len(gl):
                    msg = f"not a game number: {index} (scan found {len(gl)})"
                    return {"ok": False, "error" if op == "get" else "output": msg}
                g = gl[index - 1]
                if op == "get":
                    p = _find_ini(g)
                    if p is None:
                        return {"ok": False, "error":
                                "no helixsr.ini found: install HelixSR in this game first"}
                    vals, note = _read_ini_values(p)
                    out = {"ok": True, "path": str(p), "values": vals}
                    if note:
                        out["note"] = note
                    return out
                norm, err = _normalize_ini_values(values or {})
                if err:
                    return {"ok": False, "output": err}
                p = _find_ini(g)
                if p is None:
                    try:
                        installed = g.installed()
                    except Exception:  # noqa: BLE001
                        installed = False
                    if not installed:
                        return {"ok": False, "output":
                                "no helixsr.ini found: install HelixSR in this game first"}
                    p = _find_ini(g, create=True)
                    if p is None:
                        return {"ok": False, "output": "cannot locate settings file for this game"}
                    helixscan.make_ini(p.parent, None)
                text = p.read_text(encoding="utf-8", errors="replace") if p.is_file() else ""
                for key, val in norm.items():
                    text = _set_ini_value(text, INI_SECTIONS[key], key, val)
                p.write_text(text, encoding="utf-8")
                return {"ok": True, "output": f"saved {p}", "path": str(p)}
        except OSError as e:
            msg = f"settings file error: {e}"
            return {"ok": False, "error" if op == "get" else "output": msg}
        except Exception as e:  # noqa: BLE001
            decky.logger.error(f"HelixSR game settings failed: {e}")
            msg = f"{type(e).__name__}: {e}"
            return {"ok": False, "error" if op == "get" else "output": msg}

    async def start_build(self, rebuild: bool = False) -> dict:
        """Build the network: a background thread first ensures NVIDIA's DLSS
        DLL (downloaded with the plugin's own TLS, verified by hash), then the
        reference helixsr-setup.sh runs with --dlss so its Python never needs
        HTTPS. Game prompts default to no on EOF; use install_games after."""
        thread_alive = bool(self._build_thread and self._build_thread.is_alive())
        if thread_alive:
            return {"ok": False, "error": "build already running"}
        d = await self._active_dir()
        if d is None:
            return {"ok": False, "error": "no HelixSR version downloaded"}
        if not rebuild and helixscan.network_ready(d):
            return {"ok": False, "error": "the network is already built — nothing to do"}
        script = d / "helixsr-setup.sh"
        if not script.is_file():
            return {"ok": False, "error": f"helixsr-setup.sh not found in {d}: re-download the version"}
        log = _helixsr_root() / f"build-{_tag_of_dir(d)}.log"
        bash = shutil.which("bash") or "bash"
        import datetime
        header = (f"[helixsr] plugin v{_plugin_version()} building network in {d}\n"
                  f"[helixsr] start: {datetime.datetime.now().isoformat(timespec='seconds')}\n"
                  f"[helixsr] bash: {bash}, setup.sh: {script.stat().st_size} bytes\n")
        with open(log, "w") as f:
            f.write(header)
        self._build_log = log
        self._build_running = True
        self._build_thread = threading.Thread(
            target=self._build_run, args=(d, log, header, bash, str(script), bool(rebuild)), daemon=True)
        self._build_thread.start()
        return {"ok": True, "log": str(log)}

    def _build_log_line(self, log: Path, msg: str):
        with open(log, "a") as f:
            f.write(msg + "\n")

    def _emit_sync(self, ok: bool, rc: int):
        try:
            fut = asyncio.run_coroutine_threadsafe(decky.emit("helixsr_build_done", ok, rc), self.loop)
            fut.result(timeout=30)
        except Exception as e:  # noqa: BLE001
            decky.logger.warning(f"helixsr build emit failed: {e}")

    def _finish_build(self, log: Path, ok: bool, rc: int, note: str):
        self._build_log_line(log, f"[helixsr] {note} (rc={rc})")
        self._build_running = False
        self._emit_sync(ok, rc)

    def _build_run(self, d: Path, log: Path, header: str, bash: str, script: str, rebuild: bool):
        """Phase 1 (worker thread): ensure the DLSS SDK DLL, with progress to
        the build log. Phase 2 is scheduled back on the event loop."""
        line = lambda msg: self._build_log_line(log, msg)  # noqa: E731
        try:
            if not self._alive:
                self._build_running = False
                return
            fixed = _repair_exec_bits(d)
            if fixed:
                line(f"[helixsr] restored executable permission: {', '.join(fixed)}")
            dlss = d / DLSS_FILE
            if dlss.is_file() and _sha256_file(dlss) == DLSS_SHA256:
                line("[helixsr] reusing verified nvngx_dlss.dll")
            else:
                if dlss.is_file():
                    line("[helixsr] existing nvngx_dlss.dll failed checksum, re-downloading")
                    dlss.unlink()
                line("[helixsr] HelixSR needs NVIDIA's DLSS 310.7.0 DLL to build its network on this PC.")
                line(f"[helixsr] downloading from NVIDIA's GitHub ({DLSS_URL}), used once and deleted.")
                line(f"[helixsr] the DLL is NVIDIA's, under NVIDIA's license: {DLSS_LICENSE}")
                req = urllib.request.Request(DLSS_URL, headers={"User-Agent": "helixsr-decky-plugin"})
                try:
                    r = urllib.request.urlopen(req, timeout=120, context=_ssl_context())
                except Exception as e:  # noqa: BLE001
                    line(f"[helixsr] DLSS download failed: {type(e).__name__}: {e}")
                    self._finish_build(log, False, -1, "setup failed: dlss download failed")
                    return
                try:
                    total = int(r.headers.get("Content-Length") or 0)
                except (TypeError, ValueError):
                    total = 0
                got, last_pct = 0, -1
                try:
                    with r, open(dlss, "wb") as f:
                        while True:
                            if not self._alive:
                                self._build_running = False
                                return
                            chunk = r.read(1 << 20)
                            if not chunk:
                                break
                            f.write(chunk)
                            got += len(chunk)
                            pct = int(100 * got / total) if total else -1
                            if pct >= last_pct + 5:
                                last_pct = pct
                                if total:
                                    line(f"[helixsr] downloading nvngx_dlss.dll {got >> 20}/{total >> 20} MB ({pct}%)")
                                else:
                                    line(f"[helixsr] downloading nvngx_dlss.dll {got >> 20} MB ...")
                except Exception as e:  # noqa: BLE001
                    dlss.unlink(missing_ok=True)
                    line(f"[helixsr] DLSS download failed: {type(e).__name__}: {e}")
                    self._finish_build(log, False, -1, "setup failed: dlss download failed")
                    return
                if total and got != total:
                    dlss.unlink(missing_ok=True)
                    line(f"[helixsr] incomplete DLSS download: got {got >> 20} of {total >> 20} MB")
                    self._finish_build(log, False, -1, "setup failed: incomplete dlss download")
                    return
                if _sha256_file(dlss) != DLSS_SHA256:
                    dlss.unlink(missing_ok=True)
                    line("[helixsr] downloaded file is not the expected DLSS 310.7.0 DLL (checksum mismatch)")
                    self._finish_build(log, False, -1, "setup failed: dlss checksum mismatch")
                    return
                line(f"[helixsr] nvngx_dlss.dll downloaded and verified ({got >> 20} MB)")
            if not self._alive:
                self._build_running = False
                return
            fut = asyncio.run_coroutine_threadsafe(
                self._spawn_and_watch(d, log, header, bash, script, dlss, rebuild), self.loop)
            fut.result()
            proc = self._build_proc
            if not self._alive and proc is not None and proc.returncode is None:
                try:
                    proc.terminate()
                except ProcessLookupError:
                    pass
        except Exception as e:  # noqa: BLE001 (e.g. event loop closed by unload)
            try:
                line(f"[helixsr] build aborted: {type(e).__name__}: {e}")
            except OSError:
                pass
            self._build_running = False

    async def _spawn_and_watch(self, d: Path, log: Path, header: str,
                               bash: str, script: str, dlss: Path, rebuild: bool):
        args = [bash, script, "--yes"]
        if rebuild:
            args.append("--rebuild")
        args += ["--dlss", str(dlss)]
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=open(log, "a"), stderr=subprocess.STDOUT,
                cwd=str(d))
        except Exception as e:  # noqa: BLE001
            self._build_log_line(log, f"[helixsr] could not start build: {type(e).__name__}: {e}")
            try:
                await decky.emit("helixsr_build_done", False, -1)
            except Exception:  # noqa: BLE001 (e.g. unloading)
                pass
            self._build_running = False
            return
        self._build_proc = proc

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
                self._build_log_line(log, f"[helixsr] setup exited rc={rc}")
                try:
                    await decky.emit("helixsr_build_done", False, rc)
                except Exception:  # noqa: BLE001 (e.g. unloading)
                    pass
                return
            rc = proc.returncode
            if log.stat().st_size <= len(header) + 1:
                self._build_log_line(log, "[helixsr] NOTE: the setup script produced no output at all "
                                          "(it may have failed before its first message)")
            if rc == 0:
                dlss.unlink(missing_ok=True)
                self._build_log_line(log, "[helixsr] network built; once-used nvngx_dlss.dll removed")
            else:
                self._build_log_line(log, "[helixsr] setup failed; nvngx_dlss.dll kept for retry")
            self._build_log_line(log, f"[helixsr] setup exited rc={rc}")
            try:
                await decky.emit("helixsr_build_done", rc == 0, rc)
            except Exception:  # noqa: BLE001 (e.g. unloading)
                pass
        except Exception as e:  # noqa: BLE001
            self._build_log_line(log, f"\n[helixsr] build watch failed: {e}")
            try:
                await decky.emit("helixsr_build_done", False, -1)
            except Exception:  # noqa: BLE001 (e.g. unloading)
                pass
        finally:
            self._build_running = False
            if self._build_proc is proc:
                self._build_proc = None

    async def get_build_status(self) -> dict:
        running = bool(self._build_running)
        tail = ""
        if self._build_log and self._build_log.exists():
            try:
                data = self._build_log.read_bytes()[-6000:]
                tail = data.decode("utf-8", errors="replace")
            except OSError:
                pass
        d = await self._active_dir()
        return {"running": running, "log": tail,
                "log_path": str(self._build_log) if self._build_log else "",
                "network_built": bool(d and helixscan.network_ready(d))}

    # Asyncio-compatible long-running code, executed in a task when the plugin is loaded
    # MUST NOT BLOCK - spawn tasks for long work.
    async def _main(self):
        self.loop = asyncio.get_event_loop()
        self._alive = True
        self.settings = SettingsManager(
            name="settings",
            settings_directory=os.environ["DECKY_PLUGIN_SETTINGS_DIR"],
        )
        self.settings.read()
        self._build_thread: threading.Thread | None = None
        self._build_running = False
        self._build_log: Path | None = None
        self._dl = _new_dl_state()
        self._scan = _new_scan_state()
        self._mig = _new_mig_state()
        self._scan_stop = threading.Event()
        self._scan_cache = _empty_scan_cache()
        self._ops_lock = threading.Lock()
        self._build_proc = None
        decky.logger.info("HelixSR-Decky-Plugin-Muse loaded")

    # Function called first during the unload process, utilize this to handle your plugin being stopped, but not
    # completely removed
    async def _unload(self):
        decky.logger.info("HelixSR unloading")
        # Mark this instance dead so a DLSS download / pending spawn in
        # _build_run aborts instead of orphaning work after a reload, and
        # terminate a running setup script for the same reason.
        self._alive = False
        proc, self._build_proc = self._build_proc, None
        if proc is not None and proc.returncode is None:
            try:
                proc.terminate()
            except ProcessLookupError:
                pass

    # Function called after `_unload` during uninstall, utilize this to clean up processes and other remnants of your
    # plugin that may remain on the system
    async def _uninstall(self):
        decky.logger.info("HelixSR uninstalled")
        pass

    # Migrations that should be performed before entering `_main()`.
    async def _migration(self):
        decky.logger.info("HelixSR migrating")
        # Here's a migration example for logs:
        # - `~/.config/helixsr-decky-plugin-muse/plugin.log` will be migrated to `decky.decky_LOG_DIR/plugin.log`
        decky.migrate_logs(os.path.join(decky.DECKY_USER_HOME,
                                               ".config", "helixsr-decky-plugin-muse", "plugin.log"))
        # Here's a migration example for settings:
        # - `~/homebrew/settings/helixsr-decky-plugin-muse.json` is migrated to `decky.decky_SETTINGS_DIR/settings.json`
        # - `~/.config/helixsr-decky-plugin-muse/` all files and directories under this root are migrated to `decky.decky_SETTINGS_DIR/`
        decky.migrate_settings(
            os.path.join(decky.DECKY_HOME, "settings", "helixsr-decky-plugin-muse.json"),
            os.path.join(decky.DECKY_USER_HOME, ".config", "helixsr-decky-plugin-muse"))
        # Here's a migration example for runtime data:
        # - `~/homebrew/helixsr-decky-plugin-muse/` all files and directories under this root are migrated to `decky.decky_RUNTIME_DIR/`
        # - `~/.local/share/helixsr-decky-plugin-muse/` all files and directories under this root are migrated to `decky.decky_RUNTIME_DIR/`
        decky.migrate_runtime(
            os.path.join(decky.DECKY_HOME, "helixsr-decky-plugin-muse"),
            os.path.join(decky.DECKY_USER_HOME, ".local", "share", "helixsr-decky-plugin-muse"))
