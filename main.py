import os
import asyncio
import json
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

# The decky plugin module is located at decky-loader/plugin
# For easy intellisense checkout the decky-loader code repo
# and add the `decky-loader/plugin/imports` path to `python.analysis.extraPaths` in `.vscode/settings.json`
import decky
from settings import SettingsManager

GITHUB_OWNER = "lonewolf0622"
GITHUB_REPO = "HelixSR"
RELEASES_URL = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases?per_page=30"
EMBED_MAGIC = b"HXSRNET1"


def _helixsr_root() -> Path:
    return Path(os.environ.get("DECKY_PLUGIN_RUNTIME_DIR", tempfile.gettempdir())) / "helixsr"


def _fetch_json(url: str, timeout: int = 30):
    req = urllib.request.Request(url, headers={"User-Agent": "helixsr-decky-plugin", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


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


def _has_embedded_network(dll: Path) -> bool:
    try:
        with open(dll, "rb") as f:
            f.seek(-32, os.SEEK_END)
            return f.read(8) == EMBED_MAGIC
    except OSError:
        return False


def _network_built(d: Path) -> bool:
    dll = d / "amd_fidelityfx_dx12.dll"
    if dll.exists() and _has_embedded_network(dll):
        return True
    return (d / "helixsr_weights.bin").exists() and (d / "helixsr_kernels.pak").exists()


def _dll_version(d: Path) -> str:
    try:
        data = (d / "amd_fidelityfx_dx12.dll").read_bytes()[: 4 << 20]
        m = re.search(rb"\x00HelixSR (\d+\.\d+\.\d+)\x00", data)
        return m.group(1).decode() if m else "?"
    except OSError:
        return "?"


def _parse_game_list(output: str) -> list[dict]:
    games: list[dict] = []
    for line in output.splitlines():
        m = re.match(r"\s*(\d+)\.\s+(.*?)\s{2,}(.*)\s*$", line)
        if m:
            idx = int(m.group(1))
            name = m.group(2).strip()
            status = m.group(3).strip()
            games.append({"index": idx, "name": name, "status": status,
                          "installed": status.lower().startswith("helixsr installed")})
    return games


def _run_install_script(helixsr_dir: Path, args: list[str], timeout: int = 300) -> dict:
    script = helixsr_dir / "setup" / "helixsr_install.py"
    if not script.exists():
        return {"ok": False, "output": f"helixsr_install.py not found in {helixsr_dir}"}
    cmd = [sys.executable, str(script), str(helixsr_dir)] + args
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        output = (p.stdout or "") + (p.stderr or "")
        return {"ok": p.returncode == 0, "output": output[-8000:], "rc": p.returncode}
    except subprocess.TimeoutExpired:
        return {"ok": False, "output": "timed out"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "output": str(e)}


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
                            "size": a.get("size", 0)})
            return {"releases": out, "error": ""}
        return await asyncio.to_thread(_fetch)

    async def get_state(self) -> dict:
        def _state():
            versions = []
            for d in _version_dirs():
                versions.append({"tag": _tag_of_dir(d), "dir": d.name,
                                 "network_built": _network_built(d),
                                 "dll_version": _dll_version(d)})
            return {"active_tag": self.settings.getSetting("active_tag", ""),
                    "versions": versions, "root": str(_helixsr_root())}
        return await asyncio.to_thread(_state)

    async def set_active_version(self, tag: str) -> dict:
        self.settings.setSetting("active_tag", tag)
        self.settings.commit()
        decky.logger.info(f"HelixSR active_tag={tag}")
        return await self.get_state()

    async def download_version(self, tag: str, zip_url: str) -> dict:
        def _dl():
            root = _helixsr_root()
            root.mkdir(parents=True, exist_ok=True)
            tmp = root / f".{tag}.zip"
            try:
                req = urllib.request.Request(zip_url, headers={"User-Agent": "helixsr-decky-plugin"})
                with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as f:
                    shutil.copyfileobj(r, f)
                with zipfile.ZipFile(tmp) as z:
                    # Top-level folder HelixSR-x.y.z
                    top = z.namelist()[0].split("/")[0]
                    z.extractall(root)
                tmp.unlink(missing_ok=True)
                d = root / top
                (d / "helixsr-setup.sh").chmod(0o755)
                (d / "helixsr-uninstall.sh").chmod(0o755)
                if not self.settings.getSetting("active_tag"):
                    self.settings.setSetting("active_tag", tag)
                    self.settings.commit()
                return {"ok": True, "dir": top,
                        "network_built": _network_built(d)}
            except Exception as e:  # noqa: BLE001
                decky.logger.error(f"download_version {tag} failed: {e}")
                return {"ok": False, "error": str(e)}
        return await asyncio.to_thread(_dl)

    async def remove_version(self, tag: str) -> dict:
        def _rm():
            d = _dir_for_tag(tag)
            if d is None:
                return {"ok": False, "error": "not downloaded"}
            shutil.rmtree(d, ignore_errors=True)
            if self.settings.getSetting("active_tag") == tag:
                rest = _version_dirs()
                self.settings.setSetting("active_tag", _tag_of_dir(rest[0]) if rest else "")
                self.settings.commit()
            return {"ok": True}
        return await asyncio.to_thread(_rm)

    async def _active_dir(self) -> Path | None:
        tag = self.settings.getSetting("active_tag", "")
        d = _dir_for_tag(tag) if tag else None
        if d is None:
            rest = _version_dirs()
            d = rest[-1] if rest else None
        return d

    async def list_games(self) -> dict:
        d = await self._active_dir()
        if d is None:
            return {"ok": False, "error": "no HelixSR version downloaded", "games": []}
        res = await asyncio.to_thread(_run_install_script, d, ["list"])
        if not res["ok"]:
            return {"ok": False, "error": res["output"][-500:], "games": []}
        return {"ok": True, "games": _parse_game_list(res["output"]), "tag": _tag_of_dir(d)}

    async def install_games(self, indices: list[int]) -> dict:
        d = await self._active_dir()
        if d is None:
            return {"ok": False, "output": "no HelixSR version downloaded"}
        args = ["install"] + [str(int(i)) for i in indices]
        return await asyncio.to_thread(_run_install_script, d, args)

    async def uninstall_games(self, indices: list[int]) -> dict:
        d = await self._active_dir()
        if d is None:
            return {"ok": False, "output": "no HelixSR version downloaded"}
        args = ["uninstall"] + [str(int(i)) for i in indices]
        return await asyncio.to_thread(_run_install_script, d, args)

    async def start_build(self) -> dict:
        """Run helixsr-setup.sh --yes in the background (builds the network, ~1 min).
        Game prompts default to no on EOF; use install_games afterwards."""
        if self._build_task and not self._build_task.done():
            return {"ok": False, "error": "build already running"}
        d = await self._active_dir()
        if d is None:
            return {"ok": False, "error": "no HelixSR version downloaded"}
        log = _helixsr_root() / f"build-{_tag_of_dir(d)}.log"
        self._build_log = log

        async def _run():
            self._build_running = True
            try:
                with open(log, "w") as f:
                    f.write(f"[helixsr] building network in {d}\n")
                proc = await asyncio.create_subprocess_exec(
                    "bash", str(d / "helixsr-setup.sh"), "--yes",
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=open(log, "a"), stderr=subprocess.STDOUT,
                    cwd=str(d))
                await proc.wait()
                rc = proc.returncode
                with open(log, "a") as f:
                    f.write(f"\n[helixsr] setup exited rc={rc}\n")
                await decky.emit("helixsr_build_done", rc == 0, rc)
            except Exception as e:  # noqa: BLE001
                with open(log, "a") as f:
                    f.write(f"\n[helixsr] build failed: {e}\n")
                await decky.emit("helixsr_build_done", False, -1)
            finally:
                self._build_running = False

        self._build_task = self.loop.create_task(_run())
        return {"ok": True, "log": str(log)}

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
                "network_built": bool(d and _network_built(d))}

    # Asyncio-compatible long-running code, executed in a task when the plugin is loaded
    # MUST NOT BLOCK - spawn tasks for long work.
    async def _main(self):
        self.loop = asyncio.get_event_loop()
        self.settings = SettingsManager(
            name="settings",
            settings_directory=os.environ["DECKY_PLUGIN_SETTINGS_DIR"],
        )
        self.settings.read()
        self._build_task = None
        self._build_running = False
        self._build_log: Path | None = None
        decky.logger.info("HelixSR-Decky-Plugin-Muse loaded")

    # Function called first during the unload process, utilize this to handle your plugin being stopped, but not
    # completely removed
    async def _unload(self):
        decky.logger.info("HelixSR unloading")
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
