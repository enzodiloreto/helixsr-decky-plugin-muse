"""Plugin RPC flows with a stubbed decky module and a stub HelixSR bridge.

The stub (STUB_BRIDGE) speaks the release's JSON CLI protocol so every
game operation is exercised end to end without touching upstream files.
"""
import asyncio
import json
import os
import shutil
import stat
import sys
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from _common import REPO, StaticSettings, install_decky_stub

emitted = []
install_decky_stub(emitted)

import main
import bridge


def _mode(p):
    return stat.S_IMODE(os.stat(p).st_mode)


STUB_BRIDGE = r'''
import json, sys
from pathlib import Path
rel = Path(sys.argv[1]); cmd = sys.argv[2]
pairs = sys.argv[3:]
a = {}
i = 0
bad = None
while i < len(pairs):
    k = pairs[i]
    if not k.startswith("--") or i + 1 >= len(pairs):
        bad = k
        break
    k = k[2:]
    if k == "folder":
        a.setdefault("folder", []).append(pairs[i + 1])
    else:
        a[k] = pairs[i + 1]
    i += 2
st_file = rel / ".stub-state.json"
try:
    st = json.loads(st_file.read_text())
except Exception:
    st = {"installs": {}, "settings": {}}
def save():
    st_file.write_text(json.dumps(st))
def base_games():
    try:
        return json.loads((rel / ".stub-games.json").read_text())
    except Exception:
        return []
def games_list():
    out = []
    for g in base_games():
        g = dict(g)
        inst = st["installs"].get(g["folder"])
        g["installed"] = inst["way"] if inst else None
        g["version"] = inst["version"] if inst else None
        out.append(g)
    return out
def find(folder):
    for g in games_list():
        if g["folder"] == folder:
            return g
    return None
out = {"protocol": 1, "ok": False}
if bad is not None:
    out = {"protocol": 1, "ok": False, "error": f"bad argument: {bad}"}
elif cmd == "status":
    try:
        s = json.loads((rel / ".stub-status.json").read_text())
    except Exception:
        s = {}
    out = {"protocol": 1, "ok": True, "version": s.get("version", "9.9.9"),
           "network_ready": s.get("network_ready", True),
           "fg_ready": s.get("fg_ready", False),
           "fg_reason": s.get("fg_reason", ""), "release": str(rel)}
elif cmd == "games":
    out = {"protocol": 1, "ok": True, "games": games_list()}
elif cmd == "install":
    g = find(a.get("game", ""))
    if g is None:
        out = {"protocol": 1, "ok": False, "error": "no game (.exe) found"}
    else:
        way = a.get("way") or "auto"
        if way == "auto":
            way = g.get("recommended") or "scaler"
        if way not in ("scaler", "fsr"):
            out = {"protocol": 1, "ok": False, "error": "bad way"}
        elif a.get("fg") not in (None, "on", "off"):
            out = {"protocol": 1, "ok": False, "error": "--fg must be on or off"}
        elif way == "scaler" and not a.get("scaler"):
            out = {"protocol": 1, "ok": False, "error": "HelixSR Scaler has not been downloaded yet"}
        else:
            st["installs"][g["folder"]] = {"way": way, "version": "9.9.9"}
            save()
            g["installed"] = way
            g["version"] = "9.9.9"
            out = {"protocol": 1, "ok": True, "game": g}
elif cmd == "remove":
    g = find(a.get("game", ""))
    if g is None or g["folder"] not in st["installs"]:
        out = {"protocol": 1, "ok": False, "error": "HelixSR is not installed"}
    else:
        del st["installs"][g["folder"]]
        save()
        g["installed"] = None
        g["version"] = None
        out = {"protocol": 1, "ok": True, "game": g}
elif cmd == "scaler-fg":
    g = find(a.get("game", ""))
    if g is None or st["installs"].get(g["folder"], {}).get("way") != "scaler":
        out = {"protocol": 1, "ok": False, "error": "HelixSR Scaler is not installed"}
    elif a.get("on") not in ("true", "false"):
        out = {"protocol": 1, "ok": False, "error": "--on must be true or false"}
    else:
        g["frame_generation"] = (a["on"] == "true")
        out = {"protocol": 1, "ok": True, "game": g}
elif cmd == "settings-get":
    g = find(a.get("game", ""))
    if g is None or not g.get("installed"):
        out = {"protocol": 1, "ok": False, "error": "HelixSR is not installed"}
    else:
        kind = "scaler" if g["installed"] == "scaler" else "helixsr"
        out = {"protocol": 1, "ok": True, "kind": kind,
               "path": g.get("settings_path") or "/tmp/x.ini", "exists": True,
               "values": dict(st["settings"].get(g["folder"], {})), "other": {}}
elif cmd == "settings-set":
    g = find(a.get("game", ""))
    if g is None or not g.get("installed"):
        out = {"protocol": 1, "ok": False, "error": "HelixSR is not installed"}
    else:
        try:
            values = json.loads(a.get("values", ""))
        except Exception:
            values = None
        if not isinstance(values, dict):
            out = {"protocol": 1, "ok": False, "error": "--values is not JSON"}
        else:
            cur = dict(st["settings"].get(g["folder"], {}))
            cur.update(values)
            st["settings"][g["folder"]] = cur
            save()
            out = {"protocol": 1, "ok": True,
                   "kind": "scaler" if g["installed"] == "scaler" else "helixsr",
                   "path": g.get("settings_path"), "exists": True,
                   "values": cur, "other": {}}
elif cmd == "reset-settings":
    g = find(a.get("game", ""))
    if g is None or not g.get("installed"):
        out = {"protocol": 1, "ok": False, "error": "HelixSR is not installed"}
    else:
        st["settings"].pop(g["folder"], None)
        save()
        out = {"protocol": 1, "ok": True, "game": g}
elif cmd == "log":
    g = find(a.get("game", ""))
    if g is None:
        out = {"protocol": 1, "ok": False, "error": "no game"}
    else:
        out = {"protocol": 1, "ok": True,
               "files": [{"path": g["folder"] + "/helixsr.log", "lines": 2,
                          "tail": ["wave32 detected in shader", "summary: ok"]}],
               "important": ["wave32 detected in shader"], "report": "# report"}
elif cmd == "rollback":
    g = find(a.get("game", ""))
    if g is None:
        out = {"protocol": 1, "ok": False, "error": "no game"}
    else:
        out = {"protocol": 1, "ok": True, "game": g}
else:
    out = {"protocol": 1, "ok": False, "error": "unknown command"}
out["messages"] = ["stub note"]
sys.stdout.write(json.dumps(out) + "\n")
sys.stdout.flush()
sys.exit(0 if out["ok"] else 1)
'''

STUB_SETUP = r'''
import os
import sys
# argv: OUT [--dxc X] [--fg-only] [--yes] [--no-fg] [--dlss X] [--dlssg X]...
args = sys.argv[1:]
if "--status" in args:
    import json
    from pathlib import Path
    rel = Path(args[0]).parent if Path(args[0]).name == "HelixSR-fsrfg" else Path(args[0])
    print("network=1 fg=0")
    sys.exit(0)
log = None
for i, x in enumerate(args):
    if x == "--log" and i + 1 < len(args):
        log = args[i + 1]
rc = 0
for x in args:
    if x.startswith("rc="):
        rc = int(x[3:])
if log:
    open(log, "a").write("stub setup ran\n")
# the CA bundle the plugin hands to the setup's Python (stdout is the build log)
print("ssl_cert_file=%s" % os.environ.get("SSL_CERT_FILE", ""))
print("stub setup ran")
sys.exit(rc)
'''


class PluginCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="hxplug2-"))
        self._saved_env = {k: os.environ.get(k) for k in
                           ("DECKY_PLUGIN_RUNTIME_DIR", "DECKY_USER_HOME",
                            "DECKY_PLUGIN_DIR", "HELIXSR_SETUP_JOBS")}
        os.environ["DECKY_PLUGIN_RUNTIME_DIR"] = str(self.tmp / "runtime")
        os.environ["DECKY_USER_HOME"] = str(self.tmp / "home")
        os.environ["DECKY_PLUGIN_DIR"] = str(REPO)
        os.environ.pop("HELIXSR_SETUP_JOBS", None)
        emitted.clear()
        main._status_cache.clear()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _plugin(self, active=""):
        p = main.Plugin()
        p.settings = StaticSettings(active)
        p._alive = True
        p._dl = main._new_dl_state()
        p._dl_stop = threading.Event()
        p._build_log = None
        p._build_proc = None
        p._build_cancel = threading.Event()
        p._build_lock = threading.Lock()
        p._scan = main._new_scan_state()
        p._scan_stop = threading.Event()
        p._scan_cache = main._empty_scan_cache()
        p._scan_lock = threading.Lock()
        p._update_check = {"at": 0.0, "result": {"ok": True, "update_available": False}}
        p._pu = {"running": False, "done": False, "ok": False, "error": "",
                 "version": "", "url": "", "downloaded": 0, "total": 0,
                 "mode": "", "path": ""}
        p._pu_stop = threading.Event()
        p._update_pending = ""
        return p

    async def _wait_done(self, getter, cap=400):
        for _ in range(cap):
            st = await getter()
            if st.get("done"):
                return st
            await asyncio.sleep(0.05)
        raise AssertionError("timed out waiting for completion")

    def _release_dir(self, tag="2.0.0", status=None, games=None):
        """Fake 2.0+ release: stub bridge + DLL stamp + bundled scaler."""
        d = Path(os.environ["DECKY_PLUGIN_RUNTIME_DIR"]) / "helixsr" / f"HelixSR-{tag}"
        (d / "setup").mkdir(parents=True)
        (d / "setup" / "helixsr_bridge.py").write_text(STUB_BRIDGE)
        (d / "setup" / "helixsr_setup.py").write_text(STUB_SETUP)
        dll = b"MZ" + b"\x00" * 64 + f"\x00HelixSR {tag}\x00".encode() + b"\x00" * 64
        (d / "HelixSR-fsrfg").mkdir()
        (d / "HelixSR-fsrfg" / "amd_fidelityfx_dx12.dll").write_bytes(dll)
        (d / "helixsr-setup.sh").write_text(
            "#!/bin/sh\nPY_URL='https://example.invalid/py.tgz'\n"
            "PY_SHA='%s'\nDXCWIN_URL='https://example.invalid/dxc.zip'\n"
            "DXCWIN_SHA='%s'\n" % ("0" * 64, "1" * 64))
        sc = d / "helixsr-scaler"
        sc.mkdir()
        (sc / "OptiScaler.dll").write_bytes(b"stub HelixSR Scaler v1.1.0 end")
        (sc / "OptiScaler.ini").write_text("[HelixSR]\nWaveSize = auto\n")
        if status is not None:
            (d / ".stub-status.json").write_text(json.dumps(status))
        if games is not None:
            (d / ".stub-games.json").write_text(json.dumps(games))
        return d

    def _game(self, folder, name="Game", **kw):
        g = {"id": folder, "name": name, "folder": folder,
             "inputs": ["DLSS"], "inputs_found": True,
             "ways": ["scaler", "fsr"], "recommended": "scaler",
             "anticheat": [], "installed": None, "version": None,
             "settings_path": folder + "/OptiScaler.ini",
             "log_path": folder + "/helixsr.log",
             "supported": True, "blocked": None, "exe_folder": folder,
             "scaler_version": None, "scaler_dll": "dxgi.dll",
             "scaler_layout": "new", "launch_options": None,
             "frame_generation": None, "fg_possible": True, "fg_other": False,
             "fg_installed": False, "fg_unsupported": False,
             "scaler_missing": [], "scaler_seen": None, "previous": None,
             "game_fg": []}
        g.update(kw)
        Path(folder).mkdir(parents=True, exist_ok=True)
        return g

    def test_scan_lists_only_compatible_games(self):
        """The release's scan lists every game with an .exe; only the games
        HelixSR can use are listed."""
        d = self._release_dir()
        fsr = self._game(str(self.tmp / "games" / "FSR"), name="FSR",
                         ways=["scaler", "fsr"], recommended="scaler")
        dlss = self._game(str(self.tmp / "games" / "DLSS"), name="DLSS",
                          ways=["scaler"], recommended="scaler")
        # no upscaler file at all: nothing for HelixSR to take over
        plain = self._game(str(self.tmp / "games" / "Plain"), name="Plain",
                           ways=["scaler"], recommended="scaler",
                           inputs=[], inputs_found=False)
        # anti-cheat without FSR 3.1: only the Scaler could work -> not listed
        ac = self._game(str(self.tmp / "games" / "AC"), name="AC",
                        ways=["scaler"], recommended=None,
                        blocked="anti-cheat", anticheat=["EasyAntiCheat"])
        # anti-cheat with FSR 3.1: the drop-in is fine
        ac_fsr = self._game(str(self.tmp / "games" / "ACFSR"), name="ACFSR",
                            ways=["scaler", "fsr"], recommended="fsr",
                            anticheat=["EasyAntiCheat"])
        (d / ".stub-games.json").write_text(
            json.dumps([fsr, dlss, plain, ac, ac_fsr]))
        p = self._plugin(active="v2.0.0")

        async def go():
            p.loop = asyncio.get_event_loop()
            self.assertEqual(await p.start_scan(), {"ok": True})
            st = await self._wait_done(p.get_scan_status)
            self.assertTrue(st["ok"], st["error"])
            names = [g["name"] for g in st["games"]]
            self.assertEqual(names, ["FSR", "DLSS", "ACFSR"])
            self.assertEqual([g["index"] for g in st["games"]], [1, 2, 3])
            cached = await p.get_last_scan()
            self.assertEqual([g["name"] for g in cached["games"]], ["FSR", "DLSS", "ACFSR"])
        asyncio.run(go())

    def test_scan_keeps_games_with_an_install(self):
        d = self._release_dir()
        g1 = self._game(str(self.tmp / "games" / "Old"), name="Old",
                        ways=["scaler"], recommended="scaler",
                        inputs=[], inputs_found=False)
        (d / ".stub-games.json").write_text(json.dumps([g1]))
        # the stub reports the install from its own state
        (d / ".stub-state.json").write_text(json.dumps(
            {"installs": {g1["folder"]: {"way": "scaler", "version": "9.9.9"}},
             "settings": {}}))
        p = self._plugin(active="v2.0.0")

        async def go():
            p.loop = asyncio.get_event_loop()
            await p.start_scan()
            st = await self._wait_done(p.get_scan_status)
            self.assertEqual([g["name"] for g in st["games"]], ["Old"])
        asyncio.run(go())

    def test_install_refuses_scaler_in_anticheat_game(self):
        d = self._release_dir()
        g1 = self._game(str(self.tmp / "games" / "AC"), name="AC",
                        ways=["scaler", "fsr"], recommended="fsr",
                        anticheat=["EasyAntiCheat"])
        (d / ".stub-games.json").write_text(json.dumps([g1]))
        p = self._plugin(active="v2.0.0")

        async def go():
            p.loop = asyncio.get_event_loop()
            # the scan knows the game's anti-cheat
            await p.start_scan()
            await self._wait_done(p.get_scan_status)
            r = await p.install_games([{"folder": g1["folder"], "way": "scaler"}])
            self.assertFalse(r["ok"])
            self.assertIn("anti-cheat", r["results"][0]["detail"])
            # the FSR drop-in is fine
            r = await p.install_games([{"folder": g1["folder"], "way": "fsr"}])
            self.assertTrue(r["ok"], r)
            self.assertEqual(r["results"][0]["installed"], "fsr")
        asyncio.run(go())

    def _release_zip(self, tag="2.0.0"):
        src = self.tmp / "src" / f"HelixSR-{tag}"
        src.mkdir(parents=True)
        (src / "helixsr-setup.sh").write_text("#!/bin/sh\n")
        (src / "helixsr-setup.sh").chmod(0o755)
        (src / "setup").mkdir()
        (src / "setup" / "helixsr_bridge.py").write_text(STUB_BRIDGE)
        (src / "HelixSR-fsrfg").mkdir()
        dll = b"MZ" + b"\x00" * 64 + f"\x00HelixSR {tag}\x00".encode() + b"\x00" * 64
        (src / "HelixSR-fsrfg" / "amd_fidelityfx_dx12.dll").write_bytes(dll)
        zp = self.tmp / "rel.zip"
        with zipfile.ZipFile(zp, "w") as z:
            for f in sorted(src.rglob("*")):
                if f.is_file():
                    z.write(f, f"HelixSR-{tag}/{f.relative_to(src)}")
        return zp

    # --- bridge client --------------------------------------------------------

    def test_bridge_status_ok(self):
        d = self._release_dir()
        out = bridge.run(d, "status")
        self.assertTrue(out["ok"])
        self.assertEqual(out["version"], "9.9.9")
        self.assertTrue(out["network_ready"])

    def test_bridge_refuses_unknown_game(self):
        d = self._release_dir()
        with self.assertRaises(bridge.BridgeError) as ctx:
            bridge.run(d, "install", {"game": "/nonexistent", "way": "scaler"})
        self.assertIn("no game", str(ctx.exception))

    def test_bridge_malformed_output(self):
        d = self._release_dir()
        (d / "setup" / "helixsr_bridge.py").write_text("print('not json')\n")
        with self.assertRaises(bridge.BridgeError):
            bridge.run(d, "status")

    def test_bridge_missing_script(self):
        with self.assertRaises(bridge.BridgeError):
            bridge.run(self.tmp, "status")

    def test_bridge_never_launches_the_running_binary(self):
        # Inside Decky sys.executable is the frozen PluginLoader binary; running
        # it with a script argument starts a second loader, whose startup
        # restarts Steam's web UI. The bridge must run a real Python instead.
        d = self._release_dir()
        launched = self.tmp / "launched"
        fake = self.tmp / "PluginLoader"
        fake.write_text(f'#!/bin/sh\necho "$@" >> {launched}\nexit 1\n')
        fake.chmod(0o755)
        with mock.patch.object(sys, "executable", str(fake)):
            out = bridge.run(d, "status")
        self.assertTrue(out["ok"])
        self.assertFalse(launched.exists(), "bridge launched sys.executable")

    def test_plugin_code_never_uses_sys_executable(self):
        import ast
        for rel in ("main.py", "py_modules/bridge.py"):
            tree = ast.parse((REPO / rel).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (isinstance(node, ast.Attribute) and node.attr == "executable"
                        and isinstance(node.value, ast.Name) and node.value.id == "sys"):
                    self.fail(f"{rel}:{node.lineno} uses sys.executable")
                if (isinstance(node, ast.ImportFrom) and node.module == "sys"
                        and any(a.name == "executable" for a in node.names)):
                    self.fail(f"{rel}:{node.lineno} imports sys.executable")

    def test_status_probed_once_across_refreshes(self):
        self._release_dir(tag="2.0.0")
        p = self._plugin(active="v2.0.0")

        async def go():
            p.loop = asyncio.get_event_loop()
            with mock.patch.object(bridge, "status", wraps=bridge.status) as probe:
                await p.get_state()
                await p.get_state()
            self.assertEqual(probe.call_count, 1)
        asyncio.run(go())

    def test_status_error_is_reported_and_not_cached(self):
        d = self._release_dir(tag="2.0.0")
        bridge_py = d / "setup" / "helixsr_bridge.py"
        good = bridge_py.read_text()
        bridge_py.write_text("print('not json')\n")
        self.assertIn("error", main._status_of(d))
        bridge_py.write_text(good)
        self.assertEqual(main._status_of(d).get("version"), "9.9.9")

    def test_dll_version_stamp(self):
        d = self._release_dir(tag="2.0.0")
        self.assertEqual(bridge.dll_version(bridge.release_dll(d)), "2.0.0")
        self.assertEqual(bridge.dll_version(self.tmp / "missing.dll"), "?")

    def test_bundled_scaler_version(self):
        d = self._release_dir()
        pkg, ver = bridge.bundled_scaler_version(d)
        self.assertEqual(pkg, d / "helixsr-scaler")
        self.assertEqual(ver, "1.1.0")

    # --- releases / state ------------------------------------------------------

    def test_plugin_version_matches_package(self):
        want = json.loads((REPO / "package.json").read_text())["version"]
        self.assertEqual(main._plugin_version(), want)
        self.assertTrue(want)

    def test_get_releases_parses(self):
        p = self._plugin()

        async def go():
            p.loop = asyncio.get_event_loop()
            fake = [{"tag_name": "v2.0.0", "name": "HelixSR 2.0.0",
                     "published_at": "2026-10-01T00:00:00Z",
                     "assets": [{"name": "HelixSR-2.0.0.zip",
                                 "browser_download_url": "https://example.invalid/h.zip",
                                 "size": 5}],
                     "body": "notes", "draft": False}]
            with mock.patch.object(main, "_fetch_json", return_value=fake):
                r = await p.get_releases()
            self.assertEqual(r["error"], "")
            self.assertEqual(len(r["releases"]), 1)
            self.assertEqual(r["releases"][0]["zip_url"], "https://example.invalid/h.zip")
        asyncio.run(go())

    def test_get_state_maps_versions(self):
        self._release_dir(tag="2.0.0")
        p = self._plugin()

        async def go():
            p.loop = asyncio.get_event_loop()
            s = await p.get_state()
            self.assertEqual(len(s["versions"]), 1)
            v = s["versions"][0]
            self.assertEqual(v["tag"], "v2.0.0")
            self.assertEqual(v["dll_version"], "9.9.9")  # the bridge's own version_of
            self.assertTrue(v["network_built"])
            self.assertEqual(v["scaler_version"], "1.1.0")
        asyncio.run(go())

    # --- download ---------------------------------------------------------------

    def test_download_extract_layout(self):
        zp = self._release_zip()
        p = self._plugin()

        async def go():
            p.loop = asyncio.get_event_loop()
            r = await p.start_download("v2.0.0", zp.as_uri())
            self.assertEqual(r, {"ok": True})
            st = await self._wait_done(p.get_download_status)
            self.assertTrue(st["ok"], st["error"])
            self.assertEqual(st["dir"], "HelixSR-2.0.0")
            self.assertTrue(st["network_built"])
            root = Path(os.environ["DECKY_PLUGIN_RUNTIME_DIR"]) / "helixsr"
            self.assertFalse((root / ".v2.0.0.zip").exists())
            self.assertEqual(_mode(root / "HelixSR-2.0.0" / "helixsr-setup.sh"), 0o755)
        asyncio.run(go())

    def test_download_rejects_non_release(self):
        zp = self.tmp / "flat.zip"
        with zipfile.ZipFile(zp, "w") as z:
            z.writestr("lonely.txt", b"data")
        p = self._plugin()

        async def go():
            p.loop = asyncio.get_event_loop()
            self.assertEqual(await p.start_download("v9.9.9", zp.as_uri()), {"ok": True})
            st = await self._wait_done(p.get_download_status)
            self.assertFalse(st["ok"])
            self.assertIn("2.0+", st["error"])
        asyncio.run(go())

    def test_download_refuses_when_disk_full(self):
        zp = self._release_zip()
        p = self._plugin()

        async def go():
            p.loop = asyncio.get_event_loop()
            with mock.patch.object(main, "_free_bytes", return_value=100_000_000):
                r = await p.start_download("v2.0.0", zp.as_uri(), 137_000_000)
            self.assertFalse(r["ok"])
            self.assertIn("free disk", r["error"])
            self.assertFalse(p._dl["running"])
        asyncio.run(go())

    def test_download_cleans_partial_dir(self):
        src = self.tmp / "src-bad" / "HelixSR-9.9.9"
        src.mkdir(parents=True)
        (src / "helixsr-setup.sh").write_text("#!/bin/sh\n")
        zp = self.tmp / "bad.zip"
        with zipfile.ZipFile(zp, "w") as z:
            for f in sorted(src.iterdir()):
                z.write(f, f"HelixSR-9.9.9/{f.name}")
        p = self._plugin()

        async def go():
            p.loop = asyncio.get_event_loop()
            self.assertEqual(await p.start_download("v9.9.9", zp.as_uri()), {"ok": True})
            st = await self._wait_done(p.get_download_status)
            self.assertFalse(st["ok"])
            root = Path(os.environ["DECKY_PLUGIN_RUNTIME_DIR"]) / "helixsr"
            self.assertFalse((root / "HelixSR-9.9.9").exists())
        asyncio.run(go())

    # --- build ------------------------------------------------------------------

    def _patch_toolchain(self, p):
        p._ensure_g3_toolchain = lambda d, line, cancel: (sys.executable, Path("/bin/true"))

    def test_build_runs_stub_setup(self):
        d = self._release_dir(status={"version": "9.9.9", "network_ready": False,
                                      "fg_ready": False, "fg_reason": ""})
        (d / main.DLSS_FILE).write_bytes(b"cached dll")
        p = self._plugin(active="v2.0.0")
        self._patch_toolchain(p)

        async def go():
            p.loop = asyncio.get_event_loop()
            with mock.patch.object(main, "_sha256_file", return_value=main.DLSS_SHA256):
                r = await p.start_build()
                self.assertTrue(r["ok"], r)
                for _ in range(400):
                    st = await p.get_build_status()
                    if not st["running"]:
                        break
                    await asyncio.sleep(0.05)
            log = Path(r["log"])
            self.assertIn("stub setup ran", log.read_text())
        asyncio.run(go())

    def test_build_hands_the_ca_bundle_to_the_setup(self):
        """The setup's Python downloads over HTTPS (NVIDIA's Streamline DLLs,
        the NGX core) and has no CA certificates of its own."""
        d = self._release_dir(status={"version": "9.9.9", "network_ready": False,
                                      "fg_ready": False, "fg_reason": ""})
        (d / main.DLSS_FILE).write_bytes(b"cached dll")
        p = self._plugin(active="v2.0.0")
        self._patch_toolchain(p)

        async def go():
            p.loop = asyncio.get_event_loop()
            with mock.patch.object(main, "_sha256_file", return_value=main.DLSS_SHA256):
                r = await p.start_build()
                self.assertTrue(r["ok"], r)
                for _ in range(400):
                    st = await p.get_build_status()
                    if not st["running"]:
                        break
                    await asyncio.sleep(0.05)
            log = Path(r["log"]).read_text()
            self.assertIn("ssl_cert_file=", log)
            self.assertNotIn("ssl_cert_file=\n", log)
            self.assertTrue(log.split("ssl_cert_file=")[1].splitlines()[0].endswith("cacert.pem"))
        asyncio.run(go())

    def test_build_does_not_fetch_the_streamline_zips(self):
        """The frame generation DLLs live inside NVIDIA's 226/276 MB SDK
        zips, but the hash the setup checks is the DLL's: the plugin must
        not fetch the zips (the release's setup takes the DLLs itself)."""
        d = self._release_dir(status={"version": "9.9.9", "network_ready": False,
                                      "fg_ready": False, "fg_reason": ""})
        (d / main.DLSS_FILE).write_bytes(b"cached dll")
        p = self._plugin(active="v2.0.0")
        self._patch_toolchain(p)
        seen = {}

        async def fake_spawn(d, log, header, args):
            seen["args"] = list(args)

        async def go():
            p.loop = asyncio.get_event_loop()
            p._spawn_and_watch = fake_spawn
            with mock.patch.object(main, "_sha256_file", return_value=main.DLSS_SHA256):
                r = await p.start_build(fg=True)
                self.assertTrue(r["ok"], r)
                for _ in range(400):
                    st = await p.get_build_status()
                    if not st["running"]:
                        break
                    await asyncio.sleep(0.05)
        asyncio.run(go())
        args = seen.get("args", [])
        self.assertNotIn("--dlssg", args)
        self.assertIn("--dlss", args)
        for a in args:
            self.assertNotIn("streamline-sdk", a)

    def test_build_fg_gate_refuses(self):
        d = self._release_dir()
        p = self._plugin(active="v2.0.0")

        async def go():
            p.loop = asyncio.get_event_loop()
            with mock.patch.object(main, "_fg_compile_fits", return_value=(False, "no room")):
                r = await p.start_build(fg=True)
            self.assertFalse(r["ok"])
            self.assertIn("no room", r["error"])
        asyncio.run(go())

    def test_build_download_mutex(self):
        self._release_dir()
        p = self._plugin(active="v2.0.0")
        p._dl["running"] = True

        async def go():
            p.loop = asyncio.get_event_loop()
            r = await p.start_build()
            self.assertFalse(r["ok"])
            self.assertIn("download is running", r["error"])
        asyncio.run(go())

    def test_cancel_build_kills_tree(self):
        import subprocess as _sp
        p = self._plugin()
        proc = _sp.Popen(["sleep", "30"], start_new_session=True)
        p._build_proc = proc
        p._build_lock.acquire()

        async def go():
            p.loop = asyncio.get_event_loop()
            r = await p.cancel_build()
            self.assertTrue(r["ok"], r)
        asyncio.run(go())
        try:
            proc.wait(timeout=15)
        finally:
            if proc.poll() is None:
                proc.kill()
        self.assertIsNotNone(proc.returncode)
        p._build_lock.release()

    def test_reap_orphaned_build(self):
        import subprocess as _sp
        proc = _sp.Popen(["bash", "-c", 'exec -a helixsr_setup.py sleep 30'],
                         start_new_session=True)
        try:
            pf = Path(os.environ["DECKY_PLUGIN_RUNTIME_DIR"]) / "helixsr" / main.BUILD_PID_FILE
            pf.parent.mkdir(parents=True, exist_ok=True)
            pf.write_text(str(proc.pid))
            main._reap_orphaned_build()
            try:
                proc.wait(timeout=15)
                dead = True
            except _sp.TimeoutExpired:
                dead = False
            self.assertTrue(dead, "orphaned stub build should be dead")
            self.assertFalse(pf.exists())
        finally:
            if proc.poll() is None:
                proc.kill()

    # --- scan -------------------------------------------------------------------

    def test_scan_lists_games_with_index(self):
        d = self._release_dir()
        g1 = self._game(str(self.tmp / "games" / "A"), name="GameA")
        (d / ".stub-games.json").write_text(json.dumps([g1]))
        p = self._plugin(active="v2.0.0")

        async def go():
            p.loop = asyncio.get_event_loop()
            self.assertEqual(await p.start_scan(), {"ok": True})
            st = await self._wait_done(p.get_scan_status)
            self.assertTrue(st["ok"], st["error"])
            self.assertEqual(len(st["games"]), 1)
            self.assertEqual(st["games"][0]["index"], 1)
            self.assertEqual(st["games"][0]["name"], "GameA")
            cached = await p.get_last_scan()
            self.assertEqual(len(cached["games"]), 1)
        asyncio.run(go())

    def test_scan_extra_folders(self):
        self._release_dir()
        p = self._plugin(active="v2.0.0")

        async def go():
            p.loop = asyncio.get_event_loop()
            folder = self.tmp / "extra"
            folder.mkdir()
            r = await p.add_scan_folder(str(folder))
            self.assertTrue(r["ok"])
            self.assertIn(str(folder), r["folders"])
            r = await p.remove_scan_folder(str(folder))
            self.assertTrue(r["ok"])
            self.assertNotIn(str(folder), r["folders"])
            r = await p.add_scan_folder(str(self.tmp / "missing"))
            self.assertFalse(r["ok"])
        asyncio.run(go())

    # --- install / remove / repair / verify ---------------------------------------

    def test_install_uninstall_roundtrip(self):
        d = self._release_dir()
        g1 = self._game(str(self.tmp / "games" / "A"), name="GameA")
        (d / ".stub-games.json").write_text(json.dumps([g1]))
        p = self._plugin(active="v2.0.0")

        async def go():
            p.loop = asyncio.get_event_loop()
            r = await p.install_games([{"folder": g1["folder"], "way": "auto",
                                        "fg": True}])
            self.assertTrue(r["ok"], r)
            self.assertEqual(r["results"][0]["installed"], "scaler")
            r = await p.uninstall_games([g1["folder"]])
            self.assertTrue(r["ok"], r)
            r = await p.uninstall_games([g1["folder"]])
            self.assertFalse(r["ok"])
        asyncio.run(go())

    def test_install_needs_scaler_pkg(self):
        d = self._release_dir()
        shutil.rmtree(d / "helixsr-scaler")
        g1 = self._game(str(self.tmp / "games" / "A"))
        (d / ".stub-games.json").write_text(json.dumps([g1]))
        p = self._plugin(active="v2.0.0")

        async def go():
            p.loop = asyncio.get_event_loop()
            r = await p.install_games([{"folder": g1["folder"], "way": "scaler"}])
            self.assertFalse(r["ok"])
            self.assertIn("helixsr-scaler", r["results"][0]["detail"])
        asyncio.run(go())

    def test_repair_reinstalls(self):
        d = self._release_dir()
        g1 = self._game(str(self.tmp / "games" / "A"))
        (d / ".stub-games.json").write_text(json.dumps([g1]))
        p = self._plugin(active="v2.0.0")

        async def go():
            p.loop = asyncio.get_event_loop()
            r = await p.install_games([{"folder": g1["folder"], "way": "fsr"}])
            self.assertTrue(r["ok"], r)
            r = await p.repair_games([g1["folder"]])
            self.assertTrue(r["ok"], r)
        asyncio.run(go())

    def test_verify_reports(self):
        d = self._release_dir()
        g1 = self._game(str(self.tmp / "games" / "A"))
        (d / ".stub-games.json").write_text(json.dumps([g1]))
        p = self._plugin(active="v2.0.0")

        async def go():
            p.loop = asyncio.get_event_loop()
            r = await p.verify_installs()
            self.assertTrue(r["ok"])
            self.assertEqual(r["games"], [])
            await p.install_games([{"folder": g1["folder"], "way": "scaler"}])
            r = await p.verify_installs()
            self.assertTrue(r["ok"])
            self.assertEqual(len(r["games"]), 1)
            # No loader/DLL files exist in the fixture game: problems found.
            self.assertFalse(r["games"][0]["ok"])
        asyncio.run(go())

    def test_scaler_fg_and_rollback(self):
        d = self._release_dir()
        g1 = self._game(str(self.tmp / "games" / "A"))
        (d / ".stub-games.json").write_text(json.dumps([g1]))
        p = self._plugin(active="v2.0.0")

        async def go():
            p.loop = asyncio.get_event_loop()
            await p.install_games([{"folder": g1["folder"], "way": "scaler"}])
            r = await p.set_scaler_fg(g1["folder"], True)
            self.assertTrue(r["ok"], r)
            self.assertTrue(r["game"]["frame_generation"])
            r = await p.rollback_game(g1["folder"])
            self.assertTrue(r["ok"], r)
        asyncio.run(go())

    # --- settings / presets / logs -----------------------------------------------

    def test_settings_roundtrip(self):
        d = self._release_dir()
        g1 = self._game(str(self.tmp / "games" / "A"))
        (d / ".stub-games.json").write_text(json.dumps([g1]))
        p = self._plugin(active="v2.0.0")

        async def go():
            p.loop = asyncio.get_event_loop()
            r = await p.get_game_settings(g1["folder"])
            self.assertFalse(r["ok"])  # not installed yet
            await p.install_games([{"folder": g1["folder"], "way": "scaler"}])
            r = await p.get_game_settings(g1["folder"])
            self.assertTrue(r["ok"], r)
            self.assertEqual(r["schema"], "scaler")
            self.assertIn("HelixSR.WaveSize", r["values"])
            r = await p.set_game_settings(g1["folder"], {"HelixSR.WaveSize": "64"})
            self.assertTrue(r["ok"], r)
            r = await p.get_game_settings(g1["folder"])
            self.assertEqual(r["values"]["HelixSR.WaveSize"], "64")
            r = await p.reset_game_settings(g1["folder"])
            self.assertTrue(r["ok"], r)
            r = await p.get_game_settings(g1["folder"])
            self.assertEqual(r["values"]["HelixSR.WaveSize"], "auto")
        asyncio.run(go())

    def test_presets_crud(self):
        p = self._plugin(active="v2.0.0")

        async def go():
            p.loop = asyncio.get_event_loop()
            r = await p.get_ini_presets()
            self.assertTrue(r["ok"])
            self.assertIn("Sharpening.Sharpener", r["defaults_helixsr"])
            r = await p.save_ini_preset("Mine", {"HelixSR.WaveSize": "64"})
            self.assertTrue(r["ok"])
            self.assertIn("Mine", r["presets"])
            r = await p.delete_ini_preset("Mine")
            self.assertTrue(r["ok"])
            self.assertNotIn("Mine", r["presets"])
        asyncio.run(go())

    def test_log_and_fix(self):
        d = self._release_dir()
        g1 = self._game(str(self.tmp / "games" / "A"))
        (d / ".stub-games.json").write_text(json.dumps([g1]))
        p = self._plugin(active="v2.0.0")

        async def go():
            p.loop = asyncio.get_event_loop()
            await p.install_games([{"folder": g1["folder"], "way": "fsr"}])
            r = await p.get_game_log(g1["folder"])
            self.assertTrue(r["ok"], r)
            self.assertEqual(r["fixes"], [{"id": "wave64", "label": main.Plugin.LOG_FIXES[0]["label"]}])
            r = await p.apply_log_fix(g1["folder"], "wave64")
            self.assertTrue(r["ok"], r)
            st = json.loads((d / ".stub-state.json").read_text())
            self.assertEqual(st["settings"][g1["folder"]].get("Compatibility.WaveSize"), "64")
        asyncio.run(go())

    # --- storage -------------------------------------------------------------------

    def test_remove_version_heals_active(self):
        self._release_dir(tag="2.0.0")
        self._release_dir(tag="2.1.0")
        p = self._plugin(active="v2.1.0")

        async def go():
            p.loop = asyncio.get_event_loop()
            r = await p.remove_old_versions()
            self.assertTrue(r["ok"], r)
            self.assertEqual(r["removed"], ["v2.0.0"])
            self.assertEqual(r["skipped"], ["v2.1.0"])
            r = await p.remove_version("v2.1.0")
            self.assertTrue(r["ok"], r)
            self.assertEqual(p.settings.getSetting("active_tag"), "")
        asyncio.run(go())

    def test_remove_active_version_heals_to_other(self):
        self._release_dir(tag="2.0.0")
        self._release_dir(tag="2.1.0")
        p = self._plugin(active="v2.0.0")

        async def go():
            p.loop = asyncio.get_event_loop()
            r = await p.remove_version("v2.0.0")
            self.assertTrue(r["ok"], r)
            self.assertEqual(p.settings.getSetting("active_tag"), "v2.1.0")
        asyncio.run(go())

    def test_factory_reset(self):
        d = self._release_dir()
        g1 = self._game(str(self.tmp / "games" / "A"), name="GameA")
        (d / ".stub-games.json").write_text(json.dumps([g1]))
        p = self._plugin(active="v2.0.0")

        async def go():
            p.loop = asyncio.get_event_loop()
            await p.install_games([{"folder": g1["folder"], "way": "scaler"}])
            r = await p.factory_reset()
            self.assertTrue(r["ok"], r)
            self.assertIn("GameA", r["games_uninstalled"])
            self.assertEqual(r["versions_removed"], ["v2.0.0"])
            self.assertEqual(p.settings.getSetting("active_tag"), "")
        asyncio.run(go())

    # --- self-update / health / misc ---------------------------------------------------

    def _installed_plugin(self, version="0.6.2"):
        """A plugin folder as Decky installs it: inside plugins/."""
        plug = self.tmp / "plugins" / "helixsr-muse-plugin"
        (plug / "dist").mkdir(parents=True)
        (plug / "py_modules").mkdir()
        (plug / "plugin.json").write_text(json.dumps({"name": "HelixSR Muse Plugin"}))
        (plug / "package.json").write_text(
            json.dumps({"name": "helixsr-muse-plugin", "version": version}))
        (plug / "main.py").write_text("# old backend\n")
        (plug / "dist" / "index.js").write_text("// old bundle\n")
        return plug

    def _plugin_zip(self, version="0.6.3"):
        """A release zip shaped like the plugin's own zip."""
        src = self.tmp / "pluginsrc"
        (src / "dist").mkdir(parents=True)
        (src / "py_modules").mkdir()
        (src / "plugin.json").write_text(json.dumps({"name": "HelixSR Muse Plugin"}))
        (src / "package.json").write_text(
            json.dumps({"name": "helixsr-muse-plugin", "version": version}))
        (src / "main.py").write_text("# new backend\n")
        (src / "dist" / "index.js").write_text("// new bundle\n")
        (src / "py_modules" / "bridge.py").write_text("# bridge\n")
        zp = self.tmp / "plugin.zip"
        with zipfile.ZipFile(zp, "w") as z:
            for f in sorted(src.rglob("*")):
                if f.is_file():
                    z.write(f, f"helixsr-muse-plugin/{f.relative_to(src)}")
        return zp

    def test_update_patches_files_when_parent_is_read_only(self):
        """Decky owns plugins/ as root: the folder itself must be patched in
        place, renaming it fails with EACCES."""
        plug = self._installed_plugin()
        zp = self._plugin_zip("0.6.3")
        os.chmod(plug.parent, 0o555)
        self.addCleanup(os.chmod, plug.parent, 0o755)
        new = main._install_plugin_zip(zp, plug, "0.6.2")
        self.assertEqual(new, "0.6.3")
        self.assertEqual((plug / "main.py").read_text(), "# new backend\n")
        self.assertEqual((plug / "dist" / "index.js").read_text(), "// new bundle\n")
        self.assertEqual((plug / "py_modules" / "bridge.py").read_text(), "# bridge\n")
        self.assertFalse((plug / ".update-stage").exists())
        self.assertEqual(json.loads((plug / "package.json").read_text())["version"], "0.6.3")

    def test_update_removes_stale_files(self):
        plug = self._installed_plugin()
        (plug / "dist" / "obsolete.js").write_text("old\n")
        (plug / "assets").mkdir()
        (plug / "assets" / "banner.png").write_bytes(b"png")
        zp = self._plugin_zip("0.6.3")
        main._install_plugin_zip(zp, plug, "0.6.2")
        self.assertFalse((plug / "dist" / "obsolete.js").exists())
        # Entries the version does not ship are none of the update's business.
        self.assertTrue((plug / "assets" / "banner.png").exists())

    def test_update_refuses_older_zip(self):
        plug = self._installed_plugin()
        zp = self._plugin_zip("0.6.1")
        with self.assertRaises(OSError):
            main._install_plugin_zip(zp, plug, "0.6.2")
        self.assertEqual((plug / "main.py").read_text(), "# old backend\n")

    def test_update_permission_error_is_actionable(self):
        msg = main._update_error(PermissionError(13, "Permission denied"))
        self.assertIn("permission denied", msg.lower())
        self.assertIn("Decky", msg)

    def test_plugin_update_refuses_while_a_task_runs(self):
        p = self._plugin()

        async def go():
            p.loop = asyncio.get_event_loop()
            p._dl["running"] = True
            r = await p.start_plugin_update()
            self.assertFalse(r["ok"])
            self.assertIn("download", r["error"])
            p._dl["running"] = False
            p._build_lock.acquire()
            try:
                r = await p.start_plugin_update()
            finally:
                p._build_lock.release()
            self.assertFalse(r["ok"])
            self.assertIn("build", r["error"])
        asyncio.run(go())

    def test_way_picks_are_persisted(self):
        p = self._plugin()

        async def go():
            p.loop = asyncio.get_event_loop()
            r = await p.get_way_picks()
            self.assertTrue(r["ok"])
            self.assertEqual(r["ways"], {})
            r = await p.set_way_pick("/games/A", "fsr")
            self.assertTrue(r["ok"])
            self.assertEqual(r["ways"], {"/games/A": "fsr"})
            r = await p.set_way_pick("/games/B", "auto")
            self.assertTrue(r["ok"])
            r = await p.get_way_picks()
            self.assertEqual(r["ways"], {"/games/A": "fsr", "/games/B": "auto"})
            # a pick for a game that is gone stays (the game may come back)
            r = await p.set_way_pick("/games/C", "scaler")
            self.assertEqual(r["ways"]["/games/C"], "scaler")
            # an unknown way is dropped
            r = await p.set_way_pick("/games/A", "nonsense")
            self.assertTrue(r["ok"])
            self.assertNotIn("/games/A", r["ways"])
        asyncio.run(go())

    def test_check_plugin_update_newer(self):
        p = self._plugin()

        async def go():
            p.loop = asyncio.get_event_loop()
            fake = [{"tag_name": "v9.9.9", "prerelease": False, "draft": False, "body": "x",
                     "assets": [{"name": "helixsr-muse-plugin-v9.9.9.zip",
                                 "browser_download_url": "https://example.invalid/p.zip",
                                 "size": 1}]}]
            with mock.patch.object(main, "_fetch_json", return_value=fake):
                r = await p.check_plugin_update(force=True)
            self.assertTrue(r["update_available"])
            self.assertEqual(r["latest"], "9.9.9")
        asyncio.run(go())

    def test_health_check_runs(self):
        self._release_dir()
        p = self._plugin(active="v2.0.0")

        async def go():
            p.loop = asyncio.get_event_loop()
            with mock.patch("socket.create_connection") as c:
                c.side_effect = OSError("no net")
                r = await p.health_check()
            names = [c["name"] for c in r["checks"]]
            self.assertIn("Versions", names)
            self.assertIn("Games", names)
            self.assertIn("Disk space", names)
        asyncio.run(go())

    def test_pin_format(self):
        for pin in (main.DLSS_SHA256, main.DLSSG_SHA256, main.DLSSG2_SHA256,
                    main.G3_PY_SHA, main.G3_DXC_SHA):
            self.assertEqual(len(pin), 64)
            self.assertTrue(all(c in "0123456789abcdef" for c in pin))

    def test_version_compare(self):
        self.assertEqual(main._compare_versions("v2.0.0", "v1.9.3"), 1)
        self.assertEqual(main._compare_versions("v2.0.0", "v2.0.0"), 0)

    def test_bridge_clean(self):
        self.assertEqual(main.bridge_clean('"/tmp/games/A"'), "/tmp/games/A")
        self.assertEqual(main.bridge_clean("file:///tmp/games/A"), "/tmp/games/A")
