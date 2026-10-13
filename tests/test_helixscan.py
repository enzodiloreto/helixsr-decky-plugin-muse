"""Scan + install/uninstall tests against synthetic Steam libraries."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _common import install_decky_stub, make_helixsr_dir, make_steam_home

install_decky_stub()

import helixscan


class ScanTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="hxscan-"))
        self.here = make_helixsr_dir(self.tmp / "pkg")
        self.home = make_steam_home(self.tmp)
        self._old_home = os.environ.get("DECKY_USER_HOME")
        os.environ["DECKY_USER_HOME"] = str(self.home)
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(self._restore_home)

    def _restore_home(self):
        if self._old_home is None:
            os.environ.pop("DECKY_USER_HOME", None)
        else:
            os.environ["DECKY_USER_HOME"] = self._old_home

    def test_scan_routes(self):
        gl, skipped = helixscan.games(self.here)
        self.assertEqual(skipped, 0)
        self.assertEqual([(g.name, g.route) for g in gl],
                         [("GameA", "direct"), ("GameB", "optiscaler")])
        by_name = {g.name: g for g in gl}
        self.assertEqual(by_name["GameB"].inputs, ["DLSS"])
        self.assertFalse(any(g.installed() for g in gl))

    def test_scan_isolates_broken_game(self):
        real_walk = helixscan.walk

        def flaky(folder, depth):
            if Path(folder).name == "GameB":
                raise OSError("unreadable")
            return real_walk(folder, depth)

        with mock.patch.object(helixscan, "walk", side_effect=flaky):
            gl, skipped = helixscan.games(self.here)
        self.assertEqual([g.name for g in gl], ["GameA"])
        self.assertGreaterEqual(skipped, 1)

    def test_direct_install_uninstall_roundtrip(self):
        gl, _ = helixscan.games(self.here)
        g = next(x for x in gl if x.name == "GameA")
        target = self.tmp / "home/.local/share/lib/steamapps/common/GameA/bin/amd_fidelityfx_upscaler_dx12.dll"
        original = target.read_bytes()
        helixscan.install(g, self.here)
        self.assertTrue(helixscan.is_helixsr(target))
        self.assertEqual(target.with_name(target.stem + ".original.dll").read_bytes(), original)
        g2 = helixscan.Game("GameA", target.parent.parent, helixscan.walk(target.parent.parent, 7), self.here)
        self.assertTrue(g2.installed())
        helixscan.uninstall(g2)
        self.assertEqual(target.read_bytes(), original)
        self.assertFalse(target.with_name(target.stem + ".original.dll").exists())

    def test_optiscaler_install_uninstall_roundtrip(self):
        gl, _ = helixscan.games(self.here)
        g = next(x for x in gl if x.name == "GameB")
        folder = self.tmp / "home/.local/share/lib/steamapps/common/GameB"
        helixscan.install(g, self.here)
        self.assertTrue((folder / "dxgi.dll").exists())
        self.assertTrue((folder / "HelixSR" / "helixsr-install.json").exists())
        ini = (folder / "OptiScaler.ini").read_text()
        self.assertIn("Dx12Upscaler = fsr31", ini)
        g2 = helixscan.Game("GameB", folder, helixscan.walk(folder, 7), self.here)
        self.assertTrue(g2.installed())
        self.assertEqual(g2.route, "optiscaler")
        helixscan.uninstall(g2)
        self.assertFalse((folder / "dxgi.dll").exists())
        self.assertFalse((folder / "HelixSR").exists())
        self.assertFalse((folder / "OptiScaler.ini").exists())


if __name__ == "__main__":
    unittest.main()
