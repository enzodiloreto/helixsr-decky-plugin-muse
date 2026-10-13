"""Shared fixtures for backend tests (stdlib only).

Run:  python3 -m unittest discover -s tests -v   (from the repo root)
"""
import struct
import sys
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "py_modules"))


def install_decky_stub(emitted=None):
    """Stub the `decky` module Decky Loader provides on device."""
    decky = types.ModuleType("decky")

    class _Log:
        def info(self, *a):
            pass

        def warning(self, *a):
            pass

        def error(self, *a):
            pass

    decky.logger = _Log()

    async def _emit(*a):
        if emitted is not None:
            emitted.append(a)

    decky.emit = _emit
    sys.modules["decky"] = decky
    return decky


class StaticSettings:
    """SettingsManager stand-in with a fixed active_tag."""

    def __init__(self, active=""):
        self._s = {"active_tag": active}

    def getSetting(self, k, d=""):
        return self._s.get(k, d)

    def setSetting(self, k, v):
        self._s[k] = v

    def commit(self):
        pass


def make_helixsr_dir(base: Path, tag="1.8.1", network=True) -> Path:
    """Fake HelixSR version folder with everything scan/install needs."""
    here = base / f"HelixSR-{tag}"
    (here / "optiscaler").mkdir(parents=True)
    (here / "amd-fsr").mkdir()
    (here / "dlss-loader").mkdir()
    dll = b"MZ" + b"\x00" * 100 + f"\x00HelixSR {tag}\x00".encode() + b"\x00" * 100
    if network:
        dll += b"W" * 64 + b"P" * 64 + b"HXSRNET1" + struct.pack("<QQQ", 64, 64, 1)
    (here / "amd_fidelityfx_dx12.dll").write_bytes(dll)
    (here / "optiscaler" / "OptiScaler.dll").write_bytes(b"OptiScaler fake")
    (here / "optiscaler" / "OptiScaler.ini").write_text(
        "[Upscalers]\nDx12Upscaler = none\n[Libraries]\n"
        "FfxDx12Path = x\nFfxDx12SRPath = y\nFfxDx12FGPath = z\n")
    for n in ("fakenvapi.dll", "fakenvapi.ini"):
        (here / "optiscaler" / n).write_bytes(b"fake")
    (here / "amd-fsr" / "amd_fidelityfx_upscaler_dx12.dll").write_bytes(b"AMD FSR fake")
    (here / "amd-fsr" / "amd_fidelityfx_framegeneration_dx12.dll").write_bytes(b"AMD FG fake")
    (here / "dlss-loader" / "dxgi.dll").write_bytes(b"HelixSR DLSS loader fake")
    (here / "helixsr-setup.sh").write_text("#!/bin/sh\n")
    (here / "helixsr-uninstall.sh").write_text("#!/bin/sh\n")
    return here


def make_steam_home(base: Path) -> Path:
    """Fake HOME with a Steam root whose libraryfolders.vdf points at lib/.

    GameA: FSR 3.1 game (direct route). GameB: DLSS game (optiscaler route).
    GameC: no compatible DLLs (excluded from scans).
    """
    home = base / "home"
    lib = home / ".local/share/lib"
    common = lib / "steamapps" / "common"
    ga = common / "GameA"
    (ga / "bin").mkdir(parents=True)
    (ga / "bin" / "amd_fidelityfx_upscaler_dx12.dll").write_bytes(b"FAKE AMD FSR DLL v3.1")
    (ga / "bin" / "game.exe").write_bytes(b"\x00" * (1 << 20))
    gb = common / "GameB"
    gb.mkdir(parents=True)
    (gb / "nvngx_dlss.dll").write_bytes(b"fake dlss")
    (gb / "game.exe").write_bytes(b"\x00" * (1 << 20))
    gc = common / "GameC"
    gc.mkdir(parents=True)
    (gc / "game.exe").write_bytes(b"\x00" * 100)
    steamroot = home / ".local/share/Steam" / "steamapps"
    steamroot.mkdir(parents=True)
    (steamroot / "libraryfolders.vdf").write_text(
        '"libraryfolders"\n{\n\t"0"\n\t{\n\t\t"path"\t\t"%s"\n\t}\n}\n' % lib)
    return home
