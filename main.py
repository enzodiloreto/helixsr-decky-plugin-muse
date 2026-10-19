import os
import asyncio
import configparser
import copy
import json
import re
import shutil
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

# The decky plugin module is located at decky-loader/plugin
# For easy intellisense checkout the decky-loader code repo
# and add the `decky-loader/plugin/imports` path to `python.analysis.extraPaths` in `.vscode/settings.json`
import decky
import helixscan
import helixref
from settings import SettingsManager

GITHUB_OWNER = "lonewolf0622"
GITHUB_REPO = "HelixSR"
RELEASES_URL = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases?per_page=30"
# HelixSR Scaler (OptiScaler + HelixSR for DLSS/XeSS/FSR2-only games): separate
# repo and versioning, GPL-3.0, runtime download only (never vendored here).
SCALER_REPO = "lonewolf0622/HelixSR-Scaler"
SCALER_RELEASES_URL = f"https://api.github.com/repos/{SCALER_REPO}/releases?per_page=10"
# Frame-generation build inputs (upstream pins; factual interop values, same as
# the DLSS pin below). Downloaded with this plugin's TLS because the setup's
# Python may not do HTTPS; each file is hash-verified before use.
DLSSG_SL_URL = "https://github.com/NVIDIA-RTX/Streamline/releases/download/v2.8.0/streamline-sdk-v2.8.0.zip"
DLSSG2_SL_URL = "https://github.com/NVIDIA-RTX/Streamline/releases/download/v2.14.16/streamline-sdk-v2.14.16.zip"
DLSSG_ENTRY = "bin/x64/nvngx_dlssg.dll"
DLSSG_SHA256 = "0a6212a33ebaa708e7907468db7345c92cfdc6f8dd29c6e1248f32dbea4bcdaf"
DLSSG_FILE = "nvngx_dlssg.dll"
# HelixSR 2.0+ builds DLSS frame generation from TWO Streamline DLLs
# (310.3 for full mode, 310.9.16 for NVIDIA's light mode). Same entry, a
# newer Streamline release; recognized by checksum like the first.
DLSSG2_SHA256 = "8d59d49b36410dd6b62fef2503a5871d347415bd85d943fa0848563a95dd50c7"
DLSSG2_FILE = "nvngx_dlssg_310.9.16.dll"
XESS_URL = "https://github.com/intel/xess/releases/download/v3.0.2/XeSS_SDK_3.0.2.zip"
XESS_ENTRY = "bin/libxell.dll"
XESS_SHA256 = "d2030dcd694fda8f2ec7e044b13e6db8f0b56d4ba9113a5efad334e3f3ded8c7"


def _helixsr_root() -> Path:
    return Path(os.environ.get("DECKY_PLUGIN_RUNTIME_DIR", tempfile.gettempdir())) / "helixsr"


def _fetch_json(url: str, timeout: int = 30):
    req = urllib.request.Request(url, headers={"User-Agent": "helixsr-decky-plugin", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as r:
        return json.loads(r.read().decode("utf-8"))


def _fetch_zip_entry(url: str, entry: str, sha: str, timeout: int = 120) -> bytes:
    """One file out of a remote zip via HTTP Range requests (the full
    archives are hundreds of MB). The entry is hash-verified. Raises OSError
    when the server refuses ranges, the entry is missing, or the archive uses
    zip64 or multiple disks)."""
    import struct
    import zlib

    def ranged(start: int, size: int, first_url: str):
        req = urllib.request.Request(first_url, headers={
            "User-Agent": "helixsr-decky-plugin", "Range": f"bytes={start}-{start + size - 1}"})
        try:
            r = urllib.request.urlopen(req, timeout=timeout, context=_ssl_context())
        except Exception as e:  # noqa: BLE001
            raise OSError(f"download failed: {type(e).__name__}: {e}")
        if r.status != 206:
            raise OSError(f"server does not support range requests (HTTP {r.status})")
        with r:
            return r.read()

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "helixsr-decky-plugin"},
                                     method="HEAD")
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as r:
            total = int(r.headers.get("Content-Length") or 0)
            url = r.geturl()
    except Exception as e:  # noqa: BLE001
        raise OSError(f"download failed: {type(e).__name__}: {e}")
    if total <= 0:
        raise OSError("server did not report the archive size")
    tail = ranged(total - min(65536, total), min(65536, total), url)
    eocd = tail.rfind(b"PK\x05\x06")
    if eocd < 0:
        raise OSError("not a zip archive")
    cd_size, cd_off = struct.unpack("<II", tail[eocd + 12:eocd + 20])
    if cd_off == 0xFFFFFFFF or cd_size == 0xFFFFFFFF or cd_size <= 0:
        raise OSError("zip64 archives are not supported")
    central = ranged(cd_off, cd_size, url)
    pos, found = 0, None
    while pos + 46 <= len(central):
        if central[pos:pos + 4] != b"PK\x01\x02":
            break
        _, _, _, method, _, _, _, comp_size, _, name_len, extra_len, comment_len, _, _, _, local_off = \
            struct.unpack("<HHHHHHIIIHHHHHII", central[pos + 4:pos + 46])
        name = central[pos + 46:pos + 46 + name_len].decode("utf-8", errors="replace")
        if name == entry:
            found = (method, comp_size, local_off)
            break
        pos += 46 + name_len + extra_len + comment_len
    if found is None:
        raise OSError(f"{entry} not in the archive")
    method, comp_size, local_off = found
    head = ranged(local_off, 30, url)
    if head[:4] != b"PK\x03\x04":
        raise OSError("bad local header")
    lname_len, lextra_len = struct.unpack("<HH", head[26:30])
    # Flag bit 3 (data descriptor) only zeroes the local sizes; the central
    # sizes used here stay valid, and the sha256 check below is authoritative.
    data = ranged(local_off + 30 + lname_len + lextra_len, comp_size, url)
    if len(data) != comp_size:
        raise OSError("short read of the entry")
    if method == 0:
        raw = data
    elif method == 8:
        decomp = zlib.decompressobj(-15)
        raw = decomp.decompress(data) + decomp.flush()
    else:
        raise OSError(f"unsupported compression method {method}")
    import hashlib
    if hashlib.sha256(raw).hexdigest() != sha:
        raise OSError(f"{entry.split('/')[-1]} did not match its checksum")
    return raw


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


def _release_dll(d: Path) -> Path:
    """The release's HelixSR DLL: HelixSR-fsrfg/ on 2.0+, the zip root
    before. The legacy default keeps error messages working for bad dirs."""
    for p in (d / "HelixSR-fsrfg" / "amd_fidelityfx_dx12.dll", d / "amd_fidelityfx_dx12.dll"):
        if p.is_file():
            return p
    return d / "amd_fidelityfx_dx12.dll"


_setup_status_cache: dict = {}


def _setup_status(d: Path):
    """(network, fg) as the release's own setup reports (`helixsr_setup.py
    --status`, 2.0+; it checksums NVIDIA's files), else None. Cached per
    dir mtime like the size cache."""
    if helixref.generation(d) != 3:
        return None
    key = str(d)
    try:
        mtime = d.stat().st_mtime_ns
    except OSError:
        return None
    hit = _setup_status_cache.get(key)
    if hit and hit[0] == mtime:
        return hit[1]
    st = None
    try:
        p = subprocess.run([sys.executable, "-I", str(d / "setup" / "helixsr_setup.py"),
                            str(d), "--status"],
                           capture_output=True, text=True, timeout=300)
        m = re.search(r"network=([01])\s+fg=([01])", p.stdout or "")
        if m:
            st = (m.group(1) == "1", m.group(2) == "1")
    except (OSError, subprocess.SubprocessError):
        pass
    _setup_status_cache[key] = (mtime, st)
    return st


def _network_built(d: Path) -> bool:
    """Whether the network is built: the release's own --status on 2.0+
    (its DLL lives in HelixSR-fsrfg/), the vendored check before."""
    st = _setup_status(d)
    if st is not None:
        return st[0]
    return helixscan.network_ready(d)


# NVIDIA's DLSS SDK DLL that HelixSR builds its network from, verified by hash.
# Same file and hash as the reference setup/helixsr_setup.py downloads
# (Apache-2.0, (c) lonewolf0622). The plugin fetches it with its own TLS
# stack because the system Python the setup picks may lack SSL support
# ("unknown url type: https"), then hands it to the setup via --dlss.
DLSS_URL = 'https://raw.githubusercontent.com/NVIDIA/DLSS/v310.7.0/lib/Windows_x86_64/rel/nvngx_dlss.dll'
DLSS_SHA256 = 'be6e434a94ca32499515eb62ca0e6c274526055d568d0426e4c652dcdfb6ee6e'
DLSS_LICENSE = 'https://github.com/NVIDIA/DLSS/blob/v310.7.0/LICENSE.txt'
DLSS_FILE = 'nvngx_dlss.dll'

for _pin in (DLSS_SHA256, DLSSG_SHA256, DLSSG2_SHA256, XESS_SHA256):
    assert len(_pin) == 64 and all(c in "0123456789abcdef" for c in _pin), \
        "hash pin malformed (must be 64 hex chars)"
del _pin


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


# --- 2.0+ build toolchain (what the release's helixsr-setup.sh would set up:
# a Python 3 with numpy and the Windows DXC shader compiler through Wine) ----

G3_PY_URL = ("https://github.com/astral-sh/python-build-standalone/releases/download/"
             "20261003/cpython-3.12.15%2B20261003-x86_64-unknown-linux-gnu-install_only.tar.gz")
G3_PY_SHA = "f937814031eab4698ca6d07ec606ede1825768f3f3e99af76d9db3900bee03c5"
G3_DXC_URL = ("https://github.com/microsoft/DirectXShaderCompiler/releases/download/"
              "v1.9.2607/dxc_2026_07_29.zip")
G3_DXC_SHA = "a1dfb116ba3eeae6a1582291b53a8e7bf65ad760676bd3194685c8f7367cd241"


def _g3_pins(d: Path) -> dict:
    """URL/hash pins the release's own helixsr-setup.sh carries (parsed, so a
    newer release updates them), falling back to the known constants."""
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


def _download_for_setup(url: str, dest: Path, sha: str, line, cancel) -> None:
    """Stream a pinned download for the 2.0+ build (plugin's own TLS, cancel
    checks, progress lines). Raises OSError on any failure; leaves no file."""
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
    finally:
        pass
    if total and got != total:
        dest.unlink(missing_ok=True)
        raise OSError(f"incomplete {dest.name} download: got {got >> 20} of {total >> 20} MB")
    if _sha256_file(dest) != sha:
        dest.unlink(missing_ok=True)
        raise OSError(f"{dest.name}: checksum mismatch (not the expected file)")
    line(f"[helixsr] {dest.name} downloaded and verified ({got >> 20} MB)")


def _g3_data_dir() -> Path:
    home = Path(os.environ.get("DECKY_USER_HOME") or Path.home())
    return (Path(os.environ.get("XDG_DATA_HOME") or home / ".local" / "share")
            / "HelixSR")


def _find_wine(home: Path) -> str:
    """Proton/Wine with the same search order as helixsr-setup.sh."""
    import glob as _glob
    for pat in ("./local/share/Steam/steamapps/common/Proton*/files/bin/wine",
                ".steam/steam/steamapps/common/Proton*/files/bin/wine",
                "./local/share/Steam/compatibilitytools.d/*/files/bin/wine",
                ".steam/root/compatibilitytools.d/*/files/bin/wine"):
        for p in sorted(_glob.glob(str(home / pat))):
            if os.access(p, os.X_OK):
                return p
    return shutil.which("wine") or ""


def _has_numpy(py: str) -> bool:
    try:
        p = subprocess.run([py, "-c", "import numpy, sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)"],
                           capture_output=True, timeout=90)
        return p.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _ensure_g3_toolchain(d: Path, line, cancel) -> tuple[str, Path]:
    """A (python, dxc-wine shim) for running a 2.0+ release's setup directly:
    mirrors helixsr-setup.sh's own provisioning (portable/system Python with
    numpy, pinned Windows DXC unpacked and wrapped through Wine)."""
    data = _g3_data_dir()
    data.mkdir(parents=True, exist_ok=True)
    pins = _g3_pins(d)
    py = ""
    portable = data / "python" / "bin" / "python3"
    for c in ([str(portable)] if portable.is_file() else []) + ["python3"]:
        if _has_numpy(c):
            py = c
            break
        line(f"[helixsr] {c}: no numpy, trying the next Python")
    if not py:
        line(f"[helixsr] using a portable Python in {data}/python (one-time download, ~67 MB)")
        tar = data / "python.tar.gz"
        try:
            _download_for_setup(pins["PY_URL"], tar, pins["PY_SHA"], line, cancel)
            import tarfile
            with tarfile.open(tar) as t:
                t.extractall(data)
        finally:
            tar.unlink(missing_ok=True)
        p = subprocess.run([str(portable), "-m", "pip", "install", "--quiet",
                            "--disable-pip-version-check", "numpy"],
                           capture_output=True, timeout=600)
        if p.returncode != 0 or not _has_numpy(str(portable)):
            raise OSError("numpy install into the portable Python failed")
        py = str(portable)
    line(f"[helixsr] python: {py}")
    dxcwin = data / "dxc-win"
    if not (dxcwin / "bin" / "x64" / "dxc.exe").is_file():
        z = data / "dxc-win.zip"
        try:
            _download_for_setup(pins["DXCWIN_URL"], z, pins["DXCWIN_SHA"], line, cancel)
            # (the zip stores Windows paths: bin\x64\dxc.exe)
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
    wine = _find_wine(Path(os.environ.get("DECKY_USER_HOME") or Path.home()))
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
    if route in ("unsupported", None, "scaler"):
        have = " / ".join(inputs) if inputs else "no FSR 3.1"
        return f"{have} only: not supported by the drop-in (needs HelixSR Scaler)"
    if route == "optiscaler":
        return " / ".join(inputs) + " (through OptiScaler: select DLSS in the game)"
    if route == "blocked":
        return " / ".join(inputs) + " (anti-cheat: nothing is installed, needs the FSR drop-in)"
    return route or "compatible"


def _marker_versions(g) -> set:
    """HelixSR versions recorded in a game's install markers (per route),
    including this plugin's scaler marker. The installed HelixSR DLL itself
    is also read: it carries the build version no matter what installed it
    (HelixSR-fsrfg/amd_fidelityfx_dx12.dll on 2.0+, HelixSR/helixsr.dll
    before)."""
    out = set()
    paths = []
    if g.target is not None:
        for folder in (g.target / "HelixSR-fsrfg", g.target / helixscan.FOLDER):
            paths.append(folder / helixscan.MARKER)
            paths.append(folder / SCALER_MARKER)
    for t in g.fsr_targets:
        paths.append(t.parent / helixscan.MARKER)
    for p in paths:
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(rec, dict) and rec.get("helixsr"):
            out.add(str(rec["helixsr"]))
    if g.target is not None:
        for hp in (g.target / "HelixSR-fsrfg" / "amd_fidelityfx_dx12.dll",
                   g.target / helixscan.FOLDER / "helixsr.dll"):
            if not hp.is_file():
                continue
            try:
                v = helixscan.version_of(hp)
            except OSError:
                v = "?"
            if v and v != "?":
                out.add(v)
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


_dir_size_cache: dict = {}


def _dir_size_cached(d: Path) -> int:
    """Directory size, cached by keying on the dir's own mtime: every write
    this plugin performs inside a version dir (download, extract, build,
    cleanup) creates, removes or replaces a top-level entry, which bumps it.
    Falls back to a fresh walk when the mtime is unreadable."""
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


PLUGIN_REPO = "enzodiloreto/helixsr-decky-plugin-muse"
PLUGIN_RELEASES_URL = f"https://api.github.com/repos/{PLUGIN_REPO}/releases?per_page=5"
PLUGIN_PACKAGE_NAME = "helixsr-muse-plugin"
PLUGIN_DISPLAY_NAME = "HelixSR Muse Plugin"
PLUGIN_UPDATE_TTL = 6 * 3600


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
    """-1/0/1 numeric dotted comparison, ignoring a leading v."""
    pa, pb = _split_version(a), _split_version(b)
    n = max(len(pa), len(pb))
    pa += [0] * (n - len(pa))
    pb += [0] * (n - len(pb))
    return (pa > pb) - (pa < pb)


def _largest_zip(release: dict):
    """Biggest .zip asset of a GitHub release dict, or None."""
    assets = [a for a in (release.get("assets") or []) if str(a.get("name", "")).endswith(".zip")]
    if not assets:
        return None
    return sorted(assets, key=lambda x: x.get("size", 0) or 0, reverse=True)[0]


def _helixsr_asset(release: dict, tag: str):
    """The HelixSR release zip, matched by name. Since 1.9.0 releases carry
    extra zips (HelixSR-Scaler), largest-zip would grab the wrong file; it
    stays as the fallback for older single-zip releases."""
    assets = [a for a in (release.get("assets") or []) if str(a.get("name", "")).endswith(".zip")]
    if not assets:
        return None
    want = "helixsr-%s.zip" % str(tag).lstrip("vV").lower()
    for a in assets:
        if str(a.get("name", "")).lower() == want:
            return a
    return sorted(assets, key=lambda x: x.get("size", 0) or 0, reverse=True)[0]


def _install_plugin_zip(zip_path: Path, plug: Path, current: str) -> str:
    """Validate a plugin release zip and swap it over the plugin directory.

    Staging happens inside the plugin dir (same filesystem, and a sandbox
    denial fails fast before anything is touched). The swap is two renames
    with restore on failure; the running code is unaffected (Linux keeps
    serving renamed/vanished paths to open handles and imports).
    Returns the installed version.
    """
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
        # Note: the stage moved along with plug; its new home is under bak.
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


def _dir_writable(d: Path) -> bool:
    """Probe whether we can create entries in d (sandbox/ownership-safe)."""
    try:
        probe = d / ".write-probe"
        probe.mkdir(exist_ok=False)
        probe.rmdir()
        return True
    except OSError:
        return False


def _plugin_version() -> str:
    try:
        base = os.environ.get("DECKY_PLUGIN_DIR", "")
        if base:
            import json as _json
            return _json.loads(Path(base, "package.json").read_text()).get("version", "?")
    except (OSError, ValueError):
        pass
    return "?"


def _fg_built(d: Path) -> bool:
    """Whether this version was built with frame generation. 2.0+: the
    release's own --status (fg=1) with the helixsr-fg folder present.
    1.9.x: upstream writes helixsr_fg_setup.json next to the DLL on every
    FG build. Missing or unreadable means not built (or built before this
    check existed)."""
    st = _setup_status(d)
    if st is not None:
        return st[1] and (d / "HelixSR-fsrfg" / "helixsr-fg").is_dir()
    for base in (d,):
        try:
            rec = json.loads((base / "helixsr_fg_setup.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(rec, dict):
            return True
    return False


def _new_dl_state(tag: str = "") -> dict:
    return {"running": False, "done": False, "ok": False, "cancelled": False, "error": "",
            "tag": tag, "downloaded": 0, "total": 0,
            "dir": "", "network_built": False}


def _new_sc_state(tag: str = "") -> dict:
    return {"running": False, "done": False, "ok": False, "cancelled": False, "error": "",
            "tag": tag, "downloaded": 0, "total": 0, "dir": ""}


def _scaler_root() -> Path:
    return Path(os.environ.get("DECKY_PLUGIN_RUNTIME_DIR", tempfile.gettempdir())) / "scaler"


def _scaler_asset(release: dict):
    """The Linux Scaler tarball of a HelixSR-Scaler release dict, or None.
    The Windows .zip is unusable on Deck, so there is no largest-file
    fallback: a release without the linux tarball is skipped."""
    for a in (release.get("assets") or []):
        name = str(a.get("name", ""))
        if name.lower().endswith("-linux.tar.gz"):
            return a
    return None


def _scaler_dirs() -> list[Path]:
    root = _scaler_root()
    if not root.is_dir():
        return []
    return sorted([p for p in root.iterdir() if p.is_dir()])


def _scaler_dir_for_tag(tag: str) -> Path | None:
    want = tag.lstrip("v")
    for d in _scaler_dirs():
        if want and want in d.name:
            return d
    return None


def _safe_tar_extract(tf: tarfile.TarFile, path: Path):
    """Extract a tarball while skipping absolute paths, .. entries and
    anything that is not a regular file or directory (links, devices)."""
    for m in tf.getmembers():
        if m.name.startswith("/") or ".." in Path(m.name).parts:
            continue
        if not (m.isfile() or m.isdir()):
            continue
        tf.extract(m, path)


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
# 1.9.0+ settings: no game slider, WaveSize auto instead of default,
# Sharpness default 0.1 (range and Sharpener unchanged).
INI_DEFAULTS_MODERN = {"Sharpener": "off", "Sharpness": "0.1", "WaveSize": "auto"}
# 2.0+ settings: the game slider is back (2.0's own form has it), WaveSize
# accepts default again, and the ini template grew sections the DLL reads
# (Int8/NetworkResolution for upscaling, the DlssFrameGeneration block).
INI_SECTIONS_G3 = dict(INI_SECTIONS, Int8="Upscaling", NetworkResolution="Upscaling",
                       FgEnabled="DlssFrameGeneration", FgPreset="DlssFrameGeneration",
                       FgMemory="DlssFrameGeneration")
INI_DEFAULTS_G3 = {"Sharpener": "off", "Sharpness": "0.5", "UseGameSlider": "false",
                   "WaveSize": "auto", "Int8": "auto", "NetworkResolution": "fast",
                   "FgEnabled": "true", "FgPreset": "c", "FgMemory": "auto"}
INI_CHOICES_G3 = {"Int8": ("auto", "on", "off"), "NetworkResolution": ("fast", "full"),
                  "FgEnabled": ("true", "false"), "FgPreset": ("c", "full"),
                  "FgMemory": ("auto", "video", "system")}
# The DlssFrameGeneration keys' ini spellings differ from the flat key names.
INI_OPTION_G3 = {"FgEnabled": "Enabled", "FgPreset": "Preset", "FgMemory": "Memory"}
INI_WAVES = {"legacy": ("default", "64", "32"), "modern": ("auto", "64", "32"),
             "g3": ("auto", "default", "64", "32")}
BUILTIN_DEFAULT_PRESET = "Default"
MAX_INI_PRESETS = 20
# HelixSR Scaler settings: OptiScaler.ini [HelixSR] (the synced
# HelixSR/helixsr.ini copy is hands-off).
SCALER_INI_SECTION = "HelixSR"
SCALER_INI_SETTINGS = {"Int8": ("auto", "on", "off"),
                       "WaveSize": ("auto", "64", "32"),
                       "FrameGeneration": ("auto", "on", "off"),
                       "QuickFlow": ("auto", "off", "on", "fastest")}
SCALER_INI_DEFAULTS = {"Int8": "auto", "WaveSize": "auto", "FrameGeneration": "auto",
                       "QuickFlow": "auto"}
# 2.0+ Scaler (1.1.0): frame generation quality is a preset now (c = NVIDIA's
# light mode, the file's default), QuickFlow runs only with full.
SCALER_INI_SETTINGS_G3 = {"Int8": ("auto", "on", "off"),
                          "WaveSize": ("auto", "64", "32"),
                          "FGPreset": ("auto", "c", "full"),
                          "QuickFlow": ("auto", "off", "fast", "fastest")}
SCALER_INI_DEFAULTS_G3 = {k: "auto" for k in SCALER_INI_SETTINGS_G3}


def _find_ini(g, create=False):
    """The helixsr.ini of an installed game, preferring the location its
    install marker points at. With create=True, the default location for a
    game that lost its file (caller must ensure the game is installed)."""
    if g.target is not None:
        # 2.0+ installs keep HelixSR (and its ini) in HelixSR-fsrfg/.
        for folder in (g.target / "HelixSR-fsrfg", g.target / helixscan.FOLDER):
            if (folder / helixscan.MARKER).is_file():
                return folder / helixscan.INI
        if (g.target / "HelixSR-fsrfg" / helixscan.INI).is_file():
            return g.target / "HelixSR-fsrfg" / helixscan.INI
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


def _schema_defaults(schema: str) -> dict:
    if schema == "g3":
        return dict(INI_DEFAULTS_G3)
    if schema == "modern":
        return dict(INI_DEFAULTS_MODERN)
    return dict(INI_DEFAULTS)


def _read_ini_values(p: Path, schema: str = "legacy"):
    """Current helixsr.ini values with template defaults for anything missing
    or invalid. Returns (values, note). Only the schema's keys are returned."""
    vals = _schema_defaults(schema)
    waves = INI_WAVES.get(schema, INI_WAVES["legacy"])
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
        if schema in ("legacy", "g3") and cp.has_option("Sharpening", "UseGameSlider"):
            vals["UseGameSlider"] = "true" if cp.getboolean("Sharpening", "UseGameSlider") else "false"
        if cp.has_option("Compatibility", "WaveSize"):
            v = cp.get("Compatibility", "WaveSize").strip()
            if v in waves:
                vals["WaveSize"] = v
        if schema == "g3":
            for key in ("Int8", "NetworkResolution", "FgEnabled", "FgPreset", "FgMemory"):
                section = INI_SECTIONS_G3[key]
                opt = INI_OPTION_G3.get(key, key)
                if cp.has_option(section, opt):
                    v = cp.get(section, opt).strip().lower()
                    if v in INI_CHOICES_G3[key]:
                        vals[key] = v
    except (ValueError, configparser.Error):
        pass
    return vals, ""


def _normalize_ini_values(values: dict, schema: str = "legacy"):
    """Validate RPC settings (template key names). Only provided keys are
    returned, so partial updates (e.g. auto-fix) leave the rest untouched.
    Keys outside the schema are rejected."""
    norm = {}
    waves = INI_WAVES.get(schema, INI_WAVES["legacy"])
    sections = INI_SECTIONS_G3 if schema == "g3" else INI_SECTIONS
    for key, v in values.items():
        if key not in sections:
            return None, f"unknown setting: {key}"
        if schema == "modern" and key == "UseGameSlider":
            return None, "UseGameSlider was removed in HelixSR 1.9.0 (2.0+ has it back)"
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
            if isinstance(v, bool):
                norm[key] = "true" if v else "false"
            elif isinstance(v, str) and v.lower() in ("true", "false"):
                norm[key] = v.lower()
            else:
                return None, f"bad UseGameSlider: {v!r} (true or false)"
        elif key == "WaveSize":
            s = str(v)
            if s not in waves:
                return None, f"bad WaveSize: {v!r} ({', '.join(waves)})"
            norm[key] = s
        elif schema == "g3" and key in INI_CHOICES_G3:
            if isinstance(v, bool):
                s = "true" if v else "false"
            else:
                s = str(v).lower()
            if s not in INI_CHOICES_G3[key]:
                return None, f"bad {key}: {v!r} ({', '.join(INI_CHOICES_G3[key])})"
            norm[key] = s
    return norm, ""


def _translate_ini_values(values: dict, schema: str):
    """Map a stored preset to a schema: WaveSize default<->auto, the game
    slider dropped (modern) or defaulted (legacy/g3), keys the target schema
    doesn't know (2.0+'s extra keys on older games) skipped. Returns a full
    snapshot for the schema, or (None, err) for corrupt values."""
    if not isinstance(values, dict):
        return None, "bad settings"
    sections = INI_SECTIONS_G3 if schema == "g3" else INI_SECTIONS
    mapped = {}
    for key, v in values.items():
        if key not in sections:
            continue
        if key == "UseGameSlider" and schema == "modern":
            continue
        if key == "WaveSize":
            s = str(v)
            if schema == "modern" and s == "default":
                s = "auto"
            elif schema == "legacy" and s == "auto":
                s = "default"
            mapped[key] = s
        else:
            mapped[key] = v
    full = _schema_defaults(schema)
    full.update(mapped)
    return _normalize_ini_values(full, schema)


def _installed_helixsr_version(g) -> str:
    """HelixSR version string from a game's install markers ("" if none)."""
    candidates = []
    folder = getattr(g, "folder", None)
    if folder is not None:
        candidates.append(Path(folder) / helixscan.FOLDER / helixscan.MARKER)
    if g.target is not None:
        for mfolder in (g.target / "HelixSR-fsrfg", g.target / helixscan.FOLDER):
            candidates.append(mfolder / helixscan.MARKER)
    for t in g.fsr_targets:
        candidates.append(t.parent / helixscan.MARKER)
    for p in candidates:
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(rec, dict) and rec.get("helixsr"):
            return str(rec["helixsr"])
    # No marker: read the installed DLL itself (2.0+ Scaler installs don't
    # keep a helixsr-install.json, their manifest carries the scaler only).
    if g.target is not None:
        for hp in (g.target / "HelixSR-fsrfg" / "amd_fidelityfx_dx12.dll",
                   g.target / helixscan.FOLDER / "helixsr.dll"):
            if not hp.is_file():
                continue
            try:
                v = helixscan.version_of(hp)
            except OSError:
                continue
            if v and v != "?":
                return v
    return ""


def _schema_of(d) -> str:
    """Settings schema of a version dir: 'g3' for 2.0+, else its layout."""
    try:
        if helixref.generation(d) == 3:
            return "g3"
    except Exception:  # noqa: BLE001
        return "legacy"
    return helixref.layout_of(d)


def _ini_layout_for(g, d) -> str:
    """Settings schema for a game: the layout of the HelixSR version that
    installed it (markers record the DLL version), else the active
    version's layout (mixed installs, pruned versions)."""
    try:
        want = _installed_helixsr_version(g)
        if want and want != "?":
            for vd in _version_dirs():
                try:
                    got = helixscan.version_of(_release_dll(vd))
                except OSError:
                    continue
                if got == want:
                    return _schema_of(vd)
    except Exception:  # noqa: BLE001
        pass
    try:
        return _schema_of(d)
    except Exception:  # noqa: BLE001
        return "legacy"


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


# HelixSR Scaler (OptiScaler + HelixSR) per-game installs. The Scaler lives in
# its own download (runtime/scaler); each game gets the package files next to
# its exe plus a HelixSR/ folder (helixsr.dll from the built HelixSR, its
# helixsr-fg when present). Everything the plugin puts down is recorded in
# helixsr-scaler.json (our own marker: upstream neither reads nor writes it),
# foreign files are backed up under HelixSR/backup/ and restored on removal.
SCALER_MARKER = "helixsr-scaler.json"
SCALER_LOADER = "dxgi.dll"
SCALER_SKIP_DIRS = {"Licenses"}
SCALER_SKIP_FILES = {"setup_linux.sh", "setup_windows.bat"}
# Upstream 1.9.3+ record layout (read-only interop: manifest next to the exe,
# backups under HelixSR-Scaler-backup, remover script, runtime-made files).
UPSTREAM_SCALER_MANIFEST = "HelixSR-Scaler.files"
UPSTREAM_SCALER_BACKUP = "HelixSR-Scaler-backup"
UPSTREAM_SCALER_REMOVER = "HelixSR-Scaler-remove.sh"
UPSTREAM_SCALER_MADE = ("OptiScaler.log", "HelixSR/helixsr_upscaler.dll",
                        "HelixSR/helixsr.log", "HelixSR/helixsr.ini",
                        "HelixSR/helixsr-fg/nvexec.log")


def _scaler_marker_path(g):
    if g.target is None:
        return None
    return g.target / helixscan.FOLDER / SCALER_MARKER


def _read_scaler_marker(g):
    """This plugin's Scaler install record for a game ({} if none)."""
    p = _scaler_marker_path(g)
    if p is None:
        return {}
    try:
        rec = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return rec if isinstance(rec, dict) else {}


def _read_upstream_scaler_record(t):
    """Upstream's HelixSR-Scaler.files manifest next to the exe, parsed to
    {name, version, files} (None when absent). Plain-text format, read only."""
    if t is None:
        return None
    try:
        lines = (Path(t) / UPSTREAM_SCALER_MANIFEST).read_text(
            encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    rec = {"name": "dxgi.dll", "version": None, "files": []}
    seen_any = False
    for line in lines:
        line = line.strip()
        if line.startswith("# version="):
            rec["version"] = line.split("=", 1)[1].strip() or None
        elif line.startswith("name="):
            rec["name"] = line.split("=", 1)[1].strip() or rec["name"]
        elif line and not line.startswith("#"):
            rec["files"].append(line.replace("\\", "/"))
            seen_any = True
    return rec if seen_any else None


def _scaler_launch_options(loader: str) -> str | None:
    """Steam launch options for a scaler loader name (None for dxgi.dll:
    Proton loads a game's own dxgi.dll by itself)."""
    if loader.lower() == "dxgi.dll":
        return None
    return f'WINEDLLOVERRIDES="{Path(loader).stem}=n,b" %command%'


def _scaler_install_info(g):
    """Unified view of a scaler install, ours or upstream's: {kind, scaler,
    helixsr, loader, files, launch_options}. None when neither record exists.
    The HelixSR build version comes from the installed helixsr.dll itself,
    so it works no matter what installed it."""
    rec = _read_scaler_marker(g)
    if rec:
        loader = str(rec.get("loader", SCALER_LOADER))
        return {"kind": "ours", "scaler": rec.get("scaler") or None,
                "helixsr": rec.get("helixsr") or None, "loader": loader,
                "files": list(rec.get("files", [])),
                "launch_options": rec.get("launch_options") or _scaler_launch_options(loader)}
    up = _read_upstream_scaler_record(g.target)
    if up is None:
        return None
    helixsr = ""
    if g.target is not None:
        for hp in (g.target / "HelixSR-fsrfg" / "amd_fidelityfx_dx12.dll",
                   g.target / helixscan.FOLDER / "helixsr.dll"):
            if not hp.is_file():
                continue
            try:
                helixsr = helixscan.version_of(hp)
            except OSError:
                helixsr = ""
            if helixsr == "?":
                helixsr = ""
            break
    return {"kind": "upstream", "scaler": up["version"], "helixsr": helixsr,
            "loader": up["name"], "files": list(up["files"]),
            "launch_options": _scaler_launch_options(up["name"])}


def _scaler_package_files(sdir: Path) -> list:
    """Files a Scaler install copies next to the game's exe: the package
    manifest when shipped, else everything except the installer debris."""
    manifest = next(sdir.glob("*-package.txt"), None)
    if manifest is not None:
        try:
            wanted = [l.strip().replace("\\", "/") for l in
                      manifest.read_text(encoding="utf-8", errors="replace").splitlines()]
            out = [n for n in wanted if n and not n.endswith("/")
                   and Path(n).name not in SCALER_SKIP_FILES]
            if out:
                return out
        except OSError:
            pass
    out = []
    for p in sorted(sdir.rglob("*")):
        if not p.is_file() or p.is_symlink():
            continue
        rel = p.relative_to(sdir).as_posix()
        if p.name in SCALER_SKIP_FILES or p.suffix.lower() in (".txt", ".md"):
            if p.parent.name != "Licenses":
                continue
        if rel.split("/")[0] in SCALER_SKIP_DIRS and p.parent.name != "Licenses":
            continue
        out.append(rel)
    return out


def _scaler_install_game(g, sdir: Path, here: Path, scaler_tag: str = "",
                         helixsr_version: str = "") -> str:
    """Install the Scaler into one game (exe folder g.target). Returns a log.
    Raises OSError/SystemExit on failure; backups keep every foreign file."""
    t = g.target
    if t is None:
        raise OSError(f"{g.name}: game folder not found")
    lines = []
    rec = _read_scaler_marker(g)
    folder = t / helixscan.FOLDER
    if (folder / helixscan.MARKER).is_file() and not rec:
        raise OSError(f"{g.name}: has a HelixSR install already, remove it first")
    files = _scaler_package_files(sdir)
    if not files:
        raise OSError("scaler package is empty: re-download it")
    loader = rec.get("loader", SCALER_LOADER)
    backed = rec.get("backup", {}) if isinstance(rec.get("backup"), dict) else {}
    ours = set(rec.get("files", [])) if isinstance(rec.get("files"), list) else set()
    folder = t / helixscan.FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    backup = folder / "backup"
    placed = set()
    # 1. package files (top-level OptiScaler.dll renamed to the loader name).
    for rel in files:
        src = sdir / rel
        if not src.is_file():
            continue
        dst_rel = loader if rel == "OptiScaler.dll" else rel
        dst = t / dst_rel
        placed.add(dst_rel)
        if dst_rel == "OptiScaler.ini" and dst_rel in ours and dst.is_file():
            lines.append("  kept your OptiScaler.ini (delete it to reset scaler settings)")
            continue
        if dst.exists() and not dst.is_symlink() and dst_rel not in ours \
                and dst_rel not in backed:
            backup.mkdir(parents=True, exist_ok=True)
            bdst = backup / Path(dst_rel).name
            if not bdst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                os.replace(dst, bdst)
                backed[dst_rel] = f"backup/{Path(dst_rel).name}"
                lines.append(f"  kept the game's {dst_rel} as {backed[dst_rel]}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
    # 2. HelixSR itself: the built DLL renamed, plus frame generation files.
    dll = here / "amd_fidelityfx_dx12.dll"
    shutil.copyfile(dll, folder / "helixsr.dll")
    helixsr_files = ["helixsr.dll"]
    fg = here / "helixsr-fg"
    if fg.is_dir():
        for p in sorted(fg.rglob("*")):
            if p.is_file() and not p.is_symlink():
                rel = Path("helixsr-fg") / p.relative_to(fg)
                dest = folder / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(p, dest)
                helixsr_files.append(rel.as_posix())
    else:
        lines.append("  no frame generation files in this HelixSR build (upscaling only)")
    lo = _scaler_launch_options(loader)
    (folder / SCALER_MARKER).write_text(json.dumps(
        {"scaler": scaler_tag or rec.get("scaler", ""),
         "helixsr": helixsr_version or rec.get("helixsr", ""),
         "loader": loader, "files": sorted(placed),
         "helixsr_files": sorted(helixsr_files),
         "backup": backed,
         "launch_options": lo}, indent=1) + "\n")
    lines.append(f"  installed in {t}")
    if lo:
        lines.append(f"  Steam launch options (paste per game): {lo}")
    else:
        lines.append("  no Steam launch options needed (dxgi.dll loads by itself)")
    return "\n".join(lines)


def _scaler_uninstall_game(g) -> str:
    """Remove a scaler install: recorded files go, backups and foreign files
    stay. Handles this plugin's record and, as a fallback, upstream's manifest
    (so installs from any version or tooling uninstall cleanly). Returns a log."""
    rec = _read_scaler_marker(g)
    t = g.target
    if not rec:
        if t is None:
            return f"{g.name}: no scaler install found"
        up = _read_upstream_scaler_record(t)
        if up is None:
            return f"{g.name}: no scaler install found"
        # Upstream layout: manifest files + runtime-made files go, the backup
        # dir restores, remover script and manifest go last.
        lines = []
        victims = list(dict.fromkeys(
            up["files"] + list(UPSTREAM_SCALER_MADE)))
        for rel in victims:
            if rel.startswith("/") or ".." in Path(rel).parts:
                continue
            try:
                if (t / rel).is_file() and not (t / rel).is_symlink():
                    (t / rel).unlink()
            except OSError as e:  # noqa: BLE001
                lines.append(f"  could not remove {rel}: {e}")
        backup = t / UPSTREAM_SCALER_BACKUP
        if backup.is_dir():
            for f in sorted(backup.rglob("*")):
                if not f.is_file() and not f.is_symlink():
                    continue
                dst = t / f.relative_to(backup)
                try:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    if not dst.exists():
                        os.replace(f, dst)
                except OSError as e:  # noqa: BLE001
                    lines.append(f"  could not restore {f.name}: {e}")
            shutil.rmtree(backup, ignore_errors=True)
        for n in (UPSTREAM_SCALER_MANIFEST, UPSTREAM_SCALER_REMOVER):
            try:
                (t / n).unlink(missing_ok=True)
            except OSError:
                pass
        for rel in victims:
            parent = t / rel
            for _ in range(max(1, len(Path(rel).parts))):
                parent = parent.parent
                if parent == t:
                    break
                try:
                    parent.rmdir()
                except OSError:
                    break
        lines.append(f"  scaler removed from {t} (take its launch options back out of Steam)")
        return "\n".join(lines)
    folder = t / helixscan.FOLDER
    lines = []
    victims = list(dict.fromkeys(
        list(rec.get("files", []))
        + list(rec.get("helixsr_files", []) if isinstance(rec.get("helixsr_files"), list) else [])
        + [SCALER_MARKER]))
    in_helix = ("helixsr.dll", SCALER_MARKER)
    for rel in victims:
        if rel.startswith("/") or ".." in Path(rel).parts:
            continue
        if rel in in_helix or rel.startswith("helixsr-fg/"):
            p = folder / rel
        else:
            p = t / rel
        try:
            if p.is_file() and not p.is_symlink():
                p.unlink()
        except OSError as e:  # noqa: BLE001
            lines.append(f"  could not remove {rel}: {e}")
    for name, brel in (rec.get("backup", {}) or {}).items():
        if str(name).startswith("/") or ".." in Path(str(name)).parts:
            continue
        src = folder / brel
        try:
            if src.exists() and not (t / name).exists():
                os.replace(src, t / name)
                lines.append(f"  restored the game's {name}")
        except OSError as e:  # noqa: BLE001
            lines.append(f"  could not restore {name}: {e}")
    for rel in victims:
        if rel.startswith("/") or ".." in Path(rel).parts:
            continue
        if rel in in_helix or rel.startswith("helixsr-fg/"):
            parent = folder / rel
        else:
            parent = t / rel
        for _ in range(max(1, len(Path(rel).parts))):
            parent = parent.parent
            if parent == t or parent == folder:
                break
            try:
                parent.rmdir()
            except OSError:
                break
    for sub in ("backup",):
        try:
            (folder / sub).rmdir()
        except OSError:
            pass
    try:
        folder.rmdir()
    except OSError:
        pass
    lines.append(f"  scaler removed from {t} (take its launch options back out of Steam)")
    return "\n".join(lines)


def _repair_direct(g, here):
    """Plugin addition (not reference code): reinstall the FSR drop-in after a
    game update replaced HelixSR's DLL. The reference's install_direct()
    deliberately refuses this case ("left as it is") so it never clobbers an
    unrecognized file; repair is explicit instead: the game's current DLL
    becomes the new backup, then the drop-in proceeds as usual."""
    mod = helixref.install_module(here)
    for t in g.fsr_targets:
        orig = helixscan.original(t)
        if t.exists() and not helixscan.is_helixsr(t):
            decky.logger.info(f"HelixSR repair {t}: not HelixSR (game updated); keeping it as {orig.name}")
            if orig.exists():
                orig.unlink()
            os.replace(t, orig)
        if not t.exists():
            if not orig.exists():
                decky.logger.info(f"HelixSR repair {t}: game file and backup both missing; skipped")
                continue
            shutil.copyfile(orig, t)
        if helixscan.is_helixsr(t):
            if not orig.exists():
                decky.logger.info(f"HelixSR repair {t}: HelixSR without a backup of the game's file; left as it is")
                continue
            helixscan.copy_checked(here / helixscan.DLL, t)   # update
        else:
            if not orig.exists():
                os.replace(t, orig)
            helixscan.copy_checked(here / helixscan.DLL, t)
        mod.copy_network(here, t.parent)
        made = helixscan.read_marker(t.parent).get("ini_made")
        (t.parent / helixscan.MARKER).write_text(json.dumps(
            {"helixsr": helixscan.version_of(here / helixscan.DLL), "files": [t.name],
             "route": "direct", "ini_made": mod.make_ini(t.parent, made)}, indent=1) + "\n")
        decky.logger.info(f"HelixSR repair installed {t} (the game's file kept as {orig.name})")


class Plugin:
    async def get_releases(self, include_prereleases: bool = False) -> dict:
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
                    dll_version = helixscan.version_of(_release_dll(d))
                except OSError:
                    dll_version = "?"
                versions.append({"tag": _tag_of_dir(d), "dir": d.name,
                                 "network_built": _network_built(d),
                                 "dll_version": dll_version,
                                 "layout": helixref.layout_of(d),
                                 "generation": helixref.generation(d),
                                 "fg_built": _fg_built(d),
                                 "fg_available": helixref.fg_engine_available(d),
                                 "size_mb": round(_dir_size_cached(d) / 1e6, 1)})
            return {"active_tag": self.settings.getSetting("active_tag", ""),
                    "versions": versions, "root": str(_helixsr_root()),
                    "plugin_version": _plugin_version(),
                    "update_pending": self._update_pending,
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
        self._dl_stop.clear()
        threading.Thread(target=self._download_run, args=(tag, zip_url), daemon=True).start()
        return {"ok": True}

    async def cancel_download(self) -> dict:
        """Ask a running release download to stop; poll get_download_status()."""
        if not self._dl["running"]:
            return {"ok": False, "error": "no download running"}
        self._dl_stop.set()
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
                        if self._dl_stop.is_set():
                            break
                        chunk = r.read(1 << 20)
                        if not chunk:
                            break
                        f.write(chunk)
                        st["downloaded"] += len(chunk)
            if self._dl_stop.is_set():
                tmp.unlink(missing_ok=True)
                st["cancelled"] = True
                st["error"] = "download cancelled"
                return
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
            if not d.is_dir() or not _release_dll(d).is_file():
                raise OSError(f"not a HelixSR release layout (no HelixSR DLL in {top})")
            for name in ("helixsr-setup.sh", "helixsr-uninstall.sh"):
                p = d / name
                if p.is_file():
                    p.chmod(0o755)
                else:
                    # Older releases (e.g. 1.4.3) ship no uninstaller.
                    decky.logger.debug(f"HelixSR {tag}: no {name} in this release")
            st["dir"] = top
            st["network_built"] = _network_built(d)
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

    async def get_scaler_releases(self, include_prereleases: bool = False) -> dict:
        """HelixSR Scaler releases (separate repo): Linux tarballs for Deck."""
        def _fetch():
            try:
                data = _fetch_json(SCALER_RELEASES_URL)
            except Exception as e:  # noqa: BLE001
                err = f"{type(e).__name__}: {e}"
                decky.logger.warning(f"get_scaler_releases failed: {err}")
                return {"releases": [], "error": err}
            out = []
            for r in data:
                if not isinstance(r, dict) or r.get("draft"):
                    continue
                if r.get("prerelease") and not include_prereleases:
                    continue
                tag = r.get("tag_name", "")
                a = _scaler_asset(r)
                if not tag or a is None:
                    continue
                out.append({"tag": tag, "name": r.get("name", tag),
                            "published_at": r.get("published_at", "")[:10],
                            "tar_name": a["name"], "tar_url": a["browser_download_url"],
                            "size": a.get("size", 0),
                            "body": (r.get("body") or "")[:2000]})
            return {"releases": out, "error": ""}
        return await asyncio.to_thread(_fetch)

    async def get_scaler_state(self) -> dict:
        """Downloaded Scaler version (single slot) and its health."""
        def _state():
            tag = self.settings.getSetting("scaler_tag", "")
            d = _scaler_dir_for_tag(tag) if tag else None
            ok = bool(d and (d / "OptiScaler.dll").is_file()
                      and (d / "OptiScaler.ini").is_file())
            return {"tag": tag, "dir": d.name if d else "",
                    "files_ok": ok,
                    "size_mb": round(_dir_size_cached(d) / 1e6, 1) if d else 0.0}
        return await asyncio.to_thread(_state)

    async def start_scaler_download(self, tag: str, tar_url: str) -> dict:
        """Download + extract a Scaler release in a background thread; poll
        get_scaler_download_status(). Replaces any previous Scaler version."""
        if self._sc["running"]:
            return {"ok": False, "error": "a scaler download is already running"}
        self._sc = _new_sc_state(tag)
        self._sc["running"] = True
        self._sc_stop.clear()
        threading.Thread(target=self._scaler_download_run, args=(tag, tar_url), daemon=True).start()
        return {"ok": True}

    async def cancel_scaler_download(self) -> dict:
        """Ask a running Scaler download to stop; poll get_scaler_download_status()."""
        if not self._sc["running"]:
            return {"ok": False, "error": "no scaler download running"}
        self._sc_stop.set()
        return {"ok": True}

    async def get_scaler_download_status(self) -> dict:
        return dict(self._sc)

    def _scaler_download_run(self, tag: str, tar_url: str):
        st = self._sc
        tmp: Path | None = None
        try:
            root = _scaler_root()
            root.mkdir(parents=True, exist_ok=True)
            tmp = root / f".{tag}.tar.gz"
            req = urllib.request.Request(tar_url, headers={"User-Agent": "helixsr-decky-plugin"})
            with urllib.request.urlopen(req, timeout=120, context=_ssl_context()) as r:
                try:
                    st["total"] = int(r.headers.get("Content-Length") or 0)
                except (TypeError, ValueError):
                    st["total"] = 0
                with open(tmp, "wb") as f:
                    while True:
                        if self._sc_stop.is_set():
                            break
                        chunk = r.read(1 << 20)
                        if not chunk:
                            break
                        f.write(chunk)
                        st["downloaded"] += len(chunk)
            if self._sc_stop.is_set():
                tmp.unlink(missing_ok=True)
                st["cancelled"] = True
                st["error"] = "download cancelled"
                return
            if st["total"] and st["downloaded"] != st["total"]:
                raise OSError(f"incomplete download: got {st['downloaded']} of {st['total']} bytes")
            with tarfile.open(tmp, "r:gz") as tf:
                tops = {m.name.split("/")[0] for m in tf.getmembers()
                        if not m.name.startswith("/") and ".." not in Path(m.name).parts
                        and m.name not in (".", "./")}
                if len(tops) != 1:
                    raise OSError("unexpected scaler package layout")
                _safe_tar_extract(tf, root)
                top = tops.pop()
            tmp.unlink(missing_ok=True)
            d = root / top
            if not (d / "OptiScaler.dll").is_file() or not (d / "OptiScaler.ini").is_file():
                shutil.rmtree(d, ignore_errors=True)
                raise OSError(f"not a HelixSR Scaler layout (no {top}/OptiScaler.dll + OptiScaler.ini)")
            setup = d / "setup_linux.sh"
            if setup.is_file():
                try:
                    setup.chmod(0o755)
                except OSError:
                    pass
            st["dir"] = top
            # Single slot: the new version replaces any previous one. Games
            # already carry their own copies, so they keep working.
            for old in _scaler_dirs():
                if old != d:
                    shutil.rmtree(old, ignore_errors=True)
            self.settings.setSetting("scaler_tag", tag)
            self.settings.commit()
        except Exception as e:  # noqa: BLE001
            decky.logger.error(f"scaler download {tag} failed: {e}")
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

    async def check_plugin_update(self, force: bool = False) -> dict:
        return await asyncio.to_thread(self._check_plugin_update_sync, force)

    def _check_plugin_update_sync(self, force: bool = False) -> dict:
        """Is there a newer HelixSR Muse Plugin release? Cached for 6h."""
        now = time.monotonic()
        if not force and self._update_check["at"] and now - self._update_check["at"] < PLUGIN_UPDATE_TTL:
            return dict(self._update_check["result"])
        current = _plugin_version()
        try:
            data = _fetch_json(PLUGIN_RELEASES_URL)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
        best = None
        for r in data:
            if not isinstance(r, dict) or r.get("draft") or r.get("prerelease"):
                continue
            tag = str(r.get("tag_name") or "")
            asset = _largest_zip(r)
            if not tag or asset is None:
                continue
            if _compare_versions(tag, current) > 0 and \
                    (best is None or _compare_versions(tag, best["tag"]) > 0):
                ver = tag[1:] if tag[:1].lower() == "v" else tag
                best = {"tag": tag, "latest": ver, "url": asset["browser_download_url"],
                        "size": asset.get("size", 0) or 0,
                        "body": (r.get("body") or "")[:2000]}
        result = {"ok": True, "current": current, "update_available": best is not None}
        if best:
            result.update(best)
        try:
            plug = Path(os.environ.get("DECKY_PLUGIN_DIR") or "")
            result["can_install"] = plug.is_dir() and _dir_writable(plug)
        except OSError:
            result["can_install"] = False
        self._update_check = {"at": now, "result": result}
        return dict(result)

    async def start_plugin_update(self) -> dict:
        """Download the newest release and install it over the plugin directory
        in a background thread; poll get_plugin_update_status(). The new code
        takes effect after Decky restarts."""
        if self._pu["running"]:
            return {"ok": False, "error": "an update is already running"}
        busy = self._dl["running"] or self._scan["running"] or self._mig["running"] \
            or bool(self._build_thread and self._build_thread.is_alive())
        if busy:
            return {"ok": False, "error": "finish the running download, scan, build or migration first"}
        check = await self.check_plugin_update()
        if not check.get("ok"):
            return {"ok": False, "error": check.get("error") or "update check failed"}
        if not check.get("update_available"):
            return {"ok": False, "error": "already on the newest version"}
        self._pu = {"running": True, "done": False, "ok": False, "cancelled": False, "error": "",
                    "version": check["latest"], "url": check["url"],
                    "downloaded": 0, "total": check.get("size", 0) or 0,
                    "mode": "", "path": ""}
        self._pu_stop.clear()
        threading.Thread(target=self._plugin_update_run, daemon=True).start()
        return {"ok": True, "version": check["latest"]}

    async def get_plugin_update_status(self) -> dict:
        return dict(self._pu)

    async def cancel_plugin_update(self) -> dict:
        """Ask a running plugin update to stop; poll get_plugin_update_status()."""
        if not self._pu["running"]:
            return {"ok": False, "error": "no update running"}
        self._pu_stop.set()
        return {"ok": True}

    def _plugin_update_run(self):
        st = self._pu
        tmp: Path | None = None
        try:
            if not self._alive:
                return
            plug = Path(os.environ.get("DECKY_PLUGIN_DIR") or "")
            inplace = plug.is_dir() and _dir_writable(plug)
            st["mode"] = "inplace" if inplace else "manual"
            root = _helixsr_root()
            root.mkdir(parents=True, exist_ok=True)
            tmp = root / ".plugin-update.zip"
            req = urllib.request.Request(st["url"], headers={"User-Agent": "helixsr-muse-plugin"})
            with urllib.request.urlopen(req, timeout=120, context=_ssl_context()) as r:
                try:
                    total = int(r.headers.get("Content-Length") or 0)
                except (TypeError, ValueError):
                    total = 0
                if total:
                    st["total"] = total
                with open(tmp, "wb") as f:
                    while True:
                        if not self._alive:
                            return
                        if self._pu_stop.is_set():
                            break
                        chunk = r.read(1 << 20)
                        if not chunk:
                            break
                        f.write(chunk)
                        st["downloaded"] += len(chunk)
            if self._pu_stop.is_set():
                tmp.unlink(missing_ok=True)
                st["cancelled"] = True
                st["error"] = "update cancelled"
                return
            if st["total"] and st["downloaded"] != st["total"]:
                raise OSError(f"incomplete download: got {st['downloaded']} of {st['total']} bytes")
            if inplace:
                new_version = _install_plugin_zip(tmp, plug, _plugin_version())
                st["ok"] = True
                self._update_pending = new_version
                self._update_check = {"at": 0.0, "result": {"ok": True, "update_available": False}}
            else:
                st["path"] = self._stage_manual_zip(tmp, st["version"])
                st["ok"] = True
        except Exception as e:  # noqa: BLE001
            decky.logger.error(f"plugin self-update failed: {e}")
            st["error"] = str(e)
        finally:
            try:
                if tmp is not None:
                    tmp.unlink(missing_ok=True)
            except OSError:
                pass
            st["running"] = False
            st["done"] = True

    def _stage_manual_zip(self, tmp: Path, version: str) -> str:
        """Validate the release zip and copy it somewhere the user can reach
        for Decky's manual Install-from-ZIP flow. Returns the destination path."""
        ver = version[1:] if version[:1].lower() == "v" else version
        name = f"helixsr-muse-plugin-v{ver}.zip"
        home = Path(os.environ.get("DECKY_USER_HOME") or Path.home())
        downloads = home / "Downloads"
        dest_dir = downloads if _dir_writable(downloads) else _helixsr_root()
        with zipfile.ZipFile(tmp) as z:
            names = [i.filename for i in z.infolist()
                     if not i.filename.startswith("/") and ".." not in Path(i.filename).parts]
            tops = {n.split("/")[0] for n in names if "/" in n}
            if len(tops) != 1:
                raise OSError("unexpected plugin zip layout")
            top = tops.pop()
            meta = json.loads(z.read(f"{top}/plugin.json").decode("utf-8"))
            pkg = json.loads(z.read(f"{top}/package.json").decode("utf-8"))
            if meta.get("name") != PLUGIN_DISPLAY_NAME:
                raise OSError(f"zip is not {PLUGIN_DISPLAY_NAME}")
            if _compare_versions(str(pkg.get("version") or ""), _plugin_version()) <= 0:
                raise OSError("zip is not newer than the running plugin")
        dest = dest_dir / name
        shutil.copyfile(tmp, dest)
        return str(dest)

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
                freed = _dir_size_cached(d)
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
            want = helixscan.version_of(_release_dll(d))
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
                    freed += _dir_size_cached(d)
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

    async def check_build_ready(self, tag: str | None = None, fg: bool = False) -> dict:
        """Fast pre-build checklist (no downloads, no writes)."""
        d = _dir_for_tag(tag) if tag else await self._active_dir()
        if d is None:
            return {"ok": False, "error": "no HelixSR version downloaded", "checks": []}
        if fg and helixref.layout_of(d) != "modern":
            return {"ok": False, "error": "frame generation needs HelixSR 1.9.0+", "checks": []}
        if fg and not helixref.fg_engine_available(d):
            return {"ok": False, "error": "this HelixSR has no frame generation engine "
                                          "(withdrawn in 1.9.3+; alternative providers live in Scaler)",
                    "checks": []}
        return await asyncio.to_thread(self._check_build_ready_run, d, bool(fg))

    def _check_build_ready_run(self, d: Path, fg: bool = False) -> dict:
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
        elif helixref.generation(d) == 3:
            # The plugin provisions the setup's portable Python itself
            # during a 2.0+ build (the wrapper would do the same): not a
            # gate, just a note. Wine/disk/DLSS rows stay hard gates.
            checks.append({"name": "Python 3 + numpy", "ok": True,
                           "detail": f"{py_detail or 'none found'} — a portable Python "
                                     "(~67 MB) will be downloaded during the build"})
        else:
            checks.append({"name": "Python 3 + numpy", "ok": False,
                           "detail": py_detail or "none found (setup downloads a portable Python)"})
        # Shader compiler: Proton/Wine, same search order as helixsr-setup.sh.
        wine = _find_wine(home)
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
        if helixref.layout_of(d) == "modern":
            checks.append({"name": "Frame generation", "ok": True,
                           "detail": f"{'built' if _fg_built(d) else 'not built'}"
                           + (" (--no-fg): the plugin installs the FSR drop-in only"
                              if not fg else ": DLSS-G downloads on build, NGX core via the setup")})
        if fg:
            gen3 = helixref.generation(d) == 3
            fg_dlls = [(DLSSG_FILE, DLSSG_SHA256)] + ([(DLSSG2_FILE, DLSSG2_SHA256)] if gen3 else [])
            have = [name for name, sha in fg_dlls
                    if (d / name).is_file() and _sha256_file(d / name) == sha]
            if len(have) == len(fg_dlls):
                checks.append({"name": "DLSS Frame Generation DLLs", "ok": True,
                               "detail": "cached, verified"})
            else:
                sizes = "~8 MB" if len(fg_dlls) == 1 else "~8 MB each of 2"
                checks.append({"name": "DLSS Frame Generation DLLs", "ok": True,
                               "detail": f"will download {sizes} on build "
                                         "(NVIDIA Streamline SDK)"})
            zstd = shutil.which("zstd") or ""
            py314 = ""
            for c in ([str(portable)] if portable.is_file() else []) + ["python3"]:
                try:
                    p = subprocess.run(
                        [c, "-c", "import sys; sys.exit(0 if sys.version_info >= (3, 14) else 1)"],
                        capture_output=True, timeout=90)
                    if p.returncode == 0:
                        py314 = c
                        break
                except (OSError, subprocess.SubprocessError):
                    pass
            # The 400 MB driver package streams through the extractor: peak
            # disk is bounded, 1 GB free on the setup data dir is plenty.
            ngx_ok = bool(zstd or py314) and free_data > 1_000_000_000
            checks.append({"name": "NGX core prerequisites", "ok": ngx_ok,
                           "detail": (f"{'zstd' if zstd else py314 or 'no zstd/Python 3.14'}; "
                                      f"setup data {free_data / 1e9:.1f} GB free")})
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
        if not _network_built(target):
            return {"ok": False, "error": f"the network is not built for {target_tag}: run Build first"}
        try:
            target_version = helixscan.version_of(_release_dll(target))
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
        try:
            with self._ops_lock:
                gl, _skipped, _log = helixref.scan(target, extra)
                todo = []
                for g in gl:
                    try:
                        installed = g.installed()
                    except Exception:  # noqa: BLE001
                        installed = False
                    sc = _scaler_install_info(g)
                    if not installed and not sc:
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
                        if _scaler_install_info(g):
                            detail = self._scaler_install_one(g, target)
                        else:
                            detail = helixref.uninstall(g, target) + "\n" + helixref.install(g, target)
                        results.append({"name": g.name, "ok": True, "detail": detail[-2000:]})
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
            # The scan narrates (e.g. folders already listed); per-scan output
            # is returned by the dispatch and dropped here, as before.
            with self._ops_lock:
                gl, skipped, _log = helixref.scan(d, extra, self._scan_stop.is_set)
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
                    routes = helixref.available_routes(g, d)
                except Exception:  # noqa: BLE001
                    routes = [g.route] if g.route else []
                if not inst and not routes and helixref.layout_of(d) == "modern" \
                        and not getattr(g, "fsr_targets", None) \
                        and list(getattr(g, "inputs", []) or []):
                    old = g.old_install() if callable(getattr(g, "old_install", None)) else None
                    if not old:
                        routes = ["scaler"]
                sc = _scaler_install_info(g)
                launch_options = ""
                if sc:
                    # A scaler install, ours or upstream's (upstream objects
                    # don't report it): overlay the installed state here.
                    # installed_versions carries the HelixSR build (migration
                    # matching) plus the scaler tag (display).
                    inst = True
                    status = "HelixSR Scaler installed"
                    routes = ["scaler"]
                    launch_options = sc.get("launch_options") or ""
                    iv = [sc["helixsr"]] if sc.get("helixsr") else []
                    iv.append(f"scaler-{sc['scaler']}" if sc.get("scaler") else "scaler")
                else:
                    iv = sorted(_marker_versions(g))
                out.append({"index": i, "name": g.name, "folder": str(g.folder),
                            "route": "scaler" if sc else g.route,
                            "available": routes, "inputs": list(g.inputs),
                            "installed": inst, "status": status,
                            "installed_versions": iv if inst else [],
                            "launch_options": launch_options,
                            "exe": str(g.target) if g.target else "",
                            "fsr": [str(p) for p in g.fsr_targets]})
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
                g.setdefault("exe", "")
                if not isinstance(g.get("fsr"), list):
                    g["fsr"] = []
                g.setdefault("launch_options", "")
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

    def _update_scaler_rows(self, targets: list):
        """Refresh persisted scan rows for scaler installs from the live
        install record (launch options, versions): the panel can then refresh
        from this cache instead of walking the libraries again.
        Call with _ops_lock held."""
        try:
            p = self._scan_cache_path()
            payload = json.loads(p.read_text(encoding="utf-8"))
            games = payload.get("games")
            if not isinstance(games, list):
                return
            by_folder = {g.get("folder"): g for g in games if isinstance(g, dict)}
            by_name = {g.get("name"): g for g in games if isinstance(g, dict)}
            changed = False
            for g in targets:
                row = by_folder.get(str(g.folder)) or by_name.get(g.name)
                if row is None:
                    continue
                sc = _scaler_install_info(g)
                if not sc:
                    continue
                row["installed"] = True
                row["status"] = "HelixSR Scaler installed"
                row["route"] = "scaler"
                row["available"] = ["scaler"]
                row["launch_options"] = sc.get("launch_options") or ""
                iv = [sc["helixsr"]] if sc.get("helixsr") else []
                iv.append(f"scaler-{sc['scaler']}" if sc.get("scaler") else "scaler")
                row["installed_versions"] = iv
                changed = True
            if changed:
                p.write_text(json.dumps(payload), encoding="utf-8")
        except (OSError, ValueError):
            pass

    async def get_scan_status(self) -> dict:
        st = dict(self._scan)
        st["games"] = list(st["games"])
        return st

    async def install_games(self, indices: list[int], route: str | None = None,
                            fg: bool | None = None) -> dict:
        """route "" = default; fg (2.0+ Scaler installs only): None auto (on
        when built, the release's default), else on/off."""
        return await self._game_op("install", indices, route, fg)

    async def uninstall_games(self, indices: list[int]) -> dict:
        return await self._game_op("uninstall", indices)

    async def repair_games(self, indices: list[int]) -> dict:
        """Reinstall into games a Steam update broke (drop-in DLL replaced).
        Other routes repair through the normal install path (idempotent)."""
        return await self._game_op("repair", indices)

    def _resolve_games(self, d: Path, extra: list | None = None) -> list:
        """Cached or fresh Game objects for installs. Call with _ops_lock held."""
        if extra is None:
            extra = []
        cached = self._scan_cache
        if cached["dir"] == str(d) and cached.get("extra", []) == list(extra) \
                and cached["games"] and time.monotonic() - cached["time"] < SCAN_CACHE_TTL:
            return cached["games"]
        gl, _skipped, _log = helixref.scan(d, extra)
        self._scan_cache = {"dir": str(d), "games": gl, "tag": _tag_of_dir(d),
                            "extra": list(extra), "time": time.monotonic()}
        return gl

    async def _game_op(self, op: str, indices: list[int], route: str | None = None,
                       fg: bool | None = None) -> dict:
        d = await self._active_dir()
        if d is None:
            return {"ok": False, "output": "no HelixSR version downloaded"}
        try:
            clean = [int(i) for i in indices]
        except (TypeError, ValueError):
            return {"ok": False, "output": f"bad game numbers: {indices}"}
        if route is not None and not isinstance(route, str):
            return {"ok": False, "output": f"bad route: {route}"}
        if fg is not None and not isinstance(fg, bool):
            return {"ok": False, "output": f"bad fg flag: {fg!r}"}
        route = route or None
        extra = self._extra_folders()
        if op == "install" and route == "scaler":
            return await asyncio.to_thread(self._scaler_install_run, d, clean, extra, fg)
        return await asyncio.to_thread(self._game_op_run, op, d, clean, route, extra)

    def _scaler_dir_or_error(self):
        tag = self.settings.getSetting("scaler_tag", "")
        d = _scaler_dir_for_tag(tag) if tag else None
        if d is None or not (d / "OptiScaler.dll").is_file():
            return None, "download HelixSR Scaler first"
        return d, ""

    def _scaler_helixsr_check(self, d):
        """(dir, error): scaler installs need a built modern HelixSR."""
        if helixref.layout_of(d) != "modern":
            return None, "activate a downloaded HelixSR 1.9.0+ version first"
        if not _network_built(d):
            return None, "the network is not built: run Build first"
        return d, ""

    def _scaler_install_one(self, g, d, fg: bool | None = None):
        """Install (or refresh) scaler into one resolved game, +, returns log.
        2.0+ releases install through their own bridge (bundled Scaler with
        frame generation, pairing checks, manifest and remover); versions
        with upstream scaler support (1.9.3+) use the reference installer
        (route forced to scaler: anti-cheat refusals, loader choice,
        manifest and remover included); older ones use this plugin's own
        installer, preferring the version's bundled helixsr-scaler/ over the
        separate Scaler download. Raises."""
        if helixref.generation(d) == 3:
            # fg: None auto = on when built (the release's own default:
            # Enter = yes); the games panel's toggle passes true/false.
            return helixref.g3_install(g, d, way="scaler",
                                       fg=_fg_built(d) if fg is None else fg)
        mod = helixref.install_module(d)
        if hasattr(mod, "install_scaler"):
            c = copy.copy(g)
            c.route = "scaler"
            return helixref.install(c, d)
        bundled = Path(d) / "helixsr-scaler"
        if (bundled / "OptiScaler.dll").is_file():
            sdir, stag = bundled, self.settings.getSetting("scaler_tag", "") or "bundled"
        else:
            sdir, err = self._scaler_dir_or_error()
            if err:
                raise OSError(err)
            stag = self.settings.getSetting("scaler_tag", "")
        here, err = self._scaler_helixsr_check(d)
        if err:
            raise OSError(err)
        try:
            hver = helixscan.version_of(_release_dll(here))
        except OSError:
            hver = "?"
        return _scaler_install_game(g, sdir, here, stag, hver)

    def _scaler_uninstall_one(self, g, d):
        """Remove a scaler install, ours or upstream's. Returns the log."""
        if helixref.generation(d) == 3 and g.target is not None and g.target.is_dir():
            # 2.0+ installs (ours = via the bridge) carry the upstream
            # manifest, not this plugin's marker: let the bridge remove it.
            return helixref.g3_remove(g, d)
        if _read_scaler_marker(g):
            return _scaler_uninstall_game(g)
        mod = helixref.install_module(d)
        if hasattr(mod, "uninstall_scaler") and g.target is not None:
            return helixref.uninstall_upstream_scaler(g.target, d)
        return _scaler_uninstall_game(g)

    @staticmethod
    def _pick_targets(gl, indices):
        """Index validation shared by game ops (no lock: caller holds _ops_lock)."""
        targets = []
        for i in indices:
            if not 1 <= i <= len(gl):
                return None, f"not a game number: {i} (scan found {len(gl)})"
            targets.append(gl[i - 1])
        return targets, ""

    def _scaler_install_run(self, d, indices, extra, fg: bool | None = None):
        mod = helixref.install_module(d)
        if hasattr(mod, "install_scaler") or helixref.generation(d) == 3:
            # Upstream owns the flow (anti-cheat refusals, loader choice,
            # manifest/remover, switching): validate and dispatch per game.
            # 2.0+ goes through its bundled Scaler via the bridge instead of
            # the reference's install_scaler, same dispatch.
            try:
                with self._ops_lock:
                    if not _network_built(d):
                        return {"ok": False, "output": "the network is not built: run Build first"}
                    gl = self._resolve_games(d, extra or [])
                    targets, err = self._pick_targets(gl, indices)
                    if err:
                        return {"ok": False, "output": err}
                    logs = []
                    for g in targets:
                        decky.logger.info(f"HelixSR scaler install {g.name} (route scaler)")
                        try:
                            logs.append(self._scaler_install_one(g, d, fg))
                        except (OSError, SystemExit) as e:
                            return {"ok": False, "output": f"{g.name}: {e}"}
                    self._update_scan_file([(g.name, str(g.folder)) for g in targets], True)
                    self._update_scaler_rows(targets)
                    return {"ok": True, "output": "\n".join(logs)[-8000:]}
            except SystemExit as e:
                return {"ok": False, "output": str(e) or "aborted"}
            except Exception as e:  # noqa: BLE001
                decky.logger.error(f"HelixSR scaler install failed: {e}")
                return {"ok": False, "output": f"{type(e).__name__}: {e}"}
        # Older modern versions (1.9.0-1.9.2) have no upstream scaler flow:
        # eligibility plus this plugin's own installer (separate download).
        try:
            with self._ops_lock:
                gl = self._resolve_games(d, extra or [])
                targets, err = self._pick_targets(gl, indices)
                if err:
                    return {"ok": False, "output": err}
                logs, skipped = [], []
                for g in targets:
                    if getattr(g, "fsr_targets", None):
                        skipped.append(f"{g.name}: has FSR 3.1, use the drop-in instead")
                        continue
                    if not getattr(g, "inputs", None):
                        skipped.append(f"{g.name}: no DLSS/XeSS/FSR2 found")
                        continue
                    decky.logger.info(f"HelixSR scaler install {g.name}")
                    try:
                        logs.append(self._scaler_install_one(g, d))
                    except (OSError, SystemExit) as e:
                        return {"ok": False, "output": f"{g.name}: {e}"}
                if not logs:
                    return {"ok": False, "output": "\n".join(skipped)}
                if skipped:
                    logs.append("skipped: " + "; ".join(skipped))
                self._update_scan_file([(g.name, str(g.folder)) for g in targets], True)
                self._update_scaler_rows(targets)
                return {"ok": True, "output": "\n".join(logs)[-8000:]}
        except SystemExit as e:
            return {"ok": False, "output": str(e) or "aborted"}
        except Exception as e:  # noqa: BLE001
            decky.logger.error(f"HelixSR scaler install failed: {e}")
            return {"ok": False, "output": f"{type(e).__name__}: {e}"}

    def _game_op_run(self, op: str, d: Path, indices: list[int],
                     route: str | None = None, extra: list | None = None) -> dict:
        """Install/uninstall via the reference implementation, in-process (no
        child process, no prompts): fast file copies, guarded by _ops_lock.
        Reuses a fresh scan so every click doesn't re-walk the libraries."""
        try:
            with self._ops_lock:
                if not _network_built(d):
                    return {"ok": False, "output": "the network is not built: run Build first"}
                gl = self._resolve_games(d, extra or [])
                targets, err = self._pick_targets(gl, indices)
                if err:
                    return {"ok": False, "output": err}
                modern = helixref.layout_of(d) != "legacy"
                if op == "install" and route is not None and modern:
                    # Modern installs the drop-in only (legacy installs switch
                    # internally); an explicit legacy route is meaningless, and
                    # games without FSR 3.1 cannot be installed at all.
                    for g in targets:
                        if getattr(g, "fsr_targets", None):
                            continue
                        old = g.old_install() if callable(getattr(g, "old_install", None)) else None
                        if old:
                            return {"ok": False, "output":
                                    f"{g.name}: {old} install can only be removed"}
                        try:
                            why = g.label()
                        except Exception:  # noqa: BLE001
                            why = "not supported by the drop-in"
                        return {"ok": False, "output": f"{g.name}: {why}"}
                skipped: list = []
                if op == "install" and route is not None and not modern:
                    picked = []
                    for g in targets:
                        if route not in helixscan.available_routes(g, d):
                            skipped.append(f"{g.name} cannot use route '{route}'")
                            continue
                        # Same as the reference's per-game question: switch
                        # routes (install() removes the old way first) and
                        # remember OptiScaler where picked. A shallow copy
                        # keeps the scan cache pristine on success and failure.
                        c = copy.copy(g)
                        c.route = route
                        c.chosen = route == "optiscaler" and g.can_opti
                        picked.append(c)
                    if not picked:
                        return {"ok": False, "output": skipped[0] if skipped else "nothing to install"}
                    targets = picked
                if op == "install" and modern:
                    # Batch installs include listed-but-unsupported games; skip
                    # them with a note instead of aborting the whole batch.
                    keep = [g for g in targets if getattr(g, "fsr_targets", None)]
                    skipped += [f"{g.name}: no FSR 3.1 (drop-in needs it)"
                                for g in targets if not getattr(g, "fsr_targets", None)]
                    targets = keep
                    if not targets:
                        return {"ok": False, "output": "skipped: " + "; ".join(skipped)}
                logs = []
                for g in targets:
                    decky.logger.info(f"HelixSR {op} {g.name} (route {g.route})")
                    if op == "install":
                        logs.append(helixref.install(g, d))
                    elif op == "repair":
                        if _scaler_install_info(g):
                            try:
                                logs.append(self._scaler_install_one(g, d))
                            except (OSError, SystemExit) as e:
                                return {"ok": False, "output": f"{g.name}: {e}"}
                        elif g.route == "direct":
                            _repair_direct(g, d)
                            logs.append("")
                        else:
                            logs.append(helixref.install(g, d))
                    else:
                        if _scaler_install_info(g):
                            logs.append(self._scaler_uninstall_one(g, d))
                        else:
                            logs.append(helixref.uninstall(g, d))
                # Note: the cache is intentionally NOT invalidated here. Cached
                # Game objects only resolve index -> folder; installed() and
                # route-relevant markers are re-read live wherever they matter,
                # and mutating game A never changes game B's detection.
                self._update_scan_file([(g.name, str(g.folder)) for g in targets],
                                       op == "install")
                if skipped:
                    logs.append("skipped: " + "; ".join(skipped))
                return {"ok": True, "output": "\n".join(logs)[-8000:]}
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

    def _game_settings_run(self, op: str, d: Path, index: int, values: dict | None,
                             translate: bool = False) -> dict:
        """Read/write a game's helixsr.ini (sharpening, WaveSize...). The key
        schema follows the game's installed HelixSR (1.9.0+ drops the game
        slider, WaveSize auto). With translate=True a stored preset is mapped
        to that schema first (manual sets stay strict)."""
        try:
            with self._ops_lock:
                gl = self._resolve_games(d, self._extra_folders())
                if not 1 <= index <= len(gl):
                    msg = f"not a game number: {index} (scan found {len(gl)})"
                    return {"ok": False, "error" if op == "get" else "output": msg}
                g = gl[index - 1]
                schema = _ini_layout_for(g, d)
                if op == "get":
                    p = _find_ini(g)
                    if p is None:
                        return {"ok": False, "error":
                                "no helixsr.ini found: install HelixSR in this game first"}
                    vals, note = _read_ini_values(p, schema)
                    out = {"ok": True, "path": str(p), "values": vals, "layout": schema}
                    if note:
                        out["note"] = note
                    return out
                if translate:
                    norm, err = _translate_ini_values(values or {}, schema)
                    if err and (values or {}):
                        err = f"stored preset is corrupt: {err}"
                else:
                    norm, err = _normalize_ini_values(values or {}, schema)
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
                    helixref.install_module(d).make_ini(p.parent, None)
                text = p.read_text(encoding="utf-8", errors="replace") if p.is_file() else ""
                sections = INI_SECTIONS_G3 if schema == "g3" else INI_SECTIONS
                for key, val in norm.items():
                    text = _set_ini_value(text, sections[key], INI_OPTION_G3.get(key, key), val)
                p.write_text(text, encoding="utf-8")
                return {"ok": True, "output": f"saved {p}", "path": str(p)}
        except OSError as e:
            msg = f"settings file error: {e}"
            return {"ok": False, "error" if op == "get" else "output": msg}
        except Exception as e:  # noqa: BLE001
            decky.logger.error(f"HelixSR game settings failed: {e}")
            msg = f"{type(e).__name__}: {e}"
            return {"ok": False, "error" if op == "get" else "output": msg}

    # Documented log patterns with one-tap fixes (structure carries more later).
    LOG_FIXES = [
        {"id": "wave64", "match": "wave32",
         "label": "32-lane shaders detected — set WaveSize 64 (RX 7000/9000, OptiScaler games)",
         "settings": {"WaveSize": "64"}},
    ]

    def _game_log_paths(self, g) -> list:
        paths = []
        if g.target is not None:
            for folder in (g.target / "HelixSR-fsrfg", g.target / helixscan.FOLDER):
                if (folder / "helixsr.log").is_file():
                    paths.append(folder / "helixsr.log")
            for name in ("helixsr.log", "helixsr_loader.log"):
                if (g.target / name).is_file():
                    paths.append(g.target / name)
            if _scaler_install_info(g) and (g.target / "OptiScaler.log").is_file():
                paths.append(g.target / "OptiScaler.log")
        for t in g.fsr_targets:
            p = t.parent / "helixsr.log"
            if p.is_file() and p not in paths:
                paths.append(p)
        return paths

    async def get_game_log(self, index: int) -> dict:
        d = await self._active_dir()
        if d is None:
            return {"ok": False, "error": "no HelixSR version downloaded"}
        try:
            idx = int(index)
        except (TypeError, ValueError):
            return {"ok": False, "error": f"bad game number: {index}"}
        extra = self._extra_folders()
        return await asyncio.to_thread(self._game_log_run, d, idx, extra)

    def _game_log_run(self, d: Path, index: int, extra: list) -> dict:
        try:
            with self._ops_lock:
                gl = self._resolve_games(d, extra)
                if not 1 <= index <= len(gl):
                    return {"ok": False, "error": f"not a game number: {index} (scan found {len(gl)})"}
                g = gl[index - 1]
                paths = self._game_log_paths(g)
                if not paths:
                    return {"ok": False, "error": "no helixsr.log yet — launch the game once with HelixSR installed"}
                logs = []
                for p in paths:
                    try:
                        tail = p.read_bytes()[-6000:].decode("utf-8", errors="replace")
                    except OSError as e:
                        tail = f"<unreadable: {e}>"
                    logs.append({"path": str(p), "tail": tail})
                blob = "\n".join(l["tail"] for l in logs).lower()
                if _scaler_install_info(g):
                    # Scaler settings live in OptiScaler.ini [HelixSR], not
                    # helixsr.ini: show the logs, offer no ini fixes.
                    return {"ok": True, "name": g.name, "logs": logs, "fixes": []}
                fixes = [{"id": f["id"], "label": f["label"]}
                         for f in self.LOG_FIXES if f["match"].lower() in blob]
                return {"ok": True, "name": g.name, "logs": logs, "fixes": fixes}
        except Exception as e:  # noqa: BLE001
            decky.logger.error(f"HelixSR game log failed: {e}")
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    async def apply_log_fix(self, index: int, fix_id: str) -> dict:
        d = await self._active_dir()
        if d is None:
            return {"ok": False, "output": "no HelixSR version downloaded"}
        try:
            idx = int(index)
        except (TypeError, ValueError):
            return {"ok": False, "output": f"bad game number: {index}"}
        fix = next((f for f in self.LOG_FIXES if f["id"] == str(fix_id)), None)
        if fix is None:
            return {"ok": False, "output": f"unknown fix: {fix_id}"}
        extra = self._extra_folders()
        cur = await asyncio.to_thread(self._game_log_run, d, idx, extra)
        if not cur.get("ok"):
            return {"ok": False, "output": cur.get("error", "cannot read log")}
        if str(fix_id) not in [f["id"] for f in cur.get("fixes", [])]:
            return {"ok": False, "output": "pattern no longer present in the log"}
        return await asyncio.to_thread(self._game_settings_run, "set", d, idx, dict(fix["settings"]))

    def _scaler_settings_run(self, op: str, d: Path, index: int, values: dict | None) -> dict:
        """Read/write a scaler game's OptiScaler.ini [HelixSR] (Int8, WaveSize
        and the frame-generation keys). Partial-update semantics like
        helixsr.ini. 2.0+'s Scaler renamed FrameGeneration to a preset
        (FGPreset: c = light mode) and re-spelled QuickFlow."""
        try:
            with self._ops_lock:
                gl = self._resolve_games(d, self._extra_folders())
                if not 1 <= index <= len(gl):
                    msg = f"not a game number: {index} (scan found {len(gl)})"
                    return {"ok": False, "error" if op == "get" else "output": msg}
                g = gl[index - 1]
                if g.target is None:
                    return {"ok": False, "error" if op == "get" else "output":
                            "game folder not found"}
                p = g.target / "OptiScaler.ini"
                if not p.is_file():
                    return {"ok": False, "error" if op == "get" else "output":
                            "no OptiScaler.ini found: install HelixSR Scaler in this game first"}
                schema = "g3" if (g.target / "HelixSR-fsrfg").is_dir() else "legacy"
                allowed = SCALER_INI_SETTINGS_G3 if schema == "g3" else SCALER_INI_SETTINGS
                defaults = SCALER_INI_DEFAULTS_G3 if schema == "g3" else SCALER_INI_DEFAULTS
                if op == "get":
                    vals = dict(defaults)
                    try:
                        text = p.read_text(encoding="utf-8", errors="replace")
                        cp = configparser.ConfigParser()
                        cp.optionxform = str
                        cp.read_string(text)
                    except (OSError, configparser.Error):
                        return {"ok": True, "path": str(p), "values": vals, "schema": schema,
                                "note": "unreadable, showing defaults"}
                    try:
                        for key, opts in allowed.items():
                            if cp.has_option(SCALER_INI_SECTION, key):
                                v = cp.get(SCALER_INI_SECTION, key).strip().lower()
                                if v in opts:
                                    vals[key] = v
                    except (ValueError, configparser.Error):
                        pass
                    return {"ok": True, "path": str(p), "values": vals, "schema": schema}
                if not isinstance(values, dict):
                    return {"ok": False, "output": "bad settings"}
                norm = {}
                for key, v in values.items():
                    if key not in allowed:
                        return {"ok": False, "output": f"unknown setting: {key}"}
                    s = str(v).lower()
                    if s not in allowed[key]:
                        return {"ok": False,
                                "output": f"bad {key}: {v!r} ({', '.join(allowed[key])})"}
                    norm[key] = s
                if schema == "g3" and norm.get("QuickFlow") in ("fast", "fastest"):
                    # As the release's own form enforces: QuickFlow runs only
                    # with the full preset (auto = the file's light default;
                    # at runtime it is ignored elsewhere anyway).
                    if "FGPreset" in norm:
                        preset = norm["FGPreset"]
                    else:
                        preset = "c"
                        try:
                            cp = configparser.ConfigParser()
                            cp.optionxform = str
                            cp.read_string(p.read_text(encoding="utf-8", errors="replace"))
                            if cp.has_option(SCALER_INI_SECTION, "FGPreset"):
                                preset = cp.get(SCALER_INI_SECTION, "FGPreset").strip().lower()
                                preset = {"light": "c", "default": "full"}.get(preset, preset)
                        except (OSError, configparser.Error):
                            pass
                    if preset != "full":
                        return {"ok": False, "output": "QuickFlow needs FGPreset full"}
                text = p.read_text(encoding="utf-8", errors="replace")
                for key, val in norm.items():
                    text = _set_ini_value(text, SCALER_INI_SECTION, key, val)
                p.write_text(text, encoding="utf-8")
                return {"ok": True, "output": f"saved {p}", "path": str(p)}
        except OSError as e:
            msg = f"settings file error: {e}"
            return {"ok": False, "error" if op == "get" else "output": msg}
        except Exception as e:  # noqa: BLE001
            decky.logger.error(f"HelixSR scaler settings failed: {e}")
            msg = f"{type(e).__name__}: {e}"
            return {"ok": False, "error" if op == "get" else "output": msg}

    async def get_scaler_settings(self, index: int) -> dict:
        d = await self._active_dir()
        if d is None:
            return {"ok": False, "error": "no HelixSR version downloaded"}
        try:
            idx = int(index)
        except (TypeError, ValueError):
            return {"ok": False, "error": f"bad game number: {index}"}
        return await asyncio.to_thread(self._scaler_settings_run, "get", d, idx, None)

    async def set_scaler_settings(self, index: int, values: dict) -> dict:
        d = await self._active_dir()
        if d is None:
            return {"ok": False, "output": "no HelixSR version downloaded"}
        try:
            idx = int(index)
        except (TypeError, ValueError):
            return {"ok": False, "output": f"bad game number: {index}"}
        if not isinstance(values, dict):
            return {"ok": False, "output": "bad settings"}
        return await asyncio.to_thread(self._scaler_settings_run, "set", d, idx, values)

    def _load_presets(self) -> dict:
        """ini_presets from settings, stripped of corrupt entries."""
        try:
            presets = self.settings.getSetting("ini_presets", {})
        except Exception:  # noqa: BLE001
            return {}
        if not isinstance(presets, dict):
            return {}
        return {str(k): v for k, v in presets.items() if isinstance(v, dict)}

    @staticmethod
    def _sorted_presets(presets: dict) -> dict:
        return {k: presets[k] for k in sorted(presets, key=str.lower)}

    async def get_ini_presets(self) -> dict:
        return {"ok": True, "presets": self._sorted_presets(self._load_presets()),
                "defaults": _schema_defaults(await self._active_layout())}

    async def _active_layout(self) -> str:
        """Settings schema generation of the active version (legacy fallback)."""
        try:
            d = await self._active_dir()
        except Exception:  # noqa: BLE001
            d = None
        if d is None:
            return "legacy"
        try:
            return _schema_of(d)
        except Exception:  # noqa: BLE001
            return "legacy"

    async def save_ini_preset(self, name: str, values: dict) -> dict:
        name = str(name or "").strip()
        if not name or len(name) > 40:
            return {"ok": False, "error": "preset name must be 1-40 characters"}
        if not isinstance(values, dict):
            return {"ok": False, "error": "bad settings"}
        schema = await self._active_layout()
        full, err = _translate_ini_values(values, schema)
        if err:
            return {"ok": False, "error": err}
        presets = self._load_presets()
        existing = next((k for k in presets if k.lower() == name.lower()), None)
        overwrote = existing is not None
        if not overwrote and len(presets) >= MAX_INI_PRESETS:
            return {"ok": False,
                    "error": f"too many presets (max {MAX_INI_PRESETS}): delete one first"}
        if existing is not None and existing != name:
            del presets[existing]
        presets[name] = full
        self.settings.setSetting("ini_presets", presets)
        self.settings.commit()
        return {"ok": True, "presets": self._sorted_presets(presets), "overwrote": overwrote}

    async def delete_ini_preset(self, name: str) -> dict:
        presets = self._load_presets()
        key = str(name)
        if key in presets:
            del presets[key]
        else:
            match = next((k for k in presets if k.lower() == key.lower()), None)
            if match is not None:
                del presets[match]
        self.settings.setSetting("ini_presets", presets)
        self.settings.commit()
        return {"ok": True, "presets": self._sorted_presets(presets)}

    async def apply_ini_preset(self, index: int, name: str) -> dict:
        d = await self._active_dir()
        if d is None:
            return {"ok": False, "output": "no HelixSR version downloaded"}
        try:
            idx = int(index)
        except (TypeError, ValueError):
            return {"ok": False, "output": f"bad game number: {index}"}
        presets = self._load_presets()
        actual = str(name) if str(name) in presets else next(
            (k for k in presets if k.lower() == str(name).lower()), None)
        if actual is None:
            # Built-in Default (shown unless the user saved their own): the
            # target game's schema defaults, translated on write.
            if str(name).lower() != BUILTIN_DEFAULT_PRESET.lower():
                return {"ok": False, "output": f"no preset named {name}"}
            values: dict = {}
        else:
            values = presets[actual]
            if not isinstance(values, dict):
                return {"ok": False, "output": f"preset '{actual}' is corrupt: bad settings"}
        return await asyncio.to_thread(self._game_settings_run, "set", d, idx, values, True)

    async def verify_installs(self) -> dict:
        d = await self._active_dir()
        if d is None:
            return {"ok": False, "error": "no HelixSR version downloaded"}
        extra = self._extra_folders()
        return await asyncio.to_thread(self._verify_run, d, extra)

    async def health_check(self) -> dict:
        """One-screen health: releases reachable, versions healthy, games
        verified, disk OK, plugin update state. Slow parts reuse caches."""
        d = await self._active_dir()
        extra = self._extra_folders()
        return await asyncio.to_thread(self._health_run, d, extra)

    def _health_run(self, d: Path | None, extra: list) -> dict:
        import socket
        checks = []
        # 1. GitHub reachable (TCP only: costs no API rate limit).
        try:
            socket.create_connection(("api.github.com", 443), timeout=5).close()
            checks.append({"name": "GitHub reachable", "ok": True,
                           "detail": "api.github.com:443 connects"})
        except OSError as e:
            checks.append({"name": "GitHub reachable", "ok": False,
                           "detail": f"api.github.com:443 failed: {e}"})
        # 2. Downloaded versions healthy.
        versions = _version_dirs()
        if not versions:
            checks.append({"name": "Versions", "ok": False,
                           "detail": "nothing downloaded yet"})
        else:
            bad = []
            for v in versions:
                tag = _tag_of_dir(v)
                if not _release_dll(v).is_file():
                    bad.append(f"{tag}: HelixSR DLL missing")
                elif not _network_built(v):
                    bad.append(f"{tag}: network not built")
            checks.append({"name": "Versions",
                           "ok": not bad,
                           "detail": f"{len(versions)} downloaded, all healthy" if not bad
                                     else "; ".join(bad)})
        # 3. Installed games verified (cache-aware: fast when fresh).
        if d is None:
            checks.append({"name": "Games", "ok": False, "detail": "no version downloaded"})
        else:
            try:
                with self._ops_lock:
                    gl = self._resolve_games(d, extra)
                    broken, total = [], 0
                    for g in gl:
                        try:
                            installed = g.installed()
                        except Exception:  # noqa: BLE001
                            installed = False
                        if not installed and not self._install_traces(g):
                            continue
                        total += 1
                        if self._verify_game(g):
                            broken.append(g.name)
                checks.append({"name": "Games", "ok": not broken,
                               "detail": f"{total} installed, all verified" if not broken and total
                                         else ("none installed" if not total
                                               else f"broken: {', '.join(broken)}")})
            except Exception as e:  # noqa: BLE001
                checks.append({"name": "Games", "ok": False, "detail": f"scan failed: {e}"})
        # 4. Disk space.
        free_rt = _free_bytes(_helixsr_root())
        home = Path(os.environ.get("DECKY_USER_HOME") or Path.home())
        free_home = _free_bytes(home)
        checks.append({"name": "Disk space",
                       "ok": free_rt > 500_000_000 and free_home > 500_000_000,
                       "detail": f"runtime {free_rt / 1e9:.1f} GB free, home {free_home / 1e9:.1f} GB free"})
        # 5. Plugin update state (cached check, no extra API cost when fresh).
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

    def _install_traces(self, g) -> bool:
        """Marker/backup files proving HelixSR was installed here, even if the
        live files no longer qualify (e.g. a Steam update replaced the DLL)."""
        if _scaler_install_info(g):
            return True
        if g.target is not None and (g.target / helixscan.FOLDER / helixscan.MARKER).is_file():
            return True
        for t in g.fsr_targets:
            if helixscan.original(t).exists():
                return True
            if (t.parent / helixscan.MARKER).is_file():
                return True
        return False

    def _verify_run(self, d: Path, extra: list) -> dict:
        try:
            with self._ops_lock:
                gl = self._resolve_games(d, extra)
                results = []
                for i, g in enumerate(gl, 1):
                    try:
                        installed = g.installed()
                    except Exception:  # noqa: BLE001
                        installed = False
                    sc = _scaler_install_info(g)
                    if sc:
                        installed = True
                    if not installed and not self._install_traces(g):
                        continue
                    problems = self._verify_game(g)
                    results.append({"index": i, "name": g.name,
                                    "route": "scaler" if sc else g.route,
                                    "installed": installed, "ok": not problems,
                                    "problems": problems})
            return {"ok": True, "games": results}
        except Exception as e:  # noqa: BLE001
            decky.logger.error(f"HelixSR verify failed: {e}")
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    def _verify_game(self, g) -> list:
        problems = []
        sc = _scaler_install_info(g)
        if sc:
            if g.target is None:
                return ["game folder not found anymore"]
            loader = sc.get("loader", SCALER_LOADER)
            if not (g.target / loader).is_file():
                problems.append(f"{loader} missing next to the game's exe")
            # HelixSR's DLL: HelixSR-fsrfg/amd_fidelityfx_dx12.dll on 2.0+
            # installs, HelixSR/helixsr.dll before.
            if not (_release_dll(g.target).is_file()
                    or (g.target / helixscan.FOLDER / "helixsr.dll").is_file()):
                problems.append("HelixSR DLL missing")
            if not (g.target / "OptiScaler.ini").is_file():
                problems.append("OptiScaler.ini missing next to the game's exe")
            for rel in (sc.get("backup", {}) or {}).values():
                if not (g.target / helixscan.FOLDER / rel).exists():
                    problems.append(f"backup {rel} missing")
            return problems
        if g.route == "direct":
            if not g.fsr_targets:
                return ["no FSR DLL found anymore"]
            for t in g.fsr_targets:
                orig = helixscan.original(t)
                if not t.exists():
                    problems.append(f"missing {t.name} (game updated?)")
                elif not helixscan.is_helixsr(t):
                    problems.append(f"{t.name} is no longer HelixSR (game updated?)")
                if not orig.exists():
                    problems.append(f"backup {orig.name} missing")
        elif g.route == "optiscaler":
            if g.target is None:
                return ["game folder not found anymore"]
            folder = g.target / helixscan.FOLDER
            marker = folder / helixscan.MARKER
            if not marker.is_file():
                problems.append("HelixSR folder record missing")
                return problems
            try:
                rec = json.loads(marker.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return ["install record unreadable"]
            opti = rec.get("optiscaler", "dxgi.dll") if isinstance(rec, dict) else "dxgi.dll"
            if not (g.target / opti).exists():
                problems.append(f"{opti} missing next to the game's exe")
            for n in (helixscan.OWN, helixscan.OWN_SR):
                if not (folder / n).is_file():
                    problems.append(f"{n} missing in HelixSR folder")
            if not (g.target / "OptiScaler.ini").is_file():
                problems.append("OptiScaler.ini missing next to the game's exe")
        elif g.route == "dlss":
            if g.target is None:
                return ["game folder not found anymore"]
            if not helixscan.is_loader(g.target / helixscan.LOADER):
                problems.append(f"{helixscan.LOADER} missing or replaced next to the game's exe")
            if not helixscan.is_helixsr(g.target / helixscan.CORE):
                problems.append(f"{helixscan.CORE} missing or replaced next to the game's exe")
        elif g.route in ("unsupported", None):
            problems.append("not supported by the drop-in (needs HelixSR Scaler)")
        else:
            problems.append(f"unknown route {g.route}")
        return problems

    async def factory_reset(self) -> dict:
        busy = self._dl["running"] or self._sc["running"] or self._scan["running"] \
            or self._mig["running"] or self._pu["running"] \
            or bool(self._build_thread and self._build_thread.is_alive())
        if busy:
            return {"ok": False, "error": "finish the running task first"}
        d = await self._active_dir()
        extra = self._extra_folders()
        return await asyncio.to_thread(self._factory_reset_run, d, extra)

    def _factory_reset_run(self, d: Path | None, extra: list) -> dict:
        uninstalled, failed = [], []
        try:
            with self._ops_lock:
                if d is not None:
                    gl, _skipped, _log = helixref.scan(d, extra)
                    for g in gl:
                        try:
                            if not g.installed():
                                continue
                        except Exception:  # noqa: BLE001
                            continue
                        try:
                            helixref.uninstall(g, d)
                            uninstalled.append(g.name)
                        except Exception as e:  # noqa: BLE001
                            failed.append(f"{g.name}: {e}")
                removed, skipped, freed = [], [], 0
                still_used: dict[str, list] = {}
                if d is not None:
                    gl2, _skipped, _log = helixref.scan(d, extra)
                    for g in gl2:
                        try:
                            if not g.installed():
                                continue
                        except Exception:  # noqa: BLE001
                            continue
                        for v in _marker_versions(g):
                            still_used.setdefault(v, []).append(g.name)
                for v in _version_dirs():
                    tag = _tag_of_dir(v)
                    try:
                        dll_version = helixscan.version_of(_release_dll(v))
                    except OSError:
                        dll_version = "?"
                    users = still_used.get(dll_version, []) if dll_version != "?" else []
                    if users:
                        skipped.append(f"{tag} (still used by {', '.join(users)})")
                        continue
                    freed += _dir_size_cached(v)
                    shutil.rmtree(v, ignore_errors=True)
                    removed.append(tag)
                try:
                    self._scan_cache_path().unlink(missing_ok=True)
                except OSError:
                    pass
                self._scan_cache = _empty_scan_cache()
                self.settings.setSetting("active_tag", "")
                self.settings.setSetting("extra_folders", [])
                self.settings.commit()
                return {"ok": True, "games_uninstalled": uninstalled, "games_failed": failed,
                        "versions_removed": removed, "versions_skipped": skipped,
                        "freed_mb": round(freed / 1e6, 1),
                        "note": "ini presets were kept"}
        except Exception as e:  # noqa: BLE001
            decky.logger.error(f"HelixSR factory reset failed: {e}")
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    async def start_build(self, rebuild: bool = False, fg: bool = False) -> dict:
        """Build the network: a background thread first ensures NVIDIA's DLSS
        DLL (downloaded with the plugin's own TLS, verified by hash), then the
        reference setup runs with --dlss so its Python never needs HTTPS.
        Game prompts default to no on EOF; use install_games after. For
        1.9.0+ frame generation is skipped (--no-fg) unless fg is set: it
        serves HelixSR Scaler, not the drop-in this plugin installs.
        2.0+ releases run setup/helixsr_setup.py directly (their bash wrapper
        ends in a game-install step that --yes would answer into every game)."""
        thread_alive = bool(self._build_thread and self._build_thread.is_alive())
        if thread_alive:
            return {"ok": False, "error": "build already running"}
        d = await self._active_dir()
        if d is None:
            return {"ok": False, "error": "no HelixSR version downloaded"}
        if fg and helixref.layout_of(d) != "modern":
            return {"ok": False, "error": "frame generation needs HelixSR 1.9.0+"}
        if fg and not helixref.fg_engine_available(d):
            return {"ok": False, "error": "this HelixSR has no frame generation engine "
                                          "(withdrawn in 1.9.3+; alternative providers live in Scaler)"}
        net = _network_built(d)
        if not rebuild and net and (_fg_built(d) or not fg):
            return {"ok": False, "error": "the network is already built — nothing to do"}
        # A 2.0+ release's bridge caches its --status verdicts for the life
        # of the module: drop it so the gates see the build's result.
        helixref.reset_bridge(d)
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
        self._build_cancel.clear()
        self._build_running = True
        self._build_thread = threading.Thread(
            target=self._build_run, args=(d, log, header, bash, str(script), bool(rebuild), bool(fg)),
            daemon=True)
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

    def _build_run(self, d: Path, log: Path, header: str, bash: str, script: str, rebuild: bool,
                     fg: bool = False):
        """Phase 1 (worker thread): ensure the DLSS SDK DLL (plus the frame
        generation DLL when fg is set), with progress to the build log.
        Phase 2 is scheduled back on the event loop."""
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
                cancelled = False
                try:
                    with r, open(dlss, "wb") as f:
                        while True:
                            if not self._alive:
                                self._build_running = False
                                return
                            if self._build_cancel.is_set():
                                cancelled = True
                                break
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
                if cancelled:
                    dlss.unlink(missing_ok=True)
                    self._finish_build(log, False, -1, "build cancelled during the DLSS download")
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
            dlssg = None
            dlssg2 = None
            py_tool = dxc = None
            if fg:
                gen3 = helixref.generation(d) == 3
                pins = [(DLSSG_FILE, DLSSG_SHA256, DLSSG_SL_URL, "nvngx_dlssg.dll")]
                if gen3:
                    pins.append((DLSSG2_FILE, DLSSG2_SHA256, DLSSG2_SL_URL,
                                 "nvngx_dlssg.dll (310.9.16, light mode)"))
                for fname, fsha, furl, flabel in pins:
                    fpath = d / fname
                    if fpath.is_file() and _sha256_file(fpath) == fsha:
                        line(f"[helixsr] reusing verified {fname}")
                    else:
                        if fpath.is_file():
                            line(f"[helixsr] existing {fname} failed checksum, re-downloading")
                            fpath.unlink()
                        line("[helixsr] HelixSR needs NVIDIA's DLSS Frame Generation DLL to build frame generation.")
                        line(f"[helixsr] fetching {DLSSG_ENTRY} (~8 MB) from NVIDIA's Streamline SDK ({furl}): {flabel}.")
                        line("[helixsr] the DLL is NVIDIA's, under NVIDIA's license; used once to build, for this PC only.")
                        try:
                            fpath.write_bytes(_fetch_zip_entry(furl, DLSSG_ENTRY, fsha))
                        except OSError as e:
                            fpath.unlink(missing_ok=True)
                            line(f"[helixsr] DLSS-G download failed: {e}")
                            self._finish_build(log, False, -1, "setup failed: dlssg download failed")
                            return
                        line(f"[helixsr] {fname} downloaded and verified")
                dlssg = d / DLSSG_FILE
                if gen3:
                    dlssg2 = d / DLSSG2_FILE
                # Intel XeLL pre-seed: the setup skips its own download when the
                # hash matches, and falls back to its own wait otherwise.
                fgdir = d / "HelixSR-fsrfg" / "helixsr-fg" if gen3 else d / "helixsr-fg"
                if fgdir.is_dir():
                    xell = fgdir / "libxell.dll"
                    if xell.is_file() and _sha256_file(xell) == XESS_SHA256:
                        line("[helixsr] reusing verified libxell.dll")
                    else:
                        try:
                            xell.write_bytes(_fetch_zip_entry(XESS_URL, XESS_ENTRY, XESS_SHA256))
                            line("[helixsr] libxell.dll downloaded and verified (pre-seeded for the setup)")
                        except OSError as e:
                            line(f"[helixsr] XeLL download failed ({e}): the setup falls back to its own wait")
                line("[helixsr] NGX core: fetched by the setup itself (needs the zstd program or Python 3.14); "
                     "without it frame generation stays unavailable, upscaling is unaffected")
            if helixref.generation(d) == 3:
                # Provision what the release's helixsr-setup.sh would: a
                # Python with numpy and the DXC shader compiler through Wine
                # (the network build needs the compiler too, not just FG).
                # The plugin runs setup/helixsr_setup.py directly — the bash
                # wrapper's trailing game step would answer every game
                # with --yes.
                try:
                    py_tool, dxc = _ensure_g3_toolchain(d, line, self._build_cancel)
                except OSError as e:
                    line(f"[helixsr] build toolchain setup failed: {e}")
                    self._finish_build(log, False, -1, "setup failed: toolchain setup failed")
                    return
            if not self._alive:
                self._build_running = False
                return
            if self._build_cancel.is_set():
                self._finish_build(log, False, -1, "build cancelled before setup started")
                return
            fut = asyncio.run_coroutine_threadsafe(
                self._spawn_and_watch(d, log, header, bash, script, dlss, rebuild,
                                      dlssg, dlssg2, py_tool, dxc), self.loop)
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

    @staticmethod
    def _build_args(d: Path, rebuild: bool, dlss: Path, dlssg: Path | None = None) -> list:
        args = ["--yes"]
        if rebuild:
            args.append("--rebuild")
        modern = helixref.layout_of(d) == "modern"
        if modern and dlssg is None:
            args.append("--no-fg")
        args += ["--dlss", str(dlss)]
        if dlssg is not None and modern:
            args += ["--dlssg", str(dlssg)]
        return args

    async def _spawn_and_watch(self, d: Path, log: Path, header: str,
                               bash: str, script: str, dlss: Path, rebuild: bool,
                               dlssg: Path | None = None, dlssg2: Path | None = None,
                               py_tool: str | None = None, dxc: Path | None = None):
        gen3 = helixref.generation(d) == 3
        if gen3:
            if py_tool is None or dxc is None:
                self._build_log_line(log, "[helixsr] build toolchain missing — cannot run the setup directly")
                try:
                    await decky.emit("helixsr_build_done", False, -1)
                except Exception:  # noqa: BLE001 (e.g. unloading)
                    pass
                self._build_running = False
                return
            # Run the release's setup module directly (phase 1 provisioned
            # Python + the DXC shim): the release's bash wrapper ends in a
            # game-install step that --yes would answer into every game
            # (2.0+ behavior; 1.9.x aborted on EOF instead).
            net = _network_built(d)
            args = [str(py_tool), "-I", str(d / "setup" / "helixsr_setup.py"), str(d),
                    "--dxc", str(dxc), "--yes"]
            if rebuild:
                args.append("--rebuild")
            elif net:
                args.append("--fg-only")
            if dlssg is None:
                args.append("--no-fg")
            args += ["--dlss", str(dlss)]
            if dlssg is not None:
                args += ["--dlssg", str(dlssg)]
            if dlssg2 is not None:
                args += ["--dlssg", str(dlssg2)]
        else:
            args = [bash, script] + self._build_args(d, rebuild, dlss, dlssg)
        if "--no-fg" in args:
            self._build_log_line(log, "[helixsr] frame generation skipped (--no-fg): drop-in only")
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
            ok = rc == 0 or (gen3 and rc == 3)
            if gen3:
                # The wrapper deletes its temporary Wine prefix after the run.
                shutil.rmtree(_g3_data_dir() / "wineprefix", ignore_errors=True)
                helixref.reset_bridge(d)
            if ok:
                dlss.unlink(missing_ok=True)
                if dlssg is not None:
                    dlssg.unlink(missing_ok=True)
                if dlssg2 is not None:
                    dlssg2.unlink(missing_ok=True)
                self._build_log_line(log, "[helixsr] network built; once-used nvngx_dlss.dll removed")
                if gen3 and rc == 3:
                    self._build_log_line(log, "[helixsr] upscaling is built; frame generation failed "
                                              "(the log above says why) — run Build again to retry it")
            elif self._build_cancel.is_set():
                self._build_log_line(log, "[helixsr] build cancelled; nvngx_dlss.dll kept for retry")
            else:
                self._build_log_line(log, "[helixsr] setup failed; nvngx_dlss.dll kept for retry")
            self._build_log_line(log, f"[helixsr] setup exited rc={rc}")
            try:
                await decky.emit("helixsr_build_done", ok, rc)
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
                "network_built": bool(d and _network_built(d)),
                "fg_built": bool(d and _fg_built(d))}

    async def cancel_build(self) -> dict:
        """Ask a running network build to stop: aborts the plugin's DLSS/FG
        pre-download or terminates the setup script (rc reflects the signal);
        poll get_build_status(). Same paths the plugin unload uses."""
        if not (self._build_thread and self._build_thread.is_alive()):
            return {"ok": False, "error": "no build running"}
        proc, self._build_proc = self._build_proc, None
        if proc is not None and proc.returncode is None:
            try:
                proc.terminate()
            except ProcessLookupError:
                pass
        self._build_cancel.set()
        return {"ok": True}

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
        self._dl_stop = threading.Event()
        self._sc = _new_sc_state()
        self._sc_stop = threading.Event()
        self._scan = _new_scan_state()
        self._mig = _new_mig_state()
        self._update_check = {"at": 0.0, "result": {"ok": True, "update_available": False}}
        self._pu = {"running": False, "done": False, "ok": False, "cancelled": False, "error": "",
                    "version": "", "url": "", "downloaded": 0, "total": 0,
                    "mode": "", "path": ""}
        self._pu_stop = threading.Event()
        self._update_pending = ""
        self._scan_stop = threading.Event()
        self._scan_cache = _empty_scan_cache()
        self._ops_lock = threading.Lock()
        self._build_proc = None
        self._build_cancel = threading.Event()
        decky.logger.info("HelixSR Muse Plugin loaded")

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
        # Renamed in v0.0.15 (was helixsr-decky-plugin-muse): carry settings
        # and downloads over to the new per-plugin directories. One-shot: the
        # old locations are removed by the migrate calls. Uninstall the old
        # plugin entry afterwards; its code directory is unaffected by this.
        old_base = "helixsr-decky-plugin-muse"
        decky.migrate_settings(os.path.join(decky.DECKY_HOME, "settings", old_base))
        decky.migrate_runtime(os.path.join(decky.DECKY_HOME, "data", old_base))
