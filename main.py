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
                    "versions": versions, "root": str(_helixsr_root()),
                    "plugin_version": _plugin_version()}
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
        if helixscan.network_ready(d):
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
            target=self._build_run, args=(d, log, header, bash, str(script)), daemon=True)
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

    def _build_run(self, d: Path, log: Path, header: str, bash: str, script: str):
        """Phase 1 (worker thread): ensure the DLSS SDK DLL, with progress to
        the build log. Phase 2 is scheduled back on the event loop."""
        line = lambda msg: self._build_log_line(log, msg)  # noqa: E731
        try:
            if not self._alive:
                self._build_running = False
                return
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
                self._spawn_and_watch(d, log, header, bash, script, dlss), self.loop)
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
                               bash: str, script: str, dlss: Path):
        try:
            proc = await asyncio.create_subprocess_exec(
                bash, script, "--yes", "--dlss", str(dlss),
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
            await proc.wait()
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
