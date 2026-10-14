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
        p._scan = main._new_scan_state()
        p._mig = main._new_mig_state()
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

    # --- helpers ------------------------------------------------------------

    def test_repair_exec_bits(self):
        d = self.tmp / "healed"
        (d / "setup/lib/model").mkdir(parents=True)
        t = d / "setup/lib/model/launch_synth"
        t.write_bytes(b"elf")
        t.chmod(0o644)
        self.assertEqual(main._repair_exec_bits(d), ["setup/lib/model/launch_synth"])
        self.assertEqual(_mode(t), 0o755)
        self.assertEqual(main._repair_exec_bits(d), [])


if __name__ == "__main__":
    unittest.main()
