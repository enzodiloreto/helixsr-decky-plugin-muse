"""Plugin RPC flows with a stubbed decky module and synthetic fixtures."""
import asyncio
import hashlib
import json
import os
import shutil
import stat
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from _common import (
    REPO,
    StaticSettings,
    install_decky_stub,
    make_helixsr_dir,
    make_steam_home,
)

emitted = []
install_decky_stub(emitted)

import main


def _mode(p):
    return stat.S_IMODE(os.stat(p).st_mode)


class PluginCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="hxplug-"))
        self._saved_env = {k: os.environ.get(k) for k in
                           ("DECKY_PLUGIN_RUNTIME_DIR", "DECKY_USER_HOME", "DECKY_PLUGIN_DIR")}
        os.environ["DECKY_PLUGIN_RUNTIME_DIR"] = str(self.tmp / "runtime")
        os.environ["DECKY_PLUGIN_DIR"] = str(REPO)
        emitted.clear()
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
        p._sc = main._new_sc_state()
        p._sc_stop = threading.Event()
        p._scan = main._new_scan_state()
        p._mig = main._new_mig_state()
        p._update_check = {"at": 0.0, "result": {"ok": True, "update_available": False}}
        p._pu = {"running": False, "done": False, "ok": False, "error": "",
                 "version": "", "url": "", "downloaded": 0, "total": 0,
                 "mode": "", "path": ""}
        p._pu_stop = threading.Event()
        p._update_pending = ""
        p._scan_stop = threading.Event()
        p._scan_cache = main._empty_scan_cache()
        p._ops_lock = threading.Lock()
        p._build_thread = None
        p._build_running = False
        p._build_log = None
        p._build_proc = None
        return p

    def _version_dir(self, tag="1.8.1", network=True):
        root = Path(os.environ["DECKY_PLUGIN_RUNTIME_DIR"]) / "helixsr"
        return make_helixsr_dir(root, tag=tag, network=network)

    async def _wait_done(self, getter, cap=400):
        for _ in range(cap):
            st = await getter()
            if st.get("done"):
                return st
            await asyncio.sleep(0.05)
        raise AssertionError("timed out waiting for completion")

    # --- releases / state ---------------------------------------------------

    def test_plugin_version_matches_package(self):
        want = json.loads((REPO / "package.json").read_text())["version"]
        self.assertEqual(main._plugin_version(), want)
        self.assertTrue(want)

    def test_active_dir_heals_stale_tag(self):
        d = self._version_dir()
        p = self._plugin(active="v9.9.9")

        async def go():
            p.loop = asyncio.get_event_loop()
            got = await p._active_dir()
            self.assertEqual(got, d)
            self.assertEqual(p.settings.getSetting("active_tag"), "")
        asyncio.run(go())

    # --- download -----------------------------------------------------------

    def _release_zip(self, tag="9.9.9", flat=False):
        src = self.tmp / "src" / f"HelixSR-{tag}"
        src.mkdir(parents=True)
        if flat:
            (src / "lonely.txt").write_bytes(b"no top folder here")
            names = {"lonely.txt": 0o644}
        else:
            (src / "helixsr-setup.sh").write_text("#!/bin/sh\n")
            (src / "helixsr-uninstall.sh").write_text("#!/bin/sh\n")
            (src / "tool.sh").write_text("#!/bin/sh\n")
            (src / "data.bin").write_bytes(b"data")
            for f, m in (("helixsr-setup.sh", 0o755), ("helixsr-uninstall.sh", 0o755),
                         ("tool.sh", 0o755), ("data.bin", 0o644)):
                (src / f).chmod(m)
            dll = (make_helixsr_dir(self.tmp / "pkg", tag=tag) / "amd_fidelityfx_dx12.dll").read_bytes()
            (src / "amd_fidelityfx_dx12.dll").write_bytes(dll)
            names = None
        zp = self.tmp / "rel.zip"
        with zipfile.ZipFile(zp, "w") as z:
            for f in sorted(src.iterdir()):
                arc = f.name if flat else f"HelixSR-{tag}/{f.name}"
                z.write(f, arc)
        return zp

    def test_download_extract_modes_cleanup(self):
        zp = self._release_zip()
        p = self._plugin()

        async def go():
            p.loop = asyncio.get_event_loop()
            r = await p.start_download("v9.9.9", zp.as_uri())
            self.assertEqual(r, {"ok": True})
            st = await self._wait_done(p.get_download_status)
            self.assertTrue(st["ok"], st["error"])
            self.assertEqual(st["dir"], "HelixSR-9.9.9")
            self.assertTrue(st["network_built"])
            root = Path(os.environ["DECKY_PLUGIN_RUNTIME_DIR"]) / "helixsr"
            d = root / "HelixSR-9.9.9"
            self.assertEqual(_mode(d / "tool.sh"), 0o755)
            self.assertEqual(_mode(d / "data.bin"), 0o644)
            self.assertFalse((root / ".v9.9.9.zip").exists())
            self.assertEqual(p.settings.getSetting("active_tag"), "")
        asyncio.run(go())

    def test_incomplete_download_errors_and_cleans(self):
        p = self._plugin()

        class Truncated:
            headers = {"Content-Length": "100"}

            def read(self, n):
                data, self._done = (b"x" * 10, True) if not getattr(self, "_done", False) else (b"", True)
                return data

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

        async def go():
            p.loop = asyncio.get_event_loop()
            with mock.patch.object(main.urllib.request, "urlopen", return_value=Truncated()):
                r = await p.start_download("v9.9.9", "https://example.invalid/x.zip")
                self.assertEqual(r, {"ok": True})
                st = await self._wait_done(p.get_download_status)
            self.assertFalse(st["ok"])
            self.assertIn("incomplete", st["error"])
            root = Path(os.environ["DECKY_PLUGIN_RUNTIME_DIR"]) / "helixsr"
            self.assertFalse((root / ".v9.9.9.zip").exists())
        asyncio.run(go())

    def test_flat_zip_fails_cleanly(self):
        zp = self._release_zip(flat=True)
        p = self._plugin()

        async def go():
            p.loop = asyncio.get_event_loop()
            self.assertEqual(await p.start_download("v9.9.9", zp.as_uri()), {"ok": True})
            st = await self._wait_done(p.get_download_status)
            self.assertFalse(st["ok"])
        asyncio.run(go())

    def test_zip_top(self):
        with zipfile.ZipFile(self.tmp / "a.zip", "w") as z:
            z.writestr("Top/f.txt", b"x")
            z.writestr("Top/g.txt", b"y")
        with zipfile.ZipFile(self.tmp / "a.zip") as z:
            self.assertEqual(main._zip_top(z), "Top")

    # --- scan ---------------------------------------------------------------

    def test_scan_and_cache_reuse(self):
        self._version_dir()
        home = make_steam_home(self.tmp)
        os.environ["DECKY_USER_HOME"] = str(home)
        p = self._plugin()
        real_games = main.helixscan.games
        calls = []

        def counting(*a, **k):
            calls.append(1)
            return real_games(*a, **k)

        async def go():
            p.loop = asyncio.get_event_loop()
            with mock.patch.object(main.helixscan, "games", side_effect=counting):
                self.assertEqual((await p.install_games([1]))["ok"], True)
                self.assertEqual((await p.install_games([2]))["ok"], True)
            self.assertEqual(len(calls), 1)
        asyncio.run(go())

    def test_last_scan_persisted(self):
        self._version_dir(tag="9.9.9")
        home = make_steam_home(self.tmp)
        os.environ["DECKY_USER_HOME"] = str(home)
        p = self._plugin()

        async def go():
            p.loop = asyncio.get_event_loop()
            self.assertEqual((await p.get_last_scan())["games"], [])
            self.assertEqual(await p.start_scan(), {"ok": True})
            st = await self._wait_done(p.get_scan_status)
            self.assertTrue(st["ok"])
            cached = await p.get_last_scan()
            self.assertEqual([g["name"] for g in cached["games"]], ["GameA", "GameB"])
            self.assertEqual(cached["tag"], "v9.9.9")
            self.assertGreater(cached["scanned_at"], 0)
            self.assertIn("available", cached["games"][0])
            # install flips the persisted flag; uninstall flips it back
            idx = next(g["index"] for g in cached["games"] if g["name"] == "GameA")
            self.assertTrue((await p.install_games([idx]))["ok"])
            self.assertTrue((await p.get_last_scan())["games"][idx - 1]["installed"])
            self.assertTrue((await p.uninstall_games([idx]))["ok"])
            after = await p.get_last_scan()
            self.assertFalse(after["games"][idx - 1]["installed"])
            self.assertIn("FSR", after["games"][idx - 1]["status"])
        asyncio.run(go())

    def test_remove_guarded_while_used(self):
        self._version_dir(tag="9.9.9")
        home = make_steam_home(self.tmp)
        os.environ["DECKY_USER_HOME"] = str(home)
        p = self._plugin()

        async def go():
            p.loop = asyncio.get_event_loop()
            self.assertEqual(await p.start_scan(), {"ok": True})
            st = await self._wait_done(p.get_scan_status)
            idx = next(g["index"] for g in st["games"] if g["name"] == "GameB")
            self.assertEqual(st["games"][idx - 1]["installed_versions"], [])
            self.assertTrue((await p.install_games([idx], "optiscaler"))["ok"])
            st2 = await self._rescan(p)
            self.assertEqual(st2["games"][idx - 1]["installed_versions"], ["9.9.9"])
            blocked = await p.remove_version("v9.9.9")
            self.assertFalse(blocked["ok"])
            self.assertIn("GameB", blocked["error"])
            self.assertTrue((await p.uninstall_games([idx]))["ok"])
            allowed = await p.remove_version("v9.9.9")
            self.assertTrue(allowed["ok"], allowed)
            self.assertGreaterEqual(allowed.get("freed_mb", 0), 0)
        asyncio.run(go())

    async def _rescan(self, p):
        self.assertEqual(await p.start_scan(), {"ok": True})
        return await self._wait_done(p.get_scan_status)

    def test_remove_old_versions_and_dlss_cache(self):
        self._version_dir(tag="9.9.8")
        d9 = self._version_dir(tag="9.9.9")
        (d9 / "nvngx_dlss.dll").write_bytes(b"Z" * 1000)
        home = make_steam_home(self.tmp)
        os.environ["DECKY_USER_HOME"] = str(home)
        p = self._plugin(active="v9.9.9")

        async def go():
            p.loop = asyncio.get_event_loop()
            c = await p.clear_dlss_cache()
            self.assertTrue(c["ok"])
            self.assertEqual(len(c["cleared"]), 1)
            self.assertFalse((d9 / "nvngx_dlss.dll").exists())
            r = await p.remove_old_versions()
            self.assertTrue(r["ok"], r)
            self.assertEqual(r["removed"], ["v9.9.8"])
            self.assertEqual(r["skipped"], [])
            self.assertFalse((self.tmp / "runtime/helixsr/HelixSR-9.9.8").exists())
            st = await p.get_state()
            self.assertEqual([v["tag"] for v in st["versions"]], ["v9.9.9"])
            self.assertGreaterEqual(st["versions"][0]["size_mb"], 0)
        asyncio.run(go())

    def test_extra_folders(self):
        self._version_dir(tag="9.9.9")
        home = make_steam_home(self.tmp)
        os.environ["DECKY_USER_HOME"] = str(home)
        side = self.tmp / "side" / "SideGame"
        (side / "bin").mkdir(parents=True)
        (side / "bin" / "amd_fidelityfx_upscaler_dx12.dll").write_bytes(b"FSR")
        (side / "bin" / "game.exe").write_bytes(b"\x00" * 100)
        p = self._plugin()

        async def go():
            p.loop = asyncio.get_event_loop()
            bad = await p.add_scan_folder(str(self.tmp / "nope"))
            self.assertFalse(bad["ok"])
            ok = await p.add_scan_folder(str(side))
            self.assertTrue(ok["ok"])
            self.assertEqual(len(ok["folders"]), 1)
            dup = await p.add_scan_folder(str(side) + "/")
            self.assertTrue(dup["ok"])
            self.assertEqual(len(dup["folders"]), 1)
            self.assertEqual(await p.start_scan(), {"ok": True})
            st = await self._wait_done(p.get_scan_status)
            names = [g["name"] for g in st["games"]]
            self.assertIn("SideGame", names)
            rm = await p.remove_scan_folder(str(side))
            self.assertEqual(rm["folders"], [])
        asyncio.run(go())

    def test_check_build_ready_structure(self):
        self._version_dir(tag="9.9.9")
        p = self._plugin()

        async def go():
            p.loop = asyncio.get_event_loop()
            r = await p.check_build_ready("v9.9.9")
            self.assertIn("checks", r)
            self.assertEqual([c["name"] for c in r["checks"]],
                             ["Python 3 + numpy", "Proton/Wine (shader compiler)", "Disk space"])
            for c in r["checks"]:
                self.assertIn("ok", c)
                self.assertIn("detail", c)
            missing = await p.check_build_ready("v0.0.0")
            self.assertFalse(missing["ok"])
        asyncio.run(go())

    def test_fg_built_indicator(self):
        import json as _json
        base = Path(tempfile.mkdtemp(prefix="hxfg-"))
        self.addCleanup(shutil.rmtree, base, True)
        d = base / "HelixSR-1.9.0"
        (d / "setup").mkdir(parents=True)
        (d / "setup/helixsr_setup.py").write_text("# layout marker\n")
        (d / "helixsr-setup.sh").write_text("#!/bin/sh\n")
        (d / "amd_fidelityfx_dx12.dll").write_bytes(b"\x00HelixSR 1.9.0\x00" + b"\x00" * 100)
        self.assertFalse(main._fg_built(d))
        (d / "helixsr_fg_setup.json").write_text("not json{{")
        self.assertFalse(main._fg_built(d))
        (d / "helixsr_fg_setup.json").write_text(_json.dumps({"files": 1}))
        self.assertTrue(main._fg_built(d))
        root = Path(os.environ["DECKY_PLUGIN_RUNTIME_DIR"]) / "helixsr"
        root.mkdir(parents=True, exist_ok=True)
        shutil.copytree(d, root / "HelixSR-1.9.0", dirs_exist_ok=True)
        p = self._plugin(active="v1.9.0")

        async def go():
            p.loop = asyncio.get_event_loop()
            s = await p.get_state()
            v = next(x for x in s["versions"] if x["tag"] == "v1.9.0")
            self.assertTrue(v["fg_built"])
            self.assertEqual(v["layout"], "modern")
        asyncio.run(go())

    def test_build_args_and_ready_modern(self):
        legacy = self._version_dir(tag="9.9.8")
        base = Path(tempfile.mkdtemp(prefix="hxmodern-"))
        self.addCleanup(shutil.rmtree, base, True)
        modern = base / "HelixSR-1.9.0"
        (modern / "setup").mkdir(parents=True)
        (modern / "setup/helixsr_setup.py").write_text("# layout marker\n")
        (modern / "helixsr-setup.sh").write_text("#!/bin/sh\n")
        dlss = Path("/x/nvngx_dlss.dll")
        self.assertEqual(main.Plugin._build_args(legacy, False, dlss),
                         ["--yes", "--dlss", str(dlss)])
        self.assertEqual(main.Plugin._build_args(legacy, True, dlss),
                         ["--yes", "--rebuild", "--dlss", str(dlss)])
        self.assertEqual(main.Plugin._build_args(modern, False, dlss),
                         ["--yes", "--no-fg", "--dlss", str(dlss)])
        fg = Path("/x/nvngx_dlssg.dll")
        self.assertEqual(main.Plugin._build_args(modern, True, dlss, fg),
                         ["--yes", "--rebuild", "--dlss", str(dlss), "--dlssg", str(fg)])
        self.assertEqual(main.Plugin._build_args(legacy, False, dlss, fg),
                         ["--yes", "--dlss", str(dlss)])
        p = self._plugin()
        names = [c["name"] for c in p._check_build_ready_run(legacy)["checks"]]
        self.assertNotIn("Frame generation", names)
        names = [c["name"] for c in p._check_build_ready_run(modern)["checks"]]
        self.assertIn("Frame generation", names)
        fg_row = next(c for c in p._check_build_ready_run(modern)["checks"]
                      if c["name"] == "Frame generation")
        self.assertIn("not built", fg_row["detail"])
        names = [c["name"] for c in p._check_build_ready_run(modern, fg=True)["checks"]]
        self.assertIn("DLSS Frame Generation DLL", names)
        self.assertIn("NGX core prerequisites", names)
        # FG engine gate: this fixture has no setup/fg (1.9.3-style)
        self.assertFalse(main.helixref.fg_engine_available(modern))
        (modern / "setup" / "fg").mkdir(parents=True)
        (modern / "setup" / "fg" / "fg_setup.py").write_text("# engine\n")
        self.assertTrue(main.helixref.fg_engine_available(modern))
        self.assertFalse(main.helixref.fg_engine_available(legacy))

        async def fg_go():
            p.loop = asyncio.get_event_loop()
            legacy_active = self._plugin(active="v9.9.8")
            legacy_active.loop = asyncio.get_event_loop()
            r = await legacy_active.start_build(False, True)
            self.assertFalse(r["ok"])
            self.assertIn("1.9.0", r["error"])
            with mock.patch.object(main.helixref, "layout_of", return_value="modern"), \
                    mock.patch.object(main.helixref, "fg_engine_available", return_value=False):
                r = await legacy_active.check_build_ready("v9.9.8", fg=True)
                self.assertFalse(r["ok"])
                self.assertIn("withdrawn", r["error"])
                r = await legacy_active.start_build(False, True)
                self.assertFalse(r["ok"])
                self.assertIn("withdrawn", r["error"])
        asyncio.run(fg_go())

    def test_migration_moves_games(self):
        old = self._version_dir(tag="9.9.8")
        new = self._version_dir(tag="9.9.9")
        home = make_steam_home(self.tmp)
        os.environ["DECKY_USER_HOME"] = str(home)
        p = self._plugin(active="v9.9.8")

        async def go():
            p.loop = asyncio.get_event_loop()
            await self._rescan(p)
            idx = await self._game_index(p, "GameA")
            idx_b = await self._game_index(p, "GameB")
            self.assertTrue((await p.install_games([idx]))["ok"])
            self.assertTrue((await p.install_games([idx_b], "optiscaler"))["ok"])
            r = await p.start_migration("v9.9.9")
            self.assertEqual(r, {"ok": True})
            for _ in range(400):
                st = await p.get_migration_status()
                if st.get("done"):
                    break
                await asyncio.sleep(0.05)
            self.assertTrue(st["done"])
            self.assertTrue(st["ok"], st)
            self.assertEqual(st["total"], 2)
            by_name = {x["name"]: x for x in st["results"]}
            self.assertTrue(by_name["GameA"]["ok"] and by_name["GameB"]["ok"], st["results"])
            target = home / ".local/share/lib/steamapps/common/GameA/bin/amd_fidelityfx_upscaler_dx12.dll"
            self.assertTrue(main.helixscan.is_helixsr(target))
            marker = json.loads((target.parent / "helixsr-install.json").read_text())
            self.assertEqual(marker["helixsr"], "9.9.9")
            marker_b = json.loads((home / ".local/share/lib/steamapps/common/GameB/HelixSR/helixsr-install.json").read_text())
            self.assertEqual(marker_b["helixsr"], "9.9.9")
            self.assertEqual(marker_b["route"], "optiscaler")
            cached = await p.get_last_scan()
            entry = next(g for g in cached["games"] if g["name"] == "GameA")
            self.assertEqual(entry["installed_versions"], ["9.9.9"])
        asyncio.run(go())

    def test_migration_carries_old_dirs(self):
        import decky  # stubbed in tests/_common
        home = self.tmp / "deckyhome"
        old_settings = home / "settings" / "helixsr-decky-plugin-muse"
        old_data = home / "data" / "helixsr-decky-plugin-muse"
        old_settings.mkdir(parents=True)
        (old_settings / "settings.json").write_text('{"active_tag": "v9.9.9"}')
        (old_data / "helixsr").mkdir(parents=True)
        (old_data / "helixsr" / "scan-cache.json").write_text("{}")
        new_settings = home / "settings" / "helixsr-muse-plugin"
        new_data = home / "data" / "helixsr-muse-plugin"
        new_settings.mkdir(parents=True)
        new_data.mkdir(parents=True)

        decky.DECKY_HOME = str(home)

        def _move_contents(src, dst):
            # Emulate decky.migrate_*: move directory contents, remove source.
            src, dst = Path(src), Path(dst)
            if not src.is_dir():
                return {}
            dst.mkdir(parents=True, exist_ok=True)
            for child in list(src.iterdir()):
                shutil.move(str(child), str(dst / child.name))
            shutil.rmtree(src, ignore_errors=True)
            return {}

        decky.migrate_settings = lambda *ps: [_move_contents(p, new_settings) for p in ps]
        decky.migrate_runtime = lambda *ps: [_move_contents(p, new_data) for p in ps]

        p = self._plugin()
        asyncio.run(p._migration())
        self.assertEqual(json.loads((new_settings / "settings.json").read_text())["active_tag"], "v9.9.9")
        self.assertTrue((new_data / "helixsr" / "scan-cache.json").is_file())
        self.assertFalse(old_settings.exists())
        self.assertFalse(old_data.exists())

    def test_last_scan_corrupt_is_empty(self):
        p = self._plugin()
        cache = p._scan_cache_path()
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text("{not json", encoding="utf-8")

        async def go():
            p.loop = asyncio.get_event_loop()
            st = await p.get_last_scan()
            self.assertEqual(st["games"], [])
        asyncio.run(go())

    def test_cancel_scan(self):
        self._version_dir()
        home = make_steam_home(self.tmp)
        os.environ["DECKY_USER_HOME"] = str(home)
        p = self._plugin()

        def slow(*a, **k):
            import time
            time.sleep(3)
            return [], 0

        async def go():
            p.loop = asyncio.get_event_loop()
            with mock.patch.object(main.helixscan, "games", side_effect=slow):
                self.assertEqual(await p.start_scan(), {"ok": True})
                await asyncio.sleep(0.3)
                self.assertEqual(await p.cancel_scan(), {"ok": True})
                st = await self._wait_done(p.get_scan_status)
            self.assertTrue(st["cancelled"])
            self.assertFalse(st["ok"])
        asyncio.run(go())

    # --- build --------------------------------------------------------------

    def _fake_setup(self, d, body):
        (d / "helixsr-setup.sh").write_text("#!/bin/sh\n" + body)

    def _seed_dlss(self, d, content=b"fake-dlss"):
        (d / "nvngx_dlss.dll").write_bytes(content)
        return hashlib.sha256(content).hexdigest()

    async def _wait_build(self, p, cap=400):
        for _ in range(cap):
            alive = p._build_thread and p._build_thread.is_alive()
            if not alive and emitted:
                await asyncio.sleep(0.2)
                return
            await asyncio.sleep(0.05)
        raise AssertionError("timed out waiting for build")

    def test_build_rebuild_flag_and_cleanup(self):
        d = self._version_dir(tag="9.9.9", network=False)
        self._fake_setup(d, 'echo "setup args: $@"\nexit 0\n')
        digest = self._seed_dlss(d)
        p = self._plugin()

        async def go():
            p.loop = asyncio.get_event_loop()
            with mock.patch.object(main, "DLSS_SHA256", digest):
                r = await p.start_build(rebuild=True)
                self.assertTrue(r["ok"], r)
                await self._wait_build(p)
            log = Path(r["log"]).read_text()
            self.assertIn("setup args: --yes --rebuild --dlss", log)
            self.assertIn("once-used nvngx_dlss.dll removed", log)
            self.assertFalse((d / "nvngx_dlss.dll").exists())
            self.assertEqual(emitted[-1], ("helixsr_build_done", True, 0))
        asyncio.run(go())

    def test_build_refuses_when_built(self):
        self._version_dir(tag="9.9.9", network=True)
        p = self._plugin()

        async def go():
            p.loop = asyncio.get_event_loop()
            r = await p.start_build()
            self.assertFalse(r["ok"])
            self.assertIn("already built", r["error"])
        asyncio.run(go())

    def test_unload_kills_build(self):
        d = self._version_dir(tag="9.9.9", network=False)
        self._fake_setup(d, "echo started\nsleep 30\necho done\n")
        digest = self._seed_dlss(d)
        p = self._plugin()

        async def go():
            p.loop = asyncio.get_event_loop()
            with mock.patch.object(main, "DLSS_SHA256", digest):
                self.assertEqual((await p.start_build())["ok"], True)
                await asyncio.sleep(0.5)
                proc = p._build_proc
                self.assertIsNotNone(proc)
                await p._unload()
                p._build_thread.join(timeout=10)
                for _ in range(200):
                    if proc.returncode is not None:
                        break
                    await asyncio.sleep(0.05)
                self.assertEqual(proc.returncode, -15)
        asyncio.run(go())

    # --- routes + settings --------------------------------------------------

    async def _game_index(self, p, name):
        d = await p._active_dir()
        gl, _ = await asyncio.to_thread(main.helixscan.games, d)
        return next(i for i, g in enumerate(gl, 1) if g.name == name)

    def test_install_with_explicit_route(self):
        self._version_dir(tag="9.9.9")
        home = make_steam_home(self.tmp)
        os.environ["DECKY_USER_HOME"] = str(home)
        p = self._plugin()

        async def go():
            p.loop = asyncio.get_event_loop()
            idx_b = await self._game_index(p, "GameB")
            r = await p.install_games([idx_b], "dlss")
            self.assertTrue(r["ok"], r["output"])
            folder = home / ".local/share/lib/steamapps/common/GameB"
            self.assertTrue((folder / "dxgi.dll").exists())
            self.assertTrue((folder / "nvngx.dll").exists())
            r = await p.uninstall_games([idx_b])
            self.assertTrue(r["ok"], r["output"])
            self.assertFalse((folder / "dxgi.dll").exists())
            bad = await p.install_games([(await self._game_index(p, "GameA"))], "dlss")
            self.assertFalse(bad["ok"])
            self.assertIn("cannot use route", bad["output"])
        asyncio.run(go())

    def test_legacy_batch_skips_bad_route(self):
        self._version_dir(tag="9.9.9")
        home = make_steam_home(self.tmp)
        os.environ["DECKY_USER_HOME"] = str(home)
        p = self._plugin()
        ga = home / ".local/share/lib/steamapps/common/GameA/bin/amd_fidelityfx_upscaler_dx12.dll"
        original = ga.read_bytes()

        async def go():
            p.loop = asyncio.get_event_loop()
            idx_a = await self._game_index(p, "GameA")
            idx_b = await self._game_index(p, "GameB")
            r = await p.install_games([idx_a, idx_b], "dlss")
            self.assertTrue(r["ok"], r["output"])
            self.assertIn("skipped", r["output"])
            self.assertIn("GameA", r["output"])
            self.assertEqual(ga.read_bytes(), original)
            folder = home / ".local/share/lib/steamapps/common/GameB"
            self.assertTrue((folder / "dxgi.dll").exists())
        asyncio.run(go())

    def test_plugin_update_cancel(self):
        p = self._plugin()

        import time

        async def fake_check(force=False):
            return {"ok": True, "update_available": True, "latest": "9.9.9",
                    "url": "https://example.invalid/u.zip", "size": 0}

        class Slow:
            headers = {}

            def read(self, n):
                time.sleep(0.2)
                return b"x" * (1 << 20)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

        async def go():
            p.loop = asyncio.get_event_loop()
            p.check_plugin_update = fake_check
            with mock.patch.object(main.urllib.request, "urlopen", return_value=Slow()):
                self.assertEqual(await p.start_plugin_update(), {"ok": True, "version": "9.9.9"})
                await asyncio.sleep(0.3)
                self.assertEqual(await p.cancel_plugin_update(), {"ok": True})
                st = await self._wait_done(p.get_plugin_update_status)
            self.assertTrue(st["cancelled"])
            self.assertFalse(st["ok"])
            self.assertEqual(await p.cancel_plugin_update(),
                             {"ok": False, "error": "no update running"})
        asyncio.run(go())

    def test_game_settings_roundtrip(self):
        self._version_dir(tag="9.9.9")
        home = make_steam_home(self.tmp)
        os.environ["DECKY_USER_HOME"] = str(home)
        p = self._plugin()
        folder = home / ".local/share/lib/steamapps/common/GameB"

        async def go():
            p.loop = asyncio.get_event_loop()
            idx_b = await self._game_index(p, "GameB")
            self.assertTrue((await p.install_games([idx_b], "optiscaler"))["ok"])
            r = await p.get_game_settings(idx_b)
            self.assertTrue(r["ok"], r.get("error"))
            self.assertEqual(r["values"], {"Sharpener": "off", "Sharpness": "0.5",
                                           "UseGameSlider": "false", "WaveSize": "default"})
            w = await p.set_game_settings(idx_b, {"Sharpener": "nvidia", "Sharpness": "0.7",
                                                  "UseGameSlider": True, "WaveSize": "64"})
            self.assertTrue(w["ok"], w.get("output"))
            r2 = await p.get_game_settings(idx_b)
            self.assertEqual(r2["values"], {"Sharpener": "nvidia", "Sharpness": "0.7",
                                            "UseGameSlider": "true", "WaveSize": "64"})
            text = (folder / "HelixSR" / "helixsr.ini").read_text()
            self.assertIn("Sharpener = nvidia", text)
            self.assertIn("; HelixSR settings", text)
            bad = await p.set_game_settings(idx_b, {"Sharpener": "wat"})
            self.assertFalse(bad["ok"])
            self.assertIn("bad Sharpener", bad["output"])
            bad = await p.set_game_settings(idx_b, {"Sharpness": "2"})
            self.assertFalse(bad["ok"])
            bad = await p.set_game_settings(idx_b, {"Nope": "1"})
            self.assertFalse(bad["ok"])
        asyncio.run(go())

    def test_game_settings_needs_install(self):
        self._version_dir(tag="9.9.9")
        home = make_steam_home(self.tmp)
        os.environ["DECKY_USER_HOME"] = str(home)
        p = self._plugin()

        async def go():
            p.loop = asyncio.get_event_loop()
            idx_b = await self._game_index(p, "GameB")
            r = await p.get_game_settings(idx_b)
            self.assertFalse(r["ok"])
            self.assertIn("install", r["error"])
        asyncio.run(go())

    def test_set_ini_value(self):
        template = ("; HelixSR settings.\n[Sharpening]\n;Sharpener = nvidia\n"
                    ";Sharpness = 0.5\n\n[Compatibility]\n;WaveSize = default\n")
        out = main._set_ini_value(template, "Sharpening", "Sharpener", "rcas")
        self.assertIn("Sharpener = rcas", out)
        self.assertIn("; HelixSR settings.", out)
        self.assertNotIn(";Sharpener", out)
        # duplicates collapse to one active line
        dup = "[Sharpening]\nSharpener = off\nSharpener = nvidia\n"
        out = main._set_ini_value(dup, "Sharpening", "Sharpener", "off")
        self.assertEqual(out.count("Sharpener ="), 1)
        # missing section is appended
        out = main._set_ini_value("[Sharpening]\nSharpener = off\n", "Compatibility", "WaveSize", "64")
        self.assertIn("[Compatibility]\nWaveSize = 64", out)
        # CRLF stays CRLF
        out = main._set_ini_value("[Sharpening]\r\n;Sharpness = 0.5\r\n", "Sharpening", "Sharpness", "0.7")
        self.assertIn("Sharpness = 0.7", out)
        self.assertNotIn("\n\n", out.replace("\r\n", "\n"))

    def test_repair_exec_bits(self):
        d = self.tmp / "healed"
        (d / "setup/lib/model").mkdir(parents=True)
        t = d / "setup/lib/model/launch_synth"; t.write_bytes(b"elf"); t.chmod(0o644)
        self.assertEqual(main._repair_exec_bits(d), ["setup/lib/model/launch_synth"])
        self.assertEqual(_mode(t), 0o755)
        self.assertEqual(main._repair_exec_bits(d), [])

    # --- logs, presets, verify, cancel, reset ----------------------------------

    def test_game_log_and_fix(self):
        self._version_dir(tag="9.9.9")
        home = make_steam_home(self.tmp)
        os.environ["DECKY_USER_HOME"] = str(home)
        p = self._plugin()
        folder = home / ".local/share/lib/steamapps/common/GameB"

        async def go():
            p.loop = asyncio.get_event_loop()
            idx = await self._game_index(p, "GameB")
            self.assertTrue((await p.install_games([idx], "optiscaler"))["ok"])
            r = await p.get_game_log(idx)
            self.assertFalse(r["ok"])
            self.assertIn("launch the game", r["error"])
            (folder / "HelixSR" / "helixsr.log").write_text(
                "helixsr 1.0\nnetwork kernels wave32, all good\n")
            r = await p.get_game_log(idx)
            self.assertTrue(r["ok"], r.get("error"))
            self.assertEqual(len(r["logs"]), 1)
            self.assertIn("wave32", r["logs"][0]["tail"])
            self.assertEqual([f["id"] for f in r["fixes"]], ["wave64"])
            w = await p.apply_log_fix(idx, "wave64")
            self.assertTrue(w["ok"], w.get("output"))
            r2 = await p.get_game_settings(idx)
            self.assertEqual(r2["values"]["WaveSize"], "64")
            self.assertEqual(r2["values"]["Sharpener"], "off")
            (folder / "HelixSR" / "helixsr.log").write_text("helixsr 1.0\nall good\n")
            stale = await p.apply_log_fix(idx, "wave64")
            self.assertFalse(stale["ok"])
            self.assertIn("no longer present", stale["output"])
            unknown = await p.apply_log_fix(idx, "nope")
            self.assertFalse(unknown["ok"])
        asyncio.run(go())

    def test_ini_presets(self):
        p = self._plugin()

        async def go():
            p.loop = asyncio.get_event_loop()
            self.assertEqual((await p.get_ini_presets())["presets"], {})
            bad = await p.save_ini_preset("", {"WaveSize": "64"})
            self.assertFalse(bad["ok"])
            bad = await p.save_ini_preset("x", {"WaveSize": "128"})
            self.assertFalse(bad["ok"])
            ok = await p.save_ini_preset("perf", {"Sharpener": "rcas", "Sharpness": "0.8",
                                                  "UseGameSlider": False, "WaveSize": "64"})
            self.assertTrue(ok["ok"])
            self.assertIn("perf", (await p.get_ini_presets())["presets"])
            rm = await p.delete_ini_preset("perf")
            self.assertNotIn("perf", rm["presets"])
        asyncio.run(go())

    def test_preset_apply_and_partial_set(self):
        self._version_dir(tag="9.9.9")
        home = make_steam_home(self.tmp)
        os.environ["DECKY_USER_HOME"] = str(home)
        p = self._plugin()

        async def go():
            p.loop = asyncio.get_event_loop()
            idx = await self._game_index(p, "GameB")
            self.assertTrue((await p.install_games([idx], "optiscaler"))["ok"])
            await p.save_ini_preset("q", {"Sharpener": "nvidia", "Sharpness": "0.6",
                                          "UseGameSlider": True, "WaveSize": "default"})
            w = await p.apply_ini_preset(idx, "q")
            self.assertTrue(w["ok"], w.get("output"))
            r = await p.get_game_settings(idx)
            self.assertEqual(r["values"]["Sharpener"], "nvidia")
            # partial set leaves the rest untouched
            w = await p.set_game_settings(idx, {"WaveSize": "64"})
            self.assertTrue(w["ok"], w.get("output"))
            r = await p.get_game_settings(idx)
            self.assertEqual(r["values"]["WaveSize"], "64")
            self.assertEqual(r["values"]["Sharpener"], "nvidia")
            missing = await p.apply_ini_preset(idx, "nope")
            self.assertFalse(missing["ok"])
        asyncio.run(go())

    def test_ini_schemas_and_translation(self):
        full_legacy = {"Sharpener": "rcas", "Sharpness": "0.8",
                       "UseGameSlider": True, "WaveSize": "64"}
        norm, err = main._normalize_ini_values(full_legacy, "legacy")
        self.assertEqual(err, "")
        self.assertEqual(norm, {"Sharpener": "rcas", "Sharpness": "0.8",
                                "UseGameSlider": "true", "WaveSize": "64"})
        # modern drops the slider, WaveSize auto instead of default
        bad, err = main._normalize_ini_values({"UseGameSlider": "false"}, "modern")
        self.assertIsNone(bad)
        self.assertIn("1.9.0", err)
        bad, err = main._normalize_ini_values({"WaveSize": "default"}, "modern")
        self.assertIsNone(bad)
        self.assertIn("auto", err)
        norm, err = main._normalize_ini_values({"Sharpener": "nvidia", "Sharpness": "0.6",
                                                "WaveSize": "auto"}, "modern")
        self.assertEqual(err, "")
        self.assertEqual(norm, {"Sharpener": "nvidia", "Sharpness": "0.6", "WaveSize": "auto"})
        # translation both ways, with default fill
        full, err = main._translate_ini_values(full_legacy, "modern")
        self.assertEqual(err, "")
        self.assertEqual(full, {"Sharpener": "rcas", "Sharpness": "0.8", "WaveSize": "64"})
        modern = {"Sharpener": "off", "Sharpness": "0.1", "WaveSize": "auto"}
        full, err = main._translate_ini_values(modern, "legacy")
        self.assertEqual(err, "")
        self.assertEqual(full, {"Sharpener": "off", "Sharpness": "0.1",
                                "UseGameSlider": "false", "WaveSize": "default"})
        full, err = main._translate_ini_values({"Sharpener": "bogus"}, "modern")
        self.assertIsNone(full)
        self.assertIn("bogus", err)
        full, err = main._translate_ini_values("nope", "legacy")
        self.assertIsNone(full)
        # modern read: slider ignored, auto accepted, 0.1 default kept
        p = Path(tempfile.mkdtemp(prefix="hxini-")) / "helixsr.ini"
        self.addCleanup(shutil.rmtree, p.parent, True)
        p.write_text("[Sharpening]\nSharpener = nvidia\nSharpness = 0.6\nUseGameSlider = true\n"
                     "[Compatibility]\nWaveSize = auto\n")
        vals, note = main._read_ini_values(p, "modern")
        self.assertEqual(vals, {"Sharpener": "nvidia", "Sharpness": "0.6", "WaveSize": "auto"})
        vals, _ = main._read_ini_values(p, "legacy")
        self.assertEqual(vals["UseGameSlider"], "true")
        self.assertEqual(vals["WaveSize"], "default")

    def test_ini_layout_per_game(self):
        import types
        base = Path(tempfile.mkdtemp(prefix="hxly-"))
        self.addCleanup(shutil.rmtree, base, True)
        root = base / "helixsr"
        legacy = root / "HelixSR-9.9.8"
        legacy.mkdir(parents=True)
        (legacy / "helixsr-setup.sh").write_text("#!/bin/sh\n")
        (legacy / "amd_fidelityfx_dx12.dll").write_bytes(b"\x00HelixSR 9.9.8\x00" + b"\x00" * 100)
        modern = root / "HelixSR-1.9.0"
        (modern / "setup").mkdir(parents=True)
        (modern / "setup/helixsr_setup.py").write_text("# layout marker\n")
        (modern / "helixsr-setup.sh").write_text("#!/bin/sh\n")
        (modern / "amd_fidelityfx_dx12.dll").write_bytes(b"\x00HelixSR 1.9.0\x00" + b"\x00" * 100)
        gfolder = base / "GameX"
        (gfolder / "HelixSR").mkdir(parents=True)
        g = types.SimpleNamespace(target=None, fsr_targets=[],
                                  folder=gfolder, name="GameX")
        with mock.patch.dict(os.environ, {"DECKY_PLUGIN_RUNTIME_DIR": str(base)}):
            # no marker: falls back to the given (active) version's layout
            self.assertEqual(main._ini_layout_for(g, legacy), "legacy")
            self.assertEqual(main._ini_layout_for(g, modern), "modern")
            # marker records the installing version: a legacy install stays
            # legacy even with a modern version active, and vice versa
            (gfolder / "HelixSR" / "helixsr-install.json").write_text(
                json.dumps({"helixsr": "9.9.8", "files": [], "route": "direct"}))
            self.assertEqual(main._ini_layout_for(g, modern), "legacy")
            (gfolder / "HelixSR" / "helixsr-install.json").write_text(
                json.dumps({"helixsr": "1.9.0", "files": [], "route": "direct"}))
            self.assertEqual(main._ini_layout_for(g, legacy), "modern")

    MODERN_STUB_MODULE = '''MARKER = "helixsr-install.json"
LOADER_NAMES = ("dxgi.dll",)

class G:
    def __init__(self, d):
        import json
        from pathlib import Path
        spec = json.loads((Path(d) / "game.json").read_text())
        self.name = spec["name"]
        self.folder = Path(d)
        self.target = None
        self.fsr_targets = [Path(d) / n for n in spec.get("fsr", [])]
        self.route = spec.get("route") or ("direct" if self.fsr_targets else "unsupported")
        self.inputs = []

    def installed(self):
        return False

    def label(self):
        return "FSR 3.1 game" if self.fsr_targets else "DLSS only: use HelixSR Desktop (HelixSR Scaler)"

    def old_install(self):
        return None


CALLS = []


def games(extra):
    print("[helixsr-setup] scanned")
    return [G(d) for d in extra]


def install(g, here):
    CALLS.append(g.name)
    print(f"[helixsr-setup] installed {g.name}")


def uninstall(g):
    print(f"[helixsr-setup] uninstalled {g.name}")
'''

    def _modern_dir(self, tag="1.9.0"):
        root = Path(os.environ["DECKY_PLUGIN_RUNTIME_DIR"]) / "helixsr"
        d = root / f"HelixSR-{tag}"
        (d / "setup").mkdir(parents=True, exist_ok=True)
        (d / "setup/helixsr_setup.py").write_text("# layout marker\n")
        (d / "helixsr-setup.sh").write_text("#!/bin/sh\n")
        (d / "amd_fidelityfx_dx12.dll").write_bytes(
            b"\x00HelixSR " + tag.encode() + b"\x00" + b"\x00" * 100 + b"HXSRNET1" + b"\x00" * 24)
        (d / "setup/helixsr_install.py").write_text(self.MODERN_STUB_MODULE)
        return d

    def _stub_game_dir(self, name, fsr=(), route=None):
        d = self.tmp / f"stub-{name}"
        d.mkdir(parents=True, exist_ok=True)
        spec = {"name": name, "fsr": list(fsr)}
        if route:
            spec["route"] = route
        (d / "game.json").write_text(json.dumps(spec))
        return str(d)

    def test_modern_install_validation_and_batch_skip(self):
        d = self._modern_dir()
        fa = self._stub_game_dir("HasFsr", fsr=["up.dll"])
        fb = self._stub_game_dir("NoFsr", route="unsupported")
        p = self._plugin(active="v1.9.0")
        p.settings.setSetting("extra_folders", [fa, fb])

        async def go():
            p.loop = asyncio.get_event_loop()
            st = await self._rescan(p)
            self.assertTrue(st["ok"], st.get("error"))
            idx = {g["name"]: g["index"] for g in st["games"]}
            mod = main.helixref.install_module(d)
            # explicit legacy route on an FSR game: ignored, drop-in installs
            mod.CALLS.clear()
            r = await p.install_games([idx["HasFsr"]], "optiscaler")
            self.assertTrue(r["ok"], r["output"])
            self.assertEqual(mod.CALLS, ["HasFsr"])
            # explicit route on a game without FSR: clear error, nothing installed
            mod.CALLS.clear()
            bad = await p.install_games([idx["NoFsr"]], "direct")
            self.assertFalse(bad["ok"])
            self.assertIn("HelixSR Scaler", bad["output"])
            self.assertEqual(mod.CALLS, [])
            # batch: compatible games install, others skipped with a note
            mod.CALLS.clear()
            r = await p.install_games([idx["HasFsr"], idx["NoFsr"]], "")
            self.assertTrue(r["ok"], r["output"])
            self.assertEqual(mod.CALLS, ["HasFsr"])
            self.assertIn("skipped", r["output"])
            self.assertIn("NoFsr", r["output"])
        asyncio.run(go())

    SCALER_GAME_MODULE = '''MARKER = "helixsr-install.json"
LOADER_NAMES = ("dxgi.dll",)

class G:
    def __init__(self, d):
        import json
        from pathlib import Path
        spec = json.loads((Path(d) / "game.json").read_text())
        self.name = spec["name"]
        self.folder = Path(d)
        self.target = Path(d)
        self.fsr_targets = [Path(d) / n for n in spec.get("fsr", [])]
        self.route = spec.get("route") or ("direct" if self.fsr_targets else "unsupported")
        self.inputs = spec.get("inputs", [])

    def installed(self):
        import os
        from pathlib import Path as _P
        return (_P(self.target) / "HelixSR" / "helixsr-scaler.json").exists()

    def label(self):
        return "FSR 3.1 game" if self.fsr_targets else "DLSS only game"

    def old_install(self):
        return None


def games(extra):
    print("[helixsr-setup] scanned")
    return [G(d) for d in extra]


def install(g, here):
    print(f"[helixsr-setup] installed {g.name}")


def uninstall(g):
    print(f"[helixsr-setup] uninstalled {g.name}")
'''

    def _scaler_env(self, stag="1.0.3", htag="1.9.0"):
        root = Path(os.environ["DECKY_PLUGIN_RUNTIME_DIR"])
        sdir = root / "scaler" / f"HelixSR-Scaler-{stag}"
        (sdir / "OptiScaler" / "streamline").mkdir(parents=True)
        (sdir / "OptiScaler.dll").write_bytes(b"OptiScaler fake")
        (sdir / "OptiScaler.ini").write_text("[HelixSR]\nInt8=auto\nWaveSize=auto\nFrameGeneration=auto\n")
        (sdir / "setup_linux.sh").write_text("#!/bin/sh\n")
        (sdir / "OptiScaler" / "streamline" / "sl.dlss.dll").write_bytes(b"sl")
        (sdir / "HelixSR-Scaler-package.txt").write_text(
            "OptiScaler.dll\nOptiScaler.ini\nOptiScaler/streamline/sl.dlss.dll\nsetup_linux.sh\n")
        hdir = root / "helixsr" / f"HelixSR-{htag}"
        (hdir / "setup").mkdir(parents=True, exist_ok=True)
        (hdir / "setup/helixsr_setup.py").write_text("# layout marker\n")
        (hdir / "helixsr-setup.sh").write_text("#!/bin/sh\n")
        (hdir / "amd_fidelityfx_dx12.dll").write_bytes(
            b"\x00HelixSR " + htag.encode() + b"\x00" + b"\x00" * 100 + b"HXSRNET1" + b"\x00" * 24)
        (hdir / "helixsr-fg").mkdir(exist_ok=True)
        (hdir / "helixsr-fg" / "nvexec64.dll").write_bytes(b"nv")
        (hdir / "setup/helixsr_install.py").write_text(self.SCALER_GAME_MODULE)
        return sdir, hdir

    def _stub_json_game(self, name, inputs=("DLSS",), fsr=()):
        d = self.tmp / f"stub-{name}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "game.json").write_text(json.dumps({"name": name, "fsr": list(fsr),
                                                 "inputs": list(inputs)}))
        return str(d)

    def test_scaler_install_uninstall_roundtrip(self):
        sdir, hdir = self._scaler_env()
        ga = self._stub_json_game("DlssGame", inputs=["DLSS"])
        (Path(ga) / "dxgi.dll").write_bytes(b"the game's own dxgi")
        p = self._plugin(active="v1.9.0")
        p.settings.setSetting("scaler_tag", "v1.0.3")
        p.settings.setSetting("extra_folders", [ga])

        async def go():
            p.loop = asyncio.get_event_loop()
            st = await self._rescan(p)
            self.assertTrue(st["ok"], st.get("error"))
            idx = {g["name"]: g["index"] for g in st["games"]}
            r = await p.install_games([idx["DlssGame"]], "scaler")
            self.assertTrue(r["ok"], r["output"])
            self.assertIn("dxgi.dll loads by itself", r["output"])
            exe = Path(ga)
            self.assertTrue((exe / "dxgi.dll").is_file())
            self.assertNotEqual((exe / "dxgi.dll").read_bytes(), b"the game's own dxgi")
            self.assertTrue((exe / "OptiScaler.ini").is_file())
            self.assertTrue((exe / "OptiScaler" / "streamline" / "sl.dlss.dll").is_file())
            self.assertTrue((exe / "HelixSR" / "helixsr.dll").is_file())
            self.assertTrue((exe / "HelixSR" / "helixsr-fg" / "nvexec64.dll").is_file())
            self.assertEqual((exe / "HelixSR" / "backup" / "dxgi.dll").read_bytes(),
                             b"the game's own dxgi")
            rec = json.loads((exe / "HelixSR" / "helixsr-scaler.json").read_text())
            self.assertEqual(rec["loader"], "dxgi.dll")
            self.assertEqual(rec["scaler"], "v1.0.3")
            self.assertEqual(rec["helixsr"], "1.9.0")
            self.assertIsNone(rec["launch_options"])
            # settings roundtrip on the scaler ini
            s = await p.get_scaler_settings(idx["DlssGame"])
            self.assertTrue(s["ok"], s.get("error"))
            self.assertEqual(s["values"], {"Int8": "auto", "WaveSize": "auto",
                                           "FrameGeneration": "auto", "QuickFlow": "auto"})
            w = await p.set_scaler_settings(idx["DlssGame"], {"Int8": "on"})
            self.assertTrue(w["ok"], w.get("output"))
            s = await p.get_scaler_settings(idx["DlssGame"])
            self.assertEqual(s["values"]["Int8"], "on")
            w = await p.set_scaler_settings(idx["DlssGame"], {"QuickFlow": "fastest"})
            self.assertTrue(w["ok"], w.get("output"))
            bad = await p.set_scaler_settings(idx["DlssGame"], {"QuickFlow": "turbo"})
            self.assertFalse(bad["ok"])
            bad = await p.set_scaler_settings(idx["DlssGame"], {"Nope": 1})
            self.assertFalse(bad["ok"])
            bad = await p.set_scaler_settings(idx["DlssGame"], {"Int8": "sometimes"})
            self.assertFalse(bad["ok"])
            # scan overlay: installed as scaler with launch options
            st2 = await self._rescan(p)
            g = next(x for x in st2["games"] if x["name"] == "DlssGame")
            self.assertTrue(g["installed"])
            self.assertEqual(g["route"], "scaler")
            self.assertEqual(g["launch_options"], "")
            self.assertIn("1.9.0", g["installed_versions"])
            self.assertEqual(main._scaler_launch_options("dxgi.dll"), None)
            self.assertEqual(main._scaler_launch_options("winmm.dll"),
                             'WINEDLLOVERRIDES="winmm=n,b" %command%')
            # verify passes, then fails honestly when the loader is deleted
            v = await p.verify_installs()
            self.assertTrue(v["ok"])
            self.assertTrue(v["games"][0]["ok"], v["games"])
            (exe / "dxgi.dll").unlink()
            v = await p.verify_installs()
            self.assertFalse(v["games"][0]["ok"])
            # uninstall removes everything and restores the backup
            u = await p.uninstall_games([idx["DlssGame"]])
            self.assertTrue(u["ok"], u["output"])
            self.assertEqual((exe / "dxgi.dll").read_bytes(), b"the game's own dxgi")
            self.assertFalse((exe / "HelixSR").exists())
            self.assertFalse((exe / "OptiScaler.ini").exists())
            self.assertFalse((exe / "OptiScaler").exists())
        asyncio.run(go())

    def test_scaler_install_guards(self):
        sdir, hdir = self._scaler_env()
        ga = self._stub_json_game("DlssGame", inputs=["DLSS"])
        gf = self._stub_json_game("FsrGame", inputs=[], fsr=["up.dll"])
        p = self._plugin(active="v1.9.0")
        p.settings.setSetting("scaler_tag", "v1.0.3")
        p.settings.setSetting("extra_folders", [ga, gf])

        async def go():
            p.loop = asyncio.get_event_loop()
            st = await self._rescan(p)
            idx = {g["name"]: g["index"] for g in st["games"]}
            # FSR games are refused (drop-in instead), nothing installed
            bad = await p.install_games([idx["FsrGame"]], "scaler")
            self.assertFalse(bad["ok"])
            self.assertIn("drop-in", bad["output"])
            # no scaler downloaded
            p.settings.setSetting("scaler_tag", "")
            bad = await p.install_games([idx["DlssGame"]], "scaler")
            self.assertFalse(bad["ok"])
            self.assertIn("download HelixSR Scaler", bad["output"])
        asyncio.run(go())

    def test_scaler_migration_versions(self):
        import types
        sdir, hdir = self._scaler_env()
        ga = self._stub_json_game("DlssGame", inputs=["DLSS"])
        (Path(ga) / "HelixSR").mkdir(parents=True)
        marker = {"scaler": "v1.0.3", "helixsr": "1.9.0", "loader": "dxgi.dll", "files": [],
                  "backup": {}, "launch_options": "WINEDLLOVERRIDES=\"dxgi=n,b\" %command%"}
        (Path(ga) / "HelixSR" / "helixsr-scaler.json").write_text(json.dumps(marker))
        g = types.SimpleNamespace(target=Path(ga), fsr_targets=[], folder=Path(ga), name="DlssGame")
        self.assertEqual(main._marker_versions(g), {"1.9.0"})
        p = self._plugin(active="v1.9.0")
        p.settings.setSetting("scaler_tag", "v1.0.3")
        p.settings.setSetting("extra_folders", [ga])
        # current install: nothing to do (no bogus migrate count)
        p._mig = main._new_mig_state()
        p._migration_run(hdir, "v1.9.0", "1.9.0", [ga])
        self.assertTrue(p._mig["done"])
        self.assertTrue(p._mig["ok"])
        self.assertEqual(p._mig["results"], [])
        # outdated install: reinstalled into the target build, marker updated
        marker["helixsr"] = "1.8.0"
        (Path(ga) / "HelixSR" / "helixsr-scaler.json").write_text(json.dumps(marker))
        p._mig = main._new_mig_state()
        p._migration_run(hdir, "v1.9.0", "1.9.0", [ga])
        self.assertTrue(p._mig["done"])
        self.assertTrue(p._mig["ok"], p._mig["results"])
        self.assertEqual(len(p._mig["results"]), 1)
        self.assertTrue(p._mig["results"][0]["ok"])
        rec = json.loads((Path(ga) / "HelixSR" / "helixsr-scaler.json").read_text())
        self.assertEqual(rec["helixsr"], "1.9.0")
        self.assertTrue((Path(ga) / "dxgi.dll").is_file())
        self.assertTrue((Path(ga) / "HelixSR" / "helixsr.dll").is_file())

    def _scaler_tar_bytes(self, top="HelixSR-Scaler-1.0.3-linux", valid=True):
        import io
        buf = io.BytesIO()
        with __import__("tarfile").open(fileobj=buf, mode="w:gz") as tf:
            def add(name, data=b"x", mode=0o644):
                info = __import__("tarfile").TarInfo(top + "/" + name)
                info.size = len(data)
                info.mode = mode
                tf.addfile(info, io.BytesIO(data))
            if valid:
                add("OptiScaler.dll", b"OptiScaler fake")
                add("OptiScaler.ini", b"[HelixSR]\n")
                add("setup_linux.sh", b"#!/bin/sh\n", 0o755)
            else:
                add("README.txt", b"nope")
        return buf.getvalue()

    def test_scaler_asset_match(self):
        rel = {"tag_name": "v1.0.3", "assets": [
            {"name": "HelixSR-Scaler-1.0.3.zip", "size": 132504212,
             "browser_download_url": "win"},
            {"name": "HelixSR-Scaler-1.0.3-linux.tar.gz", "size": 132489118,
             "browser_download_url": "linux"},
            {"name": "SHA256SUMS.txt", "size": 100, "browser_download_url": "s"}]}
        a = main._scaler_asset(rel)
        self.assertEqual(a["browser_download_url"], "linux")
        self.assertIsNone(main._scaler_asset(
            {"tag_name": "v1.0.0", "assets": [{"name": "HelixSR-Scaler-1.0.0.zip"}]}))
        self.assertIsNone(main._scaler_asset({}))

    def test_scaler_releases_and_download(self):
        payload = self._scaler_tar_bytes()
        p = self._plugin()
        listing = [{"tag_name": "v1.0.3", "draft": False, "name": "N",
                     "published_at": "2026-10-15T15:07:20Z",
                     "assets": [{"name": "HelixSR-Scaler-1.0.3-linux.tar.gz",
                                 "size": len(payload),
                                 "browser_download_url": "https://example.invalid/s.tar.gz"}],
                     "body": "notes"}]

        class Resp:
            def __init__(self, data):
                self._data = data
                self.headers = {"Content-Length": str(len(data))}

            def read(self, n):
                chunk, self._data = self._data[:n], self._data[n:]
                return chunk

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

        async def go():
            p.loop = asyncio.get_event_loop()
            with mock.patch.object(main, "_fetch_json", return_value=listing):
                r = await p.get_scaler_releases()
            self.assertEqual(r["error"], "")
            self.assertEqual(len(r["releases"]), 1)
            rel = r["releases"][0]
            self.assertEqual(rel["tar_name"], "HelixSR-Scaler-1.0.3-linux.tar.gz")
            self.assertIn("notes", rel["body"])
            with mock.patch.object(main.urllib.request, "urlopen", return_value=Resp(payload)):
                self.assertEqual(await p.start_scaler_download("v1.0.3", rel["tar_url"]),
                                 {"ok": True})
                st = await self._wait_done(p.get_scaler_download_status)
            self.assertTrue(st["ok"], st["error"])
            self.assertEqual(st["dir"], "HelixSR-Scaler-1.0.3-linux")
            s = await p.get_scaler_state()
            self.assertEqual(s["tag"], "v1.0.3")
            self.assertEqual(s["dir"], "HelixSR-Scaler-1.0.3-linux")
            self.assertTrue(s["files_ok"])
            # bad layout is rejected
            bad = self._scaler_tar_bytes(top="HelixSR-Scaler-9.9.9", valid=False)
            with mock.patch.object(main.urllib.request, "urlopen", return_value=Resp(bad)):
                self.assertEqual(await p.start_scaler_download(
                    "v9.9.9", "https://example.invalid/bad.tgz"), {"ok": True})
                st = await self._wait_done(p.get_scaler_download_status)
            self.assertFalse(st["ok"])
            self.assertIn("Scaler layout", st["error"])
        asyncio.run(go())

    def test_scaler_download_cancel(self):
        p = self._plugin()

        import time

        class Slow:
            headers = {}

            def read(self, n):
                time.sleep(0.2)
                return b"x" * (1 << 20)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

        async def go():
            p.loop = asyncio.get_event_loop()
            with mock.patch.object(main.urllib.request, "urlopen", return_value=Slow()):
                self.assertEqual(await p.start_scaler_download("v9.9.9", "https://example.invalid/x.tgz"),
                                 {"ok": True})
                await asyncio.sleep(0.3)
                self.assertEqual(await p.cancel_scaler_download(), {"ok": True})
                st = await self._wait_done(p.get_scaler_download_status)
            self.assertTrue(st["cancelled"])
            self.assertFalse(st["ok"])
            self.assertEqual(await p.cancel_scaler_download(),
                             {"ok": False, "error": "no scaler download running"})
        asyncio.run(go())

    def test_hash_pins_wellformed(self):
        # A truncated pin fails every download with a checksum error (seen
        # live with DLSSG_SHA256); trip over it here instead of on Deck.
        for pin in (main.DLSS_SHA256, main.DLSSG_SHA256, main.XESS_SHA256):
            self.assertEqual(len(pin), 64)
            int(pin, 16)

    def test_fetch_zip_entry(self):
        import hashlib
        import http.server
        import threading
        import zipfile
        d = Path(tempfile.mkdtemp(prefix="hxrange-"))
        self.addCleanup(shutil.rmtree, d, True)
        stored = b"S" * 5000
        deflated = bytes(range(256)) * 40
        with zipfile.ZipFile(d / "a.zip", "w") as z:
            z.writestr("s/stored.bin", stored, compress_type=zipfile.ZIP_STORED)
            z.writestr("f/deflated.bin", deflated, compress_type=zipfile.ZIP_DEFLATED)
            z.writestr("top.txt", b"hi")
        data = (d / "a.zip").read_bytes()

        class RangeHandler(http.server.BaseHTTPRequestHandler):
            def _serve(self):
                body = data
                total = len(body)
                rng = (self.headers.get("Range") or "").strip()
                if rng.startswith("bytes="):
                    start, end = rng[len("bytes="):].split("-", 1)
                    start, end = int(start or 0), int(end or total - 1)
                    chunk = body[start:end + 1]
                    self.send_response(206)
                    self.send_header("Content-Range",
                                     f"bytes {start}-{start + len(chunk) - 1}/{total}")
                    self.send_header("Content-Length", str(len(chunk)))
                else:
                    chunk = body
                    self.send_response(200)
                    self.send_header("Content-Length", str(total))
                self.send_header("Accept-Ranges", "bytes")
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(chunk)

            do_GET = _serve
            do_HEAD = _serve

            def log_message(self, *a):
                pass

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), RangeHandler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        url = f"http://127.0.0.1:{srv.server_port}/a.zip"
        self.assertEqual(main._fetch_zip_entry(url, "s/stored.bin", hashlib.sha256(stored).hexdigest()),
                         stored)
        self.assertEqual(main._fetch_zip_entry(url, "f/deflated.bin", hashlib.sha256(deflated).hexdigest()),
                         deflated)
        with self.assertRaises(OSError):
            main._fetch_zip_entry(url, "nope.bin", "0" * 64)
        with self.assertRaises(OSError):
            main._fetch_zip_entry(url, "top.txt", "0" * 64)

    def test_releases_skip_prereleases_by_default(self):
        p = self._plugin()
        rels = [
            {"tag_name": "v1.9.3", "draft": False, "prerelease": False, "name": "N",
             "published_at": "2026-10-16T00:00:00Z",
             "assets": [{"name": "HelixSR-1.9.3.zip", "size": 10, "browser_download_url": "u3"}],
             "body": ""},
            {"tag_name": "v1.9.2", "draft": False, "prerelease": True, "name": "P",
             "published_at": "2026-10-16T00:00:00Z",
             "assets": [{"name": "HelixSR-1.9.2.zip", "size": 10, "browser_download_url": "u2"}],
             "body": ""},
            {"tag_name": "v1.9.1", "draft": True, "prerelease": False, "name": "D",
             "published_at": "2026-10-16T00:00:00Z",
             "assets": [{"name": "HelixSR-1.9.1.zip", "size": 10, "browser_download_url": "u1"}],
             "body": ""},
        ]

        async def go():
            p.loop = asyncio.get_event_loop()
            with mock.patch.object(main, "_fetch_json", return_value=rels):
                r = await p.get_releases()
            self.assertEqual([x["tag"] for x in r["releases"]], ["v1.9.3"])
            with mock.patch.object(main, "_fetch_json", return_value=rels):
                r = await p.get_releases(True)
            self.assertEqual([x["tag"] for x in r["releases"]], ["v1.9.3", "v1.9.2"])
        asyncio.run(go())

    def test_scaler_releases_skip_prereleases_by_default(self):
        p = self._plugin()
        rels = [
            {"tag_name": "v1.0.3", "draft": False, "prerelease": False, "name": "N",
             "published_at": "2026-10-15T00:00:00Z",
             "assets": [{"name": "HelixSR-Scaler-1.0.3-linux.tar.gz", "size": 10,
                         "browser_download_url": "u3"}],
             "body": ""},
            {"tag_name": "v1.0.4", "draft": False, "prerelease": True, "name": "P",
             "published_at": "2026-10-16T00:00:00Z",
             "assets": [{"name": "HelixSR-Scaler-1.0.4-linux.tar.gz", "size": 10,
                         "browser_download_url": "u4"}],
             "body": ""},
            {"tag_name": "nightly", "draft": False, "prerelease": True, "name": "B",
             "published_at": "2026-10-16T00:00:00Z",
             "assets": [{"name": "OptiScaler_v10.0.0-pre1.7z", "size": 10,
                         "browser_download_url": "u7"}],
             "body": ""},
        ]

        async def go():
            p.loop = asyncio.get_event_loop()
            with mock.patch.object(main, "_fetch_json", return_value=rels):
                r = await p.get_scaler_releases()
            self.assertEqual([x["tag"] for x in r["releases"]], ["v1.0.3"])
            with mock.patch.object(main, "_fetch_json", return_value=rels):
                r = await p.get_scaler_releases(True)
            self.assertEqual([x["tag"] for x in r["releases"]], ["v1.0.3", "v1.0.4"])
        asyncio.run(go())

    def test_scaler_bundled_preferred(self):
        import types
        sdir, hdir = self._scaler_env()
        gt = self.tmp / "bundled-game"
        gt.mkdir()
        g = types.SimpleNamespace(name="Bundled", target=gt, fsr_targets=[], folder=gt)
        (hdir / "helixsr-scaler").mkdir()
        (hdir / "helixsr-scaler" / "OptiScaler.dll").write_bytes(b"bundled scaler")
        (hdir / "helixsr-scaler" / "OptiScaler.ini").write_text("[HelixSR]\n")
        p = self._plugin(active="v1.9.0")
        # no scaler_tag set and the separate download removed: bundled wins
        shutil.rmtree(sdir)
        log = p._scaler_install_one(g, hdir)
        self.assertIn("installed", log)
        self.assertEqual((gt / "dxgi.dll").read_bytes(), b"bundled scaler")
        rec = json.loads((gt / "HelixSR" / "helixsr-scaler.json").read_text())
        self.assertEqual(rec["scaler"], "bundled")

    def test_preset_case_insensitive_sorted_capped(self):
        p = self._plugin()

        async def go():
            p.loop = asyncio.get_event_loop()
            d1 = (await p.get_ini_presets())["defaults"]
            self.assertEqual(d1, main.INI_DEFAULTS)
            d1["Sharpener"] = "nvidia"
            self.assertEqual(main.INI_DEFAULTS["Sharpener"], "off")
            self.assertEqual((await p.get_ini_presets())["defaults"], main.INI_DEFAULTS)
            vals = {"Sharpener": "off", "Sharpness": "0.5",
                    "UseGameSlider": False, "WaveSize": "default"}
            first = await p.save_ini_preset("zebra", vals)
            self.assertTrue(first["ok"])
            self.assertFalse(first.get("overwrote"))
            second = await p.save_ini_preset("apple", vals)
            self.assertTrue(second["ok"])
            self.assertEqual(list(second["presets"].keys()), ["apple", "zebra"])
            # case-insensitive overwrite keeps one entry under the new spelling
            third = await p.save_ini_preset("APPLE", vals)
            self.assertTrue(third["ok"])
            self.assertTrue(third.get("overwrote"))
            self.assertEqual(list((await p.get_ini_presets())["presets"].keys()),
                             ["APPLE", "zebra"])
            # case-insensitive delete
            rm = await p.delete_ini_preset("apple")
            self.assertNotIn("APPLE", rm["presets"])
            # cap: fresh plugin to avoid interference
            q = self._plugin()
            q.loop = asyncio.get_event_loop()
            for i in range(main.MAX_INI_PRESETS):
                r = await q.save_ini_preset(f"p{i}", vals)
                self.assertTrue(r["ok"], r)
            over = await q.save_ini_preset("one-more", vals)
            self.assertFalse(over["ok"])
            self.assertIn("max", over["error"])
            # overwriting at cap is still allowed
            r = await q.save_ini_preset("P0", vals)
            self.assertTrue(r["ok"], r)
            self.assertTrue(r.get("overwrote"))
        asyncio.run(go())

    def test_preset_apply_corrupt(self):
        self._version_dir(tag="9.9.9")
        home = make_steam_home(self.tmp)
        os.environ["DECKY_USER_HOME"] = str(home)
        p = self._plugin()

        async def go():
            p.loop = asyncio.get_event_loop()
            idx = await self._game_index(p, "GameB")
            self.assertTrue((await p.install_games([idx], "optiscaler"))["ok"])
            p.settings.setSetting("ini_presets", {"bad": {"Sharpener": "bogus"}})
            w = await p.apply_ini_preset(idx, "bad")
            self.assertFalse(w["ok"])
            self.assertIn("corrupt", w["output"])
        asyncio.run(go())

    def test_verify_and_repair(self):
        self._version_dir(tag="9.9.9")
        home = make_steam_home(self.tmp)
        os.environ["DECKY_USER_HOME"] = str(home)
        p = self._plugin()
        ga = home / ".local/share/lib/steamapps/common/GameA/bin/amd_fidelityfx_upscaler_dx12.dll"
        gb = home / ".local/share/lib/steamapps/common/GameB"

        async def go():
            p.loop = asyncio.get_event_loop()
            idx_a = await self._game_index(p, "GameA")
            idx_b = await self._game_index(p, "GameB")
            self.assertTrue((await p.install_games([idx_a]))["ok"])
            self.assertTrue((await p.install_games([idx_b], "optiscaler"))["ok"])
            v = await p.verify_installs()
            self.assertTrue(v["ok"])
            self.assertTrue(all(g["ok"] for g in v["games"]), v["games"])
            # simulate a Steam update overwriting the drop-in
            ga.write_bytes(b"brand new game DLL")
            v = await p.verify_installs()
            bad = next(g for g in v["games"] if g["name"] == "GameA")
            self.assertFalse(bad["ok"])
            self.assertTrue(any("pdate" in x for x in bad["problems"]), bad["problems"])
            # plain install refuses to clobber the unrecognized file ...
            r = await p.install_games([idx_a])
            self.assertTrue(r["ok"])
            self.assertIn("left as it is", r["output"])
            # ... repair refreshes the backup and drops in again
            r = await p.repair_games([idx_a])
            self.assertTrue(r["ok"], r["output"])
            self.assertTrue(main.helixscan.is_helixsr(ga))
            self.assertEqual((ga.parent / "amd_fidelityfx_upscaler_dx12.original.dll").read_bytes(),
                             b"brand new game DLL")
            # simulate a deleted OptiScaler loader
            (gb / "dxgi.dll").unlink()
            v = await p.verify_installs()
            bad = next(g for g in v["games"] if g["name"] == "GameB")
            self.assertFalse(bad["ok"])
            self.assertIn("dxgi.dll missing next to the game's exe", bad["problems"])
            self.assertTrue((await p.install_games([idx_b], "optiscaler"))["ok"])
            v = await p.verify_installs()
            self.assertTrue(all(g["ok"] for g in v["games"]), v["games"])
        asyncio.run(go())

    def test_download_cancel(self):
        p = self._plugin()

        import time

        class Slow:
            headers = {}

            def read(self, n):
                time.sleep(0.2)
                return b"x" * (1 << 20)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

        async def go():
            p.loop = asyncio.get_event_loop()
            with mock.patch.object(main.urllib.request, "urlopen", return_value=Slow()):
                self.assertEqual(await p.start_download("v9.9.9", "https://example.invalid/x.zip"),
                                 {"ok": True})
                await asyncio.sleep(0.3)
                self.assertEqual(await p.cancel_download(), {"ok": True})
                st = await self._wait_done(p.get_download_status)
            self.assertTrue(st["cancelled"])
            self.assertFalse(st["ok"])
            root = Path(os.environ["DECKY_PLUGIN_RUNTIME_DIR"]) / "helixsr"
            self.assertFalse((root / ".v9.9.9.zip").exists())
            self.assertEqual(await p.cancel_download(), {"ok": False, "error": "no download running"})
        asyncio.run(go())

    def test_factory_reset(self):
        old = self._version_dir(tag="9.9.8")
        new = self._version_dir(tag="9.9.9")
        (old / "pad.bin").write_bytes(b"\x00" * (3 << 20))
        home = make_steam_home(self.tmp)
        os.environ["DECKY_USER_HOME"] = str(home)
        ga = home / ".local/share/lib/steamapps/common/GameA/bin/amd_fidelityfx_upscaler_dx12.dll"
        original = ga.read_bytes()
        p = self._plugin(active="v9.9.9")
        p.settings.setSetting("extra_folders", ["/tmp"])

        async def go():
            p.loop = asyncio.get_event_loop()
            await p.save_ini_preset("keep", {"Sharpener": "off", "Sharpness": "0.5",
                                             "UseGameSlider": False, "WaveSize": "default"})
            idx = await self._game_index(p, "GameA")
            self.assertTrue((await p.install_games([idx]))["ok"])
            p._dl["running"] = True
            busy = await p.factory_reset()
            self.assertFalse(busy["ok"])
            p._dl["running"] = False
            r = await p.factory_reset()
            self.assertTrue(r["ok"], r.get("error"))
            self.assertEqual(r["games_uninstalled"], ["GameA"])
            self.assertEqual(sorted(r["versions_removed"]), ["v9.9.8", "v9.9.9"])
            self.assertGreaterEqual(r["freed_mb"], 3.0)
            self.assertEqual(ga.read_bytes(), original)
            rt = Path(os.environ["DECKY_PLUGIN_RUNTIME_DIR"]) / "helixsr"
            self.assertFalse((rt / "scan-cache.json").exists())
            self.assertEqual(p.settings.getSetting("active_tag"), "")
            self.assertEqual(p.settings.getSetting("extra_folders"), [])
            self.assertIn("keep", (await p.get_ini_presets())["presets"])
        asyncio.run(go())
        d = self.tmp / "healed"
        (d / "setup/lib/model").mkdir(parents=True)
        t = d / "setup/lib/model/launch_synth"
        t.write_bytes(b"elf")
        t.chmod(0o644)
        self.assertEqual(main._repair_exec_bits(d), ["setup/lib/model/launch_synth"])
        self.assertEqual(_mode(t), 0o755)
        self.assertEqual(main._repair_exec_bits(d), [])

    # --- self-update ----------------------------------------------------------

    def test_compare_versions(self):
        cmp = main._compare_versions
        self.assertEqual(cmp("v1.9.0", "v1.8.1"), 1)
        self.assertEqual(cmp("v1.8.1", "v1.9.0"), -1)
        self.assertEqual(cmp("v0.0.10", "v0.0.10"), 0)
        self.assertEqual(cmp("v0.0.9", "v0.0.10"), -1)
        self.assertEqual(cmp("1.2", "1.2.0"), 0)
        self.assertEqual(cmp("v2.0", "v10.0"), -1)

    def test_largest_zip(self):
        rel = {"assets": [
            {"name": "notes.txt", "size": 5},
            {"name": "small.zip", "size": None},
            {"name": "big.zip", "size": 99, "browser_download_url": "u"},
        ]}
        self.assertEqual(main._largest_zip(rel)["name"], "big.zip")
        self.assertIsNone(main._largest_zip({"assets": [{"name": "x.txt"}]}))
        self.assertIsNone(main._largest_zip({}))

    def _update_zip_bytes(self, name="HelixSR Muse Plugin", version="9.9.9",
                          flat=False, with_main=True):
        import io
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            base = "" if flat else "plug/"
            z.writestr(base + "plugin.json", json.dumps({"name": name}))
            z.writestr(base + "package.json", json.dumps({"name": "helixsr-muse-plugin",
                                                          "version": version}))
            if with_main:
                z.writestr(base + "dist/index.js", "console.log(1)")
                z.writestr(base + "main.py", "x = 1\n")
        return buf.getvalue()

    def _plug_dir(self, version="0.0.1"):
        plug = self.tmp / "plug"
        (plug / "dist").mkdir(parents=True)
        (plug / "dist" / "index.js").write_text("old")
        (plug / "main.py").write_text("old = 1\n")
        (plug / "package.json").write_text(json.dumps({"version": version}))
        (plug / "stale.txt").write_text("remove me if replaced")
        return plug

    def test_install_plugin_zip_swaps(self):
        plug = self._plug_dir("0.0.1")
        zp = self.tmp / "u.zip"
        zp.write_bytes(self._update_zip_bytes(version="9.9.9"))
        got = main._install_plugin_zip(zp, plug, "0.0.1")
        self.assertEqual(got, "9.9.9")
        self.assertEqual((plug / "dist" / "index.js").read_text(), "console.log(1)")
        self.assertFalse((plug / "stale.txt").exists())
        self.assertFalse((plug.parent / "plug.update-bak").exists())
        self.assertFalse((plug / ".update-stage").exists())

    def test_install_plugin_zip_rejects(self):
        plug = self._plug_dir("9.9.9")
        before = (plug / "dist" / "index.js").read_text()
        bad_name = self.tmp / "a.zip"
        bad_name.write_bytes(self._update_zip_bytes(name="Something Else"))
        with self.assertRaises(OSError):
            main._install_plugin_zip(bad_name, plug, "0.0.1")
        old_ver = self.tmp / "b.zip"
        old_ver.write_bytes(self._update_zip_bytes(version="0.0.1"))
        with self.assertRaises(OSError):
            main._install_plugin_zip(old_ver, plug, "9.9.9")
        no_main = self.tmp / "c.zip"
        no_main.write_bytes(self._update_zip_bytes(with_main=False))
        with self.assertRaises(OSError):
            main._install_plugin_zip(no_main, plug, "0.0.1")
        flat = self.tmp / "d.zip"
        flat.write_bytes(self._update_zip_bytes(flat=True))
        with self.assertRaises(OSError):
            main._install_plugin_zip(flat, plug, "0.0.1")
        self.assertEqual((plug / "dist" / "index.js").read_text(), before)

    def test_check_plugin_update(self):
        p = self._plugin()
        listing = [{"tag_name": "v9.9.9", "draft": False,
                    "assets": [{"name": "p.zip", "size": 10, "browser_download_url": "u"}],
                    "body": "notes"}]

        async def go():
            p.loop = asyncio.get_event_loop()
            with mock.patch.object(main, "_fetch_json", return_value=listing):
                with mock.patch.object(main, "_plugin_version", return_value="0.0.1"):
                    r = await p.check_plugin_update()
            self.assertTrue(r["ok"])
            self.assertTrue(r["update_available"])
            self.assertEqual(r["latest"], "9.9.9")
            self.assertEqual(r["body"], "notes")
            with mock.patch.object(main, "_plugin_version", return_value="v9.9.9"):
                r2 = await p.check_plugin_update(force=True)
            self.assertTrue(r2["ok"])
            self.assertFalse(r2["update_available"])
        asyncio.run(go())

    def test_start_plugin_update_busy(self):
        p = self._plugin()

        async def go():
            p.loop = asyncio.get_event_loop()
            p._dl["running"] = True
            r = await p.start_plugin_update()
            self.assertFalse(r["ok"])
            self.assertIn("running", r["error"])
            p._dl["running"] = False
        asyncio.run(go())

    def test_plugin_update_end_to_end(self):
        plug = self._plug_dir("0.0.1")
        os.environ["DECKY_PLUGIN_DIR"] = str(plug)
        self.addCleanup(os.environ.pop, "DECKY_PLUGIN_DIR", None)
        payload = self._update_zip_bytes(version="9.9.9")
        p = self._plugin()
        listing = [{"tag_name": "v9.9.9", "draft": False,
                    "assets": [{"name": "p.zip", "size": len(payload),
                                "browser_download_url": "https://example.invalid/u.zip"}]}]

        class Resp:
            headers = {"Content-Length": str(len(payload))}

            def __init__(self, data):
                self._data = data

            def read(self, n):
                chunk, self._data = self._data[:n], self._data[n:]
                return chunk

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

        async def go():
            p.loop = asyncio.get_event_loop()
            with mock.patch.object(main, "_fetch_json", return_value=listing), \
                    mock.patch.object(main.urllib.request, "urlopen", return_value=Resp(payload)):
                r = await p.start_plugin_update()
                self.assertTrue(r["ok"], r)
                self.assertEqual(r["version"], "9.9.9")
                for _ in range(400):
                    st = await p.get_plugin_update_status()
                    if st.get("done"):
                        break
                    await asyncio.sleep(0.05)
            self.assertTrue(st["ok"], st["error"])
            self.assertEqual((plug / "dist" / "index.js").read_text(), "console.log(1)")
            self.assertEqual(p._update_pending, "9.9.9")
            st = await p.get_state()
            self.assertEqual(st["update_pending"], "9.9.9")
        asyncio.run(go())

    def test_dir_writable_probe(self):
        d = self.tmp / "w"
        d.mkdir()
        self.assertTrue(main._dir_writable(d))
        ro = self.tmp / "ro"
        ro.mkdir()
        ro.chmod(0o555)
        try:
            self.assertFalse(main._dir_writable(ro))
        finally:
            ro.chmod(0o755)

    def test_plugin_update_manual_mode(self):
        # Plugin dir not writable (e.g. root-owned install): the zip is staged
        # for Decky's manual Install-from-ZIP flow instead of crashing.
        plug = self._plug_dir("0.0.1")
        plug.chmod(0o555)
        home = self.tmp / "home"
        (home / "Downloads").mkdir(parents=True)
        os.environ["DECKY_USER_HOME"] = str(home)
        os.environ["DECKY_PLUGIN_DIR"] = str(plug)
        self.addCleanup(os.environ.pop, "DECKY_PLUGIN_DIR", None)
        payload = self._update_zip_bytes(version="9.9.9")
        p = self._plugin()
        listing = [{"tag_name": "v9.9.9", "draft": False,
                    "assets": [{"name": "p.zip", "size": len(payload),
                                "browser_download_url": "https://example.invalid/u.zip"}]}]

        class Resp:
            headers = {"Content-Length": str(len(payload))}

            def __init__(self, data):
                self._data = data

            def read(self, n):
                chunk, self._data = self._data[:n], self._data[n:]
                return chunk

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

        async def go():
            p.loop = asyncio.get_event_loop()
            with mock.patch.object(main, "_fetch_json", return_value=listing):
                c = await p.check_plugin_update()
            self.assertTrue(c["ok"])
            self.assertFalse(c["can_install"])
            with mock.patch.object(main, "_fetch_json", return_value=listing), \
                    mock.patch.object(main.urllib.request, "urlopen", return_value=Resp(payload)):
                r = await p.start_plugin_update()
                self.assertTrue(r["ok"], r)
                for _ in range(400):
                    st = await p.get_plugin_update_status()
                    if st.get("done"):
                        break
                    await asyncio.sleep(0.05)
            self.assertTrue(st["ok"], st["error"])
            self.assertEqual(st["mode"], "manual")
            self.assertTrue(st["path"].endswith("helixsr-muse-plugin-v9.9.9.zip"))
            self.assertTrue(st["path"].startswith(str(home / "Downloads")))
            # original install untouched, no pending restart
            self.assertEqual((plug / "dist" / "index.js").read_text(), "old")
            self.assertEqual(p._update_pending, "")
            import zipfile
            with zipfile.ZipFile(st["path"]) as z:
                self.assertIn("plug/package.json", z.namelist())
        try:
            asyncio.run(go())
        finally:
            plug.chmod(0o755)


    def test_scan_game_detail_fields(self):
        self._version_dir(tag="9.9.9")
        home = make_steam_home(self.tmp)
        os.environ["DECKY_USER_HOME"] = str(home)
        p = self._plugin()

        async def go():
            p.loop = asyncio.get_event_loop()
            self.assertEqual(await p.start_scan(), {"ok": True})
            st = await self._wait_done(p.get_scan_status)
            by_name = {g["name"]: g for g in st["games"]}
            self.assertTrue(by_name["GameA"]["exe"].endswith("GameA/bin"))
            self.assertTrue(any(x.endswith("amd_fidelityfx_upscaler_dx12.dll")
                                for x in by_name["GameA"]["fsr"]))
            self.assertNotIn("GameC", by_name)
            # old cache files without the fields still load with defaults
            cache = p._scan_cache_path()
            payload = json.loads(cache.read_text(encoding="utf-8"))
            for g in payload["games"]:
                g.pop("exe", None)
                g.pop("fsr", None)
                g.pop("installed_versions", None)
            cache.write_text(json.dumps(payload), encoding="utf-8")
            cached = await p.get_last_scan()
            entry = next(g for g in cached["games"] if g["name"] == "GameA")
            self.assertEqual(entry["exe"], "")
            self.assertEqual(entry["fsr"], [])
            self.assertEqual(entry["installed_versions"], [])
        asyncio.run(go())

    def test_health_check(self):
        self._version_dir(tag="9.9.9")
        home = make_steam_home(self.tmp)
        os.environ["DECKY_USER_HOME"] = str(home)
        p = self._plugin()
        listing = [{"tag_name": "v9.9.9", "draft": False, "assets": []}]

        class FakeSock:
            def close(self):
                pass

        async def go():
            p.loop = asyncio.get_event_loop()
            with mock.patch.object(main, "_fetch_json", return_value=listing), \
                    mock.patch("socket.create_connection", return_value=FakeSock()):
                h = await p.health_check()
                names = {c["name"]: c for c in h["checks"]}
                self.assertEqual(set(names), {"GitHub reachable", "Versions", "Games",
                                              "Disk space", "Plugin update"})
                for c in h["checks"]:
                    self.assertIn("detail", c)
                self.assertTrue(names["GitHub reachable"]["ok"])
                self.assertIn("none installed", names["Games"]["detail"])
                idx = await self._game_index(p, "GameA")
                self.assertTrue((await p.install_games([idx]))["ok"])
                h = await p.health_check()
                self.assertTrue(h["ok"], h["checks"])
                by_name = {c["name"]: c for c in h["checks"]}
                self.assertIn("1 installed, all verified", by_name["Games"]["detail"])
                # break the install: game row reports, version row stays healthy
                ga = home / ".local/share/lib/steamapps/common/GameA/bin/amd_fidelityfx_upscaler_dx12.dll"
                ga.write_bytes(b"new game dll")
                h = await p.health_check()
                self.assertFalse(h["ok"])
                by_name = {c["name"]: c for c in h["checks"]}
                self.assertIn("GameA", by_name["Games"]["detail"])
                self.assertTrue(by_name["Versions"]["ok"])
        asyncio.run(go())

    def test_health_no_versions(self):
        p = self._plugin()

        class FakeSock:
            def close(self):
                pass

        async def go():
            p.loop = asyncio.get_event_loop()
            with mock.patch.object(main, "_fetch_json", side_effect=OSError("down")), \
                    mock.patch("socket.create_connection", return_value=FakeSock()):
                h = await p.health_check()
            by_name = {c["name"]: c for c in h["checks"]}
            self.assertFalse(by_name["Versions"]["ok"])
            self.assertFalse(by_name["Games"]["ok"])
            self.assertFalse(h["ok"])
        asyncio.run(go())


    def test_dir_size_cached(self):
        d = self.tmp / "sized"
        d.mkdir()
        (d / "a.bin").write_bytes(b"x" * 1000)
        main._dir_size_cache.clear()
        self.assertEqual(main._dir_size_cached(d), 1000)
        # cached: same mtime hit returns without walking (poison the walk to prove it)
        real_walk = main.os.walk
        main.os.walk = lambda *a, **k: (_ for _ in ()).throw(AssertionError("walked"))
        try:
            self.assertEqual(main._dir_size_cached(d), 1000)
        finally:
            main.os.walk = real_walk
        # new file bumps dir mtime -> fresh walk picks it up
        (d / "b.bin").write_bytes(b"y" * 500)
        self.assertEqual(main._dir_size_cached(d), 1500)

    def test_get_releases_paths(self):
        p = self._plugin()

        async def go():
            p.loop = asyncio.get_event_loop()
            good = [{"tag_name": "v9.9.9", "name": "N", "published_at": "2026-01-01T00:00:00Z",
                     "assets": [{"name": "h.zip", "size": 12, "browser_download_url": "u"}],
                     "body": "notes here"}]
            with mock.patch.object(main, "_fetch_json", return_value=good):
                r = await p.get_releases()
            self.assertEqual(r["error"], "")
            self.assertEqual(len(r["releases"]), 1)
            self.assertEqual(r["releases"][0]["zip_name"], "h.zip")
            self.assertIn("notes", r["releases"][0]["body"])
            # releases without zips are skipped, HTTP errors surface
            mixed = good + [{"tag_name": "v9.9.8", "assets": [{"name": "n.txt"}]}]
            with mock.patch.object(main, "_fetch_json", return_value=mixed):
                r = await p.get_releases()
            self.assertEqual(len(r["releases"]), 1)
            import urllib.error
            err = urllib.error.HTTPError("u", 403, "rate", {}, None)
            with mock.patch.object(main, "_fetch_json", side_effect=err):
                r = await p.get_releases()
            self.assertEqual(r["releases"], [])
            self.assertIn("403", r["error"])
            self.assertIn("rate limit", r["error"])
            with mock.patch.object(main, "_fetch_json", side_effect=OSError("down")):
                r = await p.get_releases()
            self.assertIn("OSError", r["error"])
        asyncio.run(go())

    def test_helixsr_asset_match(self):
        rel = {"tag_name": "v1.9.0", "assets": [
            {"name": "HelixSR-Scaler-1.0.0.zip", "size": 131359942,
             "browser_download_url": "scaler"},
            {"name": "HelixSR-1.9.0.zip", "size": 3803249,
             "browser_download_url": "main"},
            {"name": "SHA256SUMS.txt", "size": 478,
             "browser_download_url": "sums"}]}
        self.assertEqual(main._helixsr_asset(rel, "v1.9.0")["browser_download_url"], "main")
        solo = {"tag_name": "v1.8.1", "assets": [
            {"name": "h.zip", "size": 12, "browser_download_url": "u"}]}
        self.assertEqual(main._helixsr_asset(solo, "v1.8.1")["name"], "h.zip")
        self.assertIsNone(main._helixsr_asset(
            {"tag_name": "v1", "assets": [{"name": "n.txt"}]}, "v1"))

    def test_available_routes_ways_delegation(self):
        import types
        g = types.SimpleNamespace(fsr_targets=[], route="scaler",
                                  ways=lambda: ["direct", "scaler"])
        self.assertEqual(main.helixref.available_routes(g, "/nonexistent"), ["direct", "scaler"])
        base = Path(tempfile.mkdtemp(prefix="hxways-"))
        self.addCleanup(shutil.rmtree, base, True)
        modern = base / "HelixSR-1.9.3"
        (modern / "setup").mkdir(parents=True)
        (modern / "setup/helixsr_setup.py").write_text("# layout marker\n")
        (modern / "setup/helixsr_install.py").write_text('MARKER = "helixsr-install.json"\n')
        plain = types.SimpleNamespace(fsr_targets=[], route="unsupported")
        self.assertEqual(main.helixref.available_routes(plain, modern), [])
        fsr = types.SimpleNamespace(fsr_targets=["x.dll"], route="direct")
        self.assertEqual(main.helixref.available_routes(fsr, modern), ["direct"])

    def test_fg_engine_gate(self):
        base = Path(tempfile.mkdtemp(prefix="hxfggate-"))
        self.addCleanup(shutil.rmtree, base, True)
        root = base / "helixsr"
        noeng = root / "HelixSR-1.9.3"
        (noeng / "setup").mkdir(parents=True)
        (noeng / "setup/helixsr_setup.py").write_text("# layout marker\n")
        (noeng / "helixsr-setup.sh").write_text("#!/bin/sh\n")
        (noeng / "amd_fidelityfx_dx12.dll").write_bytes(b"\x00HelixSR 1.9.3\x00" + b"\x00" * 100)
        eng = root / "HelixSR-1.9.0"
        (eng / "setup" / "fg").mkdir(parents=True)
        (eng / "setup" / "fg" / "fg_setup.py").write_text("# engine\n")
        (eng / "setup/helixsr_setup.py").write_text("# layout marker\n")
        (eng / "helixsr-setup.sh").write_text("#!/bin/sh\n")
        (eng / "amd_fidelityfx_dx12.dll").write_bytes(b"\x00HelixSR 1.9.0\x00" + b"\x00" * 100)
        self.assertFalse(main.helixref.fg_engine_available(noeng))
        self.assertTrue(main.helixref.fg_engine_available(eng))
        with mock.patch.dict(os.environ, {"DECKY_PLUGIN_RUNTIME_DIR": str(base)}):
            p = self._plugin(active="v1.9.3")

            async def go():
                p.loop = asyncio.get_event_loop()
                r = await p.check_build_ready("v1.9.3", fg=True)
                self.assertFalse(r["ok"])
                self.assertIn("withdrawn", r["error"])
                r = await p.start_build(False, True)
                self.assertFalse(r["ok"])
                self.assertIn("withdrawn", r["error"])
            asyncio.run(go())

    def test_upstream_scaler_manifest_interop(self):
        import types
        base = Path(tempfile.mkdtemp(prefix="hxup-"))
        self.addCleanup(shutil.rmtree, base, True)
        t = base / "GameU"
        (t / "HelixSR").mkdir(parents=True)
        (t / "dxgi.dll").write_bytes(b"upstream scaler loader")
        (t / "OptiScaler.ini").write_text("[HelixSR]\nInt8=auto\n")
        (t / "HelixSR" / "helixsr.dll").write_bytes(b"\x00HelixSR 1.9.3\x00" + b"\x00" * 100)
        (t / "HelixSR-Scaler.files").write_text(
            "# HelixSR Scaler, installed by the HelixSR setup\n# version=1.0.6\nname=dxgi.dll\n"
            "dxgi.dll\nOptiScaler.ini\n")
        g = types.SimpleNamespace(target=t, fsr_targets=[], folder=t, name="GameU")
        p = self._plugin()
        info = main._scaler_install_info(g)
        self.assertEqual(info["kind"], "upstream")
        self.assertEqual(info["scaler"], "1.0.6")
        self.assertEqual(info["helixsr"], "1.9.3")
        self.assertEqual(info["loader"], "dxgi.dll")
        self.assertIsNone(info["launch_options"])
        self.assertEqual(main._marker_versions(g), {"1.9.3"})
        self.assertEqual(p._verify_game(g), [])
        (t / "dxgi.dll").unlink()
        problems = p._verify_game(g)
        self.assertTrue(any("dxgi.dll" in x for x in problems), problems)

    def test_scaler_delegates_to_upstream(self):
        base = Path(tempfile.mkdtemp(prefix="hxdel-"))
        self.addCleanup(shutil.rmtree, base, True)
        d = base / "HelixSR-1.9.3"
        (d / "setup").mkdir(parents=True)
        (d / "setup/helixsr_setup.py").write_text("# layout marker\n")
        (d / "setup/helixsr_install.py").write_text(
            'CALLS = []\n'
            'def install(g, here):\n'
            '    CALLS.append((g.name, g.route))\n'
            '    print(f"[helixsr-setup] installed {g.name} via {g.route}")\n'
            'def uninstall(g):\n'
            '    print("[helixsr-setup] uninstalled")\n'
            'def install_scaler(g, here, fg_new=False):\n'
            '    CALLS.append(("scaler", g.name))\n'
            'def uninstall_scaler(t, quiet=False):\n'
            '    print("[helixsr-setup] scaler removed")\n')
        import types
        p = self._plugin()
        g = types.SimpleNamespace(target=base / "GameD", fsr_targets=[], folder=base / "GameD",
                                  name="GameD", route="direct")
        log = p._scaler_install_one(g, d)
        self.assertIn("via scaler", log)
        mod = main.helixref.install_module(d)
        self.assertEqual(mod.CALLS, [("GameD", "scaler")])
        self.assertEqual(p._scaler_uninstall_one(g, d).strip(), "[helixsr-setup] scaler removed")

    def test_generation_dispatch(self):
        helixref = main.helixref
        legacy = self._version_dir(tag="9.9.8")
        self.assertEqual(helixref.layout_of(legacy), "legacy")
        self.assertIs(helixref.install_module(legacy), main.helixscan)
        base = Path(tempfile.mkdtemp(prefix="hxmodern-"))
        self.addCleanup(shutil.rmtree, base, True)
        modern = base / "HelixSR-1.9.0"
        (modern / "setup").mkdir(parents=True)
        (modern / "setup/helixsr_setup.py").write_text("# layout marker\n")
        (modern / "amd_fidelityfx_dx12.dll").write_bytes(b"DLL")
        self.assertEqual(helixref.layout_of(modern), "modern")
        # synthetic modern reference (our own code, not upstream's): import
        # mechanics, stdout capture, per-generation signatures.
        (modern / "setup/helixsr_install.py").write_text(
            'MARKER = "helixsr-install.json"\n'
            'LOADER_NAMES = ("dxgi.dll",)\n'
            'def games(extra):\n'
            '    print("[helixsr-setup] scanned")\n'
            '    return []\n'
            'def install(g, here):\n'
            '    print("[helixsr-setup] installed")\n'
            'def uninstall(g):\n'
            '    print("[helixsr-setup] uninstalled")\n'
            'if __name__ == "__main__":\n'
            '    raise SystemExit("nope")\n')
        mod = helixref.install_module(modern)
        self.assertTrue(mod.__name__.startswith("helixsr_install_"))
        self.assertIs(helixref.install_module(modern), mod)
        gl, skipped, log = helixref.scan(modern, ["/tmp"])
        self.assertEqual((gl, skipped), ([], 0))
        self.assertIn("scanned", log)
        self.assertIn("installed", helixref.install(object(), modern))
        self.assertIn("uninstalled", helixref.uninstall(object(), modern))
        self.assertEqual(helixref.available_routes(object(), modern), [])
        self.assertEqual(helixref.loader_name(modern), "dxgi.dll")
        p = self._plugin()
        p.settings.setSetting("active_tag", "v9.9.8")

        async def go():
            p.loop = asyncio.get_event_loop()
            s = await p.get_state()
            self.assertEqual(s["versions"][0]["layout"], "legacy")
        asyncio.run(go())


    def test_legacy_layout(self):
        # Pre-1.7 layout (e.g. 1.4.3): no uninstaller, no optiscaler/amd-fsr/
        # dlss-loader folders, network as separate files.
        base = Path(tempfile.mkdtemp(prefix="hxlegacy-"))
        self.addCleanup(shutil.rmtree, base, True)
        home = make_steam_home(base)
        os.environ["DECKY_USER_HOME"] = str(home)
        here = make_helixsr_dir(base / "pkg", tag="1.4.3", modern=False)
        self.assertFalse((here / "helixsr-uninstall.sh").exists())
        self.assertFalse((here / "optiscaler").exists())
        # FSR game that also has DLSS inputs (OptiScaler-capable on modern).
        gd = home / ".local/share/lib/steamapps/common/GameD"
        (gd / "bin").mkdir(parents=True)
        (gd / "bin" / "amd_fidelityfx_upscaler_dx12.dll").write_bytes(b"FSR DLL")
        (gd / "bin" / "nvngx_dlss.dll").write_bytes(b"DLSS DLL")
        (gd / "bin" / "game.exe").write_bytes(b"\x00" * 100)
        p = self._plugin()

        async def go():
            p.loop = asyncio.get_event_loop()
            gl, _ = await asyncio.to_thread(main.helixscan.games, here)
            d = next(g for g in gl if g.name == "GameD")
            # modern folder offers both ways, legacy folder only the drop-in
            modern = make_helixsr_dir(base / "pkg2", tag="9.9.9")
            gm, _ = await asyncio.to_thread(main.helixscan.games, modern)
            dm = next(g for g in gm if g.name == "GameD")
            self.assertIn("optiscaler", main.helixscan.available_routes(dm, modern))
            self.assertEqual(main.helixscan.available_routes(d, here), ["direct"])
            # download of an old-layout zip completes (used to crash chmod)
            zp = base / "old.zip"
            with zipfile.ZipFile(zp, "w") as z:
                z.writestr("HelixSR-1.4.3/helixsr-setup.sh", "#!/bin/sh\n")
                z.writestr("HelixSR-1.4.3/amd_fidelityfx_dx12.dll", b"fake")
            os.environ["DECKY_PLUGIN_RUNTIME_DIR"] = str(base / "rt")
            self.assertEqual(await p.start_download("v1.4.3", zp.as_uri()), {"ok": True})
            st = await self._wait_done(p.get_download_status)
            self.assertTrue(st["ok"], st["error"])
            self.assertEqual(st["dir"], "HelixSR-1.4.3")
        asyncio.run(go())

    def test_legacy_install_and_reject(self):
        base = Path(tempfile.mkdtemp(prefix="hxlegacy2-"))
        self.addCleanup(shutil.rmtree, base, True)
        home = make_steam_home(base)
        os.environ["DECKY_USER_HOME"] = str(home)
        here = make_helixsr_dir(base / "rt" / "helixsr", tag="1.4.3", modern=False)
        gd = home / ".local/share/lib/steamapps/common/GameD"
        (gd / "bin").mkdir(parents=True)
        (gd / "bin" / "amd_fidelityfx_upscaler_dx12.dll").write_bytes(b"FSR DLL")
        (gd / "bin" / "nvngx_dlss.dll").write_bytes(b"DLSS DLL")
        (gd / "bin" / "game.exe").write_bytes(b"\x00" * 100)
        os.environ["DECKY_PLUGIN_RUNTIME_DIR"] = str(base / "rt")
        p = self._plugin()
        target = gd / "bin" / "amd_fidelityfx_upscaler_dx12.dll"
        original = target.read_bytes()

        async def go():
            p.loop = asyncio.get_event_loop()
            idx = await self._game_index(p, "GameD")
            bad = await p.install_games([idx], "optiscaler")
            self.assertFalse(bad["ok"])
            self.assertIn("cannot use route", bad["output"])
            # game's files untouched by the refused install
            self.assertEqual(target.read_bytes(), original)
            self.assertFalse((gd / "bin" / "amd_fidelityfx_upscaler_dx12.original.dll").exists())
            r = await p.install_games([idx])
            self.assertTrue(r["ok"], r["output"])
            self.assertTrue(main.helixscan.is_helixsr(target))
            self.assertEqual((gd / "bin" / "amd_fidelityfx_upscaler_dx12.original.dll").read_bytes(),
                             original)
            self.assertTrue((gd / "bin" / "helixsr_weights.bin").is_file())
            self.assertTrue((gd / "bin" / "helixsr_kernels.pak").is_file())
            self.assertTrue((await p.uninstall_games([idx]))["ok"])
            self.assertEqual(target.read_bytes(), original)
        asyncio.run(go())


if __name__ == "__main__":
    unittest.main()
