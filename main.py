import os
import asyncio
import json
import re
import shutil
import ssl
import subprocess
import tempfile
import threading
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


def _new_dl_state(tag: str = "") -> dict:
    return {"running": False, "done": False, "ok": False, "error": "",
            "tag": tag, "downloaded": 0, "total": 0,
            "dir": "", "network_built": False}


def _new_scan_state() -> dict:
    return {"running": False, "done": False, "ok": False, "error": "",
            "games": [], "tag": "", "skipped": 0}


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
                try:
                    dll_version = helixscan.version_of(d / "amd_fidelityfx_dx12.dll")
                except OSError:
                    dll_version = "?"
                versions.append({"tag": _tag_of_dir(d), "dir": d.name,
                                 "network_built": helixscan.network_ready(d),
                                 "dll_version": dll_version})
            return {"active_tag": self.settings.getSetting("active_tag", ""),
                    "versions": versions, "root": str(_helixsr_root())}
        return await asyncio.to_thread(_state)

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
            with zipfile.ZipFile(tmp) as z:
                # Top-level folder HelixSR-x.y.z
                top = z.namelist()[0].split("/")[0]
                z.extractall(root)
            tmp.unlink(missing_ok=True)
            d = root / top
            (d / "helixsr-setup.sh").chmod(0o755)
            (d / "helixsr-uninstall.sh").chmod(0o755)
            st["dir"] = top
            st["network_built"] = helixscan.network_ready(d)
        except Exception as e:  # noqa: BLE001
            decky.logger.error(f"download {tag} failed: {e}")
            st["error"] = str(e)
        else:
            st["ok"] = True
        finally:
            st["running"] = False
            st["done"] = True

    async def get_download_status(self) -> dict:
        return dict(self._dl)

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

    async def start_scan(self) -> dict:
        """Scan Steam libraries for compatible games in a background thread; poll get_scan_status()."""
        if self._scan["running"]:
            return {"ok": False, "error": "a scan is already running"}
        d = await self._active_dir()
        if d is None:
            return {"ok": False, "error": "no HelixSR version downloaded"}
        self._scan = _new_scan_state()
        self._scan["running"] = True
        threading.Thread(target=self._scan_run, args=(d, _tag_of_dir(d)), daemon=True).start()
        return {"ok": True}

    def _scan_run(self, d: Path, tag: str):
        st = self._scan
        try:
            with self._ops_lock:
                gl, skipped = helixscan.games(d)
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
                out.append({"index": i, "name": g.name, "folder": str(g.folder),
                            "route": g.route, "inputs": list(g.inputs),
                            "installed": inst, "status": status})
            st["games"] = out
            st["tag"] = tag
            st["skipped"] = skipped
            st["ok"] = True
        except Exception as e:  # noqa: BLE001
            decky.logger.error(f"game scan failed: {e}")
            st["error"] = f"{type(e).__name__}: {e}"
        finally:
            st["running"] = False
            st["done"] = True

    async def get_scan_status(self) -> dict:
        st = dict(self._scan)
        st["games"] = list(st["games"])
        return st

    async def install_games(self, indices: list[int]) -> dict:
        return await self._game_op("install", indices)

    async def uninstall_games(self, indices: list[int]) -> dict:
        return await self._game_op("uninstall", indices)

    async def _game_op(self, op: str, indices: list[int]) -> dict:
        d = await self._active_dir()
        if d is None:
            return {"ok": False, "output": "no HelixSR version downloaded"}
        try:
            clean = [int(i) for i in indices]
        except (TypeError, ValueError):
            return {"ok": False, "output": f"bad game numbers: {indices}"}
        return await asyncio.to_thread(self._game_op_run, op, d, clean)

    def _game_op_run(self, op: str, d: Path, indices: list[int]) -> dict:
        """Install/uninstall via the reference implementation, in-process (no
        child process, no prompts): fast file copies, guarded by _ops_lock."""
        try:
            with self._ops_lock:
                if not helixscan.network_ready(d):
                    return {"ok": False, "output": "the network is not built: run Build first"}
                gl, _skipped = helixscan.games(d)
                targets = []
                for i in indices:
                    if not 1 <= i <= len(gl):
                        return {"ok": False, "output": f"not a game number: {i} (scan found {len(gl)})"}
                    targets.append(gl[i - 1])
                helixscan.take_log()
                for g in targets:
                    decky.logger.info(f"HelixSR {op} {g.name} (route {g.route})")
                    if op == "install":
                        helixscan.install(g, d)
                    else:
                        helixscan.uninstall(g)
                return {"ok": True, "output": "\n".join(helixscan.take_log())[-8000:]}
        except SystemExit as e:
            return {"ok": False, "output": str(e) or "aborted"}
        except Exception as e:  # noqa: BLE001
            decky.logger.error(f"HelixSR {op} failed: {e}")
            return {"ok": False, "output": f"{type(e).__name__}: {e}"}

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
                "network_built": bool(d and helixscan.network_ready(d))}

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
        self._dl = _new_dl_state()
        self._scan = _new_scan_state()
        self._ops_lock = threading.Lock()
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
