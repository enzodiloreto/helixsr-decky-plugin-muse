import {
  PanelSection,
  PanelSectionRow,
  ProgressBar,
  SliderField,
  TextField,
  ToggleField,
  staticClasses
} from "@decky/ui";
import {
  addEventListener,
  removeEventListener,
  definePlugin,
  toaster,
} from "@decky/api"
import { Fragment, useEffect, useRef, useState } from "react";
import { MdMemory } from "react-icons/md";
import { Card, Chip, MButton, PagedText } from "./components";
import { M3 } from "./theme";
import {
  DownloadStatus,
  HelixGame,
  HelixRelease,
  HelixState,
  InstallItem,
  LogResult,
  PluginUpdateInfo,
  PluginUpdateStatus,
  ReadinessCheck,
  ScanCache,
  ScanStatus,
  SettingsResult,
  VerifyResult,
  apiAddScanFolder,
  apiApplyIniPreset,
  apiApplyLogFix,
  apiCancelBuild,
  apiCancelDownload,
  apiCancelPluginUpdate,
  apiCancelScan,
  apiCheckBuildReady,
  apiCheckPluginUpdate,
  apiDeleteIniPreset,
  apiFactoryReset,
  apiGetBuildStatus,
  apiGetDownloadStatus,
  apiGetGameLog,
  apiGetGameSettings,
  apiGetIniPresets,
  apiGetLastScan,
  apiGetPluginUpdateStatus,
  apiGetReleases,
  apiGetScanStatus,
  apiGetState,
  apiGetWayPicks,
  apiHealthCheck,
  apiInstallGames,
  apiRemoveOldVersions,
  apiRemoveScanFolder,
  apiRemoveVersion,
  apiRepairGames,
  apiResetGameSettings,
  apiRollbackGame,
  apiSaveIniPreset,
  apiSetActiveVersion,
  apiSetGameSettings,
  apiSetScalerFg,
  apiSetWayPick,
  apiStartBuild,
  apiStartDownload,
  apiStartPluginUpdate,
  apiStartScan,
  apiUninstallGames,
  apiVerifyInstalls,
} from "./services/api";
import { PLUGIN_VERSION } from "./version";
import {
  ROUTE_SHORT,
  base,
  cmpTags,
  fmtAge,
  fmtSize,
  installWayFor,
  isWaySwitch,
  parseBuildProgress,
  waysFor,
} from "./utils";

// True while the panel is open; the plugin-scope build listener only
// toasts when it isn't, so completions are announced exactly once.
let panelOpen = false;

// Settings keys the bridge edits (its documented schema). Unknown keys a
// release reports come back under "other" and render read-only.
const HELIXSR_LABELS: Record<string, string> = {
  "Sharpening.Sharpener": "Sharpener — off, nvidia or rcas",
  "Sharpening.Sharpness": "Sharpness — 0.0 to 1.0",
  "Sharpening.UseGameSlider": "Game slider — let the game's slider drive sharpening",
  "Compatibility.WaveSize": "Wave size — auto, 64 for RX 7000/9000 if broken",
};
const SCALER_LABELS: Record<string, string> = {
  "HelixSR.WaveSize": "Wave size — 64 for RX 7000/9000 if the picture is broken",
  "HelixSR.FGPreset": "Frame generation — Light (fast) or Full quality",
  "HelixSR.QuickFlow": "FG motion estimate — runs with Full only (auto is off)",
};
const HELIXSR_OPTIONS: Record<string, [string, string][] | null> = {
  "Sharpening.Sharpener": [["off", "Off"], ["nvidia", "NVIDIA"], ["rcas", "RCAS"]],
  "Sharpening.Sharpness": null,
  "Sharpening.UseGameSlider": [["true", "On"], ["false", "Off"]],
  "Compatibility.WaveSize": [["auto", "Auto"], ["default", "Default"], ["64", "64"], ["32", "32"]],
};
const SCALER_OPTIONS: Record<string, [string, string][] | null> = {
  "HelixSR.WaveSize": [["auto", "Auto"], ["64", "64"], ["32", "32"]],
  "HelixSR.FGPreset": [["auto", "Auto (Light)"], ["c", "Light"], ["full", "Full"]],
  "HelixSR.QuickFlow": [["auto", "Auto"], ["off", "Off"], ["fast", "Fast"], ["fastest", "Fastest"]],
};

const WAY_LABELS: Record<string, string> = {
  auto: "Recommended",
  scaler: "HelixSR Scaler",
  fsr: "FSR drop-in",
};

// What the player has to pick inside the game for each way.
const WAY_HINTS: Record<string, string> = {
  auto: "HelixSR picks the game's best way",
  scaler: "Pick DLSS in-game (Insert for the menu)",
  fsr: "Pick AMD FSR in-game",
};

// Short way names for buttons and chips (WAY_LABELS carries the in-game hint).
const WAY_SHORT: Record<string, string> = {
  auto: "Recommended",
  scaler: "HelixSR Scaler",
  fsr: "FSR drop-in",
};

function Content() {
  const [releases, setReleases] = useState<HelixRelease[]>([]);
  const [showPrereleases, setShowPrereleases] = useState(false);
  const [showAllReleases, setShowAllReleases] = useState(false);
  const [state, setState] = useState<HelixState | null>(null);
  const [picked, setPicked] = useState("");
  const [output, setOutput] = useState("");
  const [busy, setBusy] = useState("");
  // Download
  const [downloading, setDownloading] = useState(false);
  const [dl, setDl] = useState<DownloadStatus | null>(null);
  const [dlRate, setDlRate] = useState("");
  const dlPrev = useRef<{ t: number; bytes: number } | null>(null);
  // Build
  const [building, setBuilding] = useState(false);
  const [buildLog, setBuildLog] = useState("");
  const [fgBuild, setFgBuild] = useState(true);
  const [readiness, setReadiness] = useState<ReadinessCheck[] | null>(null);
  // Games
  const [games, setGames] = useState<HelixGame[]>([]);
  const [gamesTag, setGamesTag] = useState("");
  const [scanning, setScanning] = useState(false);
  const [ways, setWays] = useState<Record<string, string>>({});
  const [fgFor, setFgFor] = useState<Record<string, boolean>>({});
  const [verify, setVerify] = useState<VerifyResult[] | null>(null);
  const [folderInput, setFolderInput] = useState("");
  // Settings + logs
  const [settingsFor, setSettingsFor] = useState<string | null>(null);
  const [settings, setSettings] = useState<SettingsResult | null>(null);
  const [settingsDraft, setSettingsDraft] = useState<Record<string, string>>({});
  const [presets, setPresets] = useState<Record<string, Record<string, string | null>>>({});
  const [presetName, setPresetName] = useState("Default");
  const [logFor, setLogFor] = useState<string | null>(null);
  const [gameLog, setGameLog] = useState<LogResult | null>(null);
  // Updates / health
  const [updateInfo, setUpdateInfo] = useState<PluginUpdateInfo | null>(null);
  const [updating, setUpdating] = useState(false);
  const [pu, setPu] = useState<PluginUpdateStatus | null>(null);
  const [puRate, setPuRate] = useState("");
  const puPrev = useRef<{ t: number; bytes: number } | null>(null);
  const [confirm, setConfirm] = useState("");
  const confirmTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const armOrRun = async (id: string, label: string, fn: () => Promise<void>) => {
    if (confirm !== id) {
      setConfirm(id);
      if (confirmTimer.current) clearTimeout(confirmTimer.current);
      confirmTimer.current = setTimeout(() => setConfirm(""), 15000);
      setOutput(`${label}: tap again to confirm.`);
      return;
    }
    if (confirmTimer.current) clearTimeout(confirmTimer.current);
    setConfirm("");
    await fn();
  };

  // Keep the way the user picked for a game: every install, repair and manual
  // scan re-scans the libraries, and that must not throw the selection back
  // to "Recommended" (the pick is what the install action uses). The picks
  // are persisted (backend settings), so they also survive closing the panel.
  const mergeWays = (list: HelixGame[]) => {
    setWays((prev) => {
      const next: Record<string, string> = { ...prev };
      list.forEach((g) => { if (!next[g.folder]) next[g.folder] = "auto"; });
      return next;
    });
  };

  // Pick a way for a game and remember it (the backend keeps it in its
  // settings: the pick survives a panel close and a re-scan).
  const pickWay = (folder: string, w: string) => {
    setWays((prev) => ({ ...prev, [folder]: w }));
    apiSetWayPick(folder, w).catch((e) => console.error("[helixsr] set_way_pick failed", e));
  };

  const refreshState = async () => {
    try {
      const s = await apiGetState();
      setState(s);
      if (!picked) setPicked(s.active_tag || s.versions[0]?.tag || "");
      return s;
    } catch (e) {
      console.error("[helixsr] get_state failed", e);
      return null;
    }
  };

  const refreshReleases = async () => {
    try {
      const relsRes = await apiGetReleases(showPrereleases);
      setReleases(relsRes.releases);
      if (!picked) setPicked(relsRes.releases[0]?.tag || "");
      if (relsRes.error) setOutput(`Failed to load HelixSR releases: ${relsRes.error}`);
      else if (relsRes.releases.length) setOutput("");
      return relsRes;
    } catch (e) {
      console.error("[helixsr] get_releases failed", e);
      return null;
    }
  };

  const onCheckPluginUpdate = async (force: boolean) => {
    try {
      const info = await apiCheckPluginUpdate(force);
      setUpdateInfo(info);
    } catch { /* best-effort */ }
  };

  useEffect(() => {
    panelOpen = true;
    (async () => {
      try {
        const [relsRes, s] = await Promise.all([apiGetReleases(), apiGetState()]);
        setReleases(relsRes.releases);
        setState(s);
        setPicked(s.active_tag || s.versions[0]?.tag || relsRes.releases[0]?.tag || "");
        if (relsRes.error) setOutput(`Failed to load HelixSR releases: ${relsRes.error}`);
        try {
          // The way picked per game: remembered between sessions. Loaded
          // before the scan, so a remembered pick wins over the "auto"
          // default the scan merge fills in.
          const picks = await apiGetWayPicks();
          if (picks.ways) setWays(picks.ways);
        } catch { /* no picks yet */ }
        try {
          const cached: ScanCache = await apiGetLastScan();
          if (cached.games.length) {
            setGames(cached.games);
            setGamesTag(cached.tag || "");
            mergeWays(cached.games);
            if (!relsRes.error) {
              const tagNote = s.active_tag && cached.tag && cached.tag !== s.active_tag
                ? ` (scanned with ${cached.tag})` : "";
              setOutput(`Showing games scanned ${fmtAge(cached.scanned_at)}${tagNote} — Scan to refresh.`);
            }
          }
        } catch { /* no cached scan yet */ }
        try {
          const [dl0, sc0, pu0] = await Promise.all([
            apiGetDownloadStatus(), apiGetScanStatus(), apiGetPluginUpdateStatus()]);
          if (dl0.running) { setDl(dl0); setDownloading(true); }
          if (sc0.running) setScanning(true);
          if (pu0.running) { setPu(pu0); setUpdating(true); }
        } catch { /* nothing running */ }
        try {
          await onCheckPluginUpdate(false);
        } catch { /* update check is best-effort */ }
      } catch (e) {
        console.error("[helixsr] init failed", e);
      }
      const listener = addEventListener<[ok: boolean, rc: number]>("helixsr_build_done", async (ok, rc) => {
        setBuilding(false);
        try {
          const st = await apiGetBuildStatus();
          setBuildLog(st.log || `Build finished rc=${rc}, no log captured (full log: ${st.log_path}).`);
        } catch {
          setBuildLog(`Build finished rc=${rc}, could not read build log.`);
        }
        toaster.toast({ title: ok ? "HelixSR build done" : "HelixSR build failed", body: `rc=${rc}` });
        refreshState();
      });
      (window as any).__helixsr_listener = listener;
    })();
    return () => {
      panelOpen = false;
      const l = (window as any).__helixsr_listener;
      if (l) removeEventListener("helixsr_build_done", l);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Poll download progress while a download runs
  useEffect(() => {
    if (!downloading) return;
    const t = setInterval(async () => {
      try {
        const st = await apiGetDownloadStatus();
        setDl(st);
        const now = Date.now();
        const prev = dlPrev.current;
        dlPrev.current = { t: now, bytes: st.downloaded };
        if (prev && now > prev.t && st.downloaded >= prev.bytes && !st.done) {
          const speed = ((st.downloaded - prev.bytes) * 1000) / (now - prev.t);
          if (speed > 0) {
            const left = st.total > st.downloaded ? (st.total - st.downloaded) / speed : 0;
            setDlRate(`${fmtSize(speed)}/s${st.total ? ` — ~${Math.max(1, Math.ceil(left))}s left` : ""}`);
          }
        }
        if (st.done) {
          setDownloading(false);
          setDlRate("");
          if (st.cancelled) {
            setOutput("Download cancelled.");
          } else if (st.ok) {
            setOutput(`Downloaded ${st.dir}. Network built: ${st.network_built ? "yes" : "no — run Build"}.`);
            setState(await apiSetActiveVersion(st.tag));
            setPicked(st.tag);
            setGames([]);
            setGamesTag("");
            toaster.toast({ title: `Downloaded ${st.tag}`, body: "Set as active version" });
          } else {
            toaster.toast({ title: "Download failed", body: st.error || "" });
            setOutput(st.error || "download failed");
          }
          refreshState();
        }
      } catch { /* ignore */ }
    }, 500);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [downloading]);

  // Poll scan status while a scan runs
  useEffect(() => {
    if (!scanning) return;
    const t = setInterval(async () => {
      try {
        const st: ScanStatus = await apiGetScanStatus();
        if (st.done) {
          setScanning(false);
          if (st.ok) {
            setGames(st.games);
            setGamesTag(st.tag || "");
            setVerify(null);
            mergeWays(st.games);
            setOutput(`Found ${st.games.length} compatible game(s) with ${st.tag}.`);
          } else if (st.cancelled) {
            setOutput("Scan cancelled.");
          } else {
            setGames([]);
            setOutput(st.error || "game scan failed");
          }
        }
      } catch { /* ignore */ }
    }, 1000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scanning]);

  // Poll plugin-update status while an update runs
  useEffect(() => {
    if (!updating) return;
    const t = setInterval(async () => {
      try {
        const st = await apiGetPluginUpdateStatus();
        setPu(st);
        const now = Date.now();
        const prev = puPrev.current;
        puPrev.current = { t: now, bytes: st.downloaded };
        if (prev && now > prev.t && st.downloaded >= prev.bytes && !st.done) {
          const speed = ((st.downloaded - prev.bytes) * 1000) / (now - prev.t);
          if (speed > 0) {
            const left = st.total > st.downloaded ? (st.total - st.downloaded) / speed : 0;
            setPuRate(`${fmtSize(speed)}/s${st.total ? ` — ~${Math.max(1, Math.ceil(left))}s left` : ""}`);
          }
        }
        if (st.done) {
          setUpdating(false);
          setPuRate("");
          puPrev.current = null;
          if (st.ok) {
            setOutput(`Plugin update to v${st.version} installed — restart Decky to apply it.`);
            toaster.toast({ title: `Plugin updated to v${st.version}`, body: "Restart Decky to apply" });
            refreshState();
            onCheckPluginUpdate(false);
          } else {
            setOutput(st.error || "plugin update failed");
            toaster.toast({ title: "Plugin update failed", body: st.error || "" });
          }
        }
      } catch { /* ignore */ }
    }, 1000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [updating]);

  // Poll build log while a build runs
  useEffect(() => {
    if (!building) return;
    const t = setInterval(async () => {
      try {
        const st = await apiGetBuildStatus();
        setBuildLog(st.log);
        if (!st.running) {
          setBuilding(false);
          refreshState();
        }
      } catch { /* ignore */ }
    }, 2000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [building]);

  const downloadedTags = new Set((state?.versions || []).map((v) => v.tag));
  const listedReleases = showAllReleases
    ? releases
    : releases.filter((r, i) => downloadedTags.has(r.tag) || i < 3);
  const activeVersion = state?.versions.find((v) => v.tag === state.active_tag);
  const pickedRelease = releases.find((r) => r.tag === picked);
  const pickedDownloaded = picked ? downloadedTags.has(picked) : false;

  const onCancelDownload = async () => {
    try {
      await apiCancelDownload();
    } catch (e) {
      console.error("[helixsr] cancel_download failed", e);
    }
  };

  const onDownloadOrUse = async () => {
    if (!picked) {
      setOutput("No release selected.");
      return;
    }
    if (pickedDownloaded) {
      setBusy("version");
      try {
        const st = await apiSetActiveVersion(picked);
        setState(st);
        setPicked(picked);
        setGames([]);
        setGamesTag("");
      } finally {
        setBusy("");
        refreshState();
      }
      return;
    }
    if (!pickedRelease) {
      toaster.toast({ title: "Release not found", body: `No download for ${picked} — reload releases.` });
      setOutput(`No download found for ${picked}. Tap Reload releases and try again.`);
      return;
    }
    setDl(null);
    setDlRate("");
    dlPrev.current = null;
    setOutput(`Starting download of ${picked} ...`);
    try {
      const r = await apiStartDownload(picked, pickedRelease.zip_url, pickedRelease.size);
      if (!r.ok) {
        toaster.toast({ title: "Download not started", body: r.error || "" });
        setOutput(r.error || "download not started");
        return;
      }
      setDownloading(true);
      setOutput(`Downloading ${pickedRelease.zip_name} (${fmtSize(pickedRelease.size)}) ...`);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      toaster.toast({ title: "Download failed to start", body: msg });
      setOutput(`Could not start download: ${msg}`);
      console.error("[helixsr] start_download failed", e);
    }
  };

  const onDownloadOrUsePick = async (tag: string) => {
    setPicked(tag);
    if ((state?.versions || []).some((v) => v.tag === tag)) {
      setBusy("version");
      try {
        const st = await apiSetActiveVersion(tag);
        setState(st);
        setGames([]);
        setGamesTag("");
      } finally {
        setBusy("");
        refreshState();
      }
      return;
    }
    const rel = releases.find((r) => r.tag === tag);
    if (!rel) {
      setOutput(`No download found for ${tag}. Tap Reload releases and try again.`);
      return;
    }
    setDl(null);
    setDlRate("");
    dlPrev.current = null;
    try {
      const r = await apiStartDownload(tag, rel.zip_url, rel.size);
      if (!r.ok) {
        setOutput(r.error || "download not started");
        return;
      }
      setDownloading(true);
      setOutput(`Downloading ${rel.zip_name} (${fmtSize(rel.size)}) ...`);
    } catch (e) {
      setOutput(`Could not start download: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const onRemoveVersion = async (tag: string) => {
    setBusy("version");
    try {
      const r = await apiRemoveVersion(tag);
      if (!r.ok) setOutput(r.error || "remove failed");
      else {
        setOutput(`Removed ${tag} (freed ${r.freed_mb} MB).`);
        if (picked === tag) setPicked("");
      }
      refreshState();
    } finally {
      setBusy("");
    }
  };

  const onCheckReady = async () => {
    setBusy("ready");
    try {
      const tag = activeVersion?.tag || null;
      const r = await apiCheckBuildReady(tag, fgBuild);
      if (!r.checks) {
        setOutput(r.error || "readiness check failed");
        setReadiness(null);
        return;
      }
      setReadiness(r.checks);
      const bad = r.checks.filter((c) => !c.ok);
      setOutput(bad.length
        ? `Not ready: ${bad.map((c) => `${c.name} — ${c.detail}`).join("; ")}`
        : `Ready to build${fgBuild ? " with FG" : ""}: ${r.checks.map((c) => c.name).join(", ")}.`);
    } finally {
      setBusy("");
    }
  };

  const onStartBuild = async (rebuild: boolean) => {
    setBusy("build");
    try {
      const r = await apiStartBuild(rebuild, fgBuild);
      if (!r.ok) {
        toaster.toast({ title: "Build not started", body: r.error || "" });
        setOutput(r.error || "build not started");
        return;
      }
      setBuilding(true);
      setBuildLog("");
      setOutput(`Building ${rebuild ? "(rebuild) " : ""}${fgBuild ? "with frame generation" : "upscaling only"} — watch the log below.`);
    } finally {
      setBusy("");
    }
  };

  const onCancelBuild = async () => {
    try {
      await apiCancelBuild();
    } catch (e) {
      console.error("[helixsr] cancel_build failed", e);
    }
  };

  const onScan = async () => {
    if (!activeVersion) {
      setOutput("Download a HelixSR version first.");
      return;
    }
    setBusy("scan");
    try {
      const r = await apiStartScan();
      if (!r.ok) {
        setOutput(r.error || "scan not started");
        return;
      }
      setScanning(true);
      setOutput("Scanning Steam libraries for games ...");
    } finally {
      setBusy("");
    }
  };

  const onCancelScan = async () => {
    try {
      await apiCancelScan();
    } catch (e) {
      console.error("[helixsr] cancel_scan failed", e);
    }
  };

  const onAddFolder = async () => {
    if (!folderInput.trim()) return;
    setBusy("scan");
    try {
      const r = await apiAddScanFolder(folderInput.trim());
      if (!r.ok) setOutput(r.error || "not added");
      else {
        setFolderInput("");
        setOutput(`Added ${r.folders?.length} extra folder(s). Scan to include them.`);
        refreshState();
      }
    } finally {
      setBusy("");
    }
  };

  const installItems = (folders: string[], way: string): InstallItem[] =>
    folders.map((folder) => {
      const g = games.find((x) => x.folder === folder);
      const it: InstallItem = { folder, way };
      if (way === "scaler" || (way === "auto" && g?.recommended === "scaler")) {
        if (fgFor[folder]) it.fg = true;
      }
      return it;
    });

  const onInstall = async (folders: string[], way: string) => {
    if (!folders.length) return;
    const switches = folders.some((f) => isWaySwitch(way, games.find((x) => x.folder === f)?.installed));
    const label = switches ? `Switch to ${WAY_SHORT[way] || way}` : "Install HelixSR";
    await armOrRun(`install:${folders.join(",")}:${way}`, label, async () => {
      setBusy("games");
      try {
        const r = await apiInstallGames(installItems(folders, way));
        const lines = (r.results || []).map((x) =>
          `${x.ok ? "✓" : "✗"} ${base(x.folder)}${x.ok ? (x.installed ? ` (${x.installed})` : "") : `: ${x.detail.slice(-300)}`}`);
        setOutput(`${r.ok ? "Installed" : "Finished with errors"}:\n${lines.join("\n")}${r.error ? `\n${r.error}` : ""}`);
        refreshState();
        onScanQuiet();
      } finally {
        setBusy("");
      }
    });
  };

  const onScanQuiet = async () => {
    try {
      const r = await apiStartScan();
      if (r.ok) setScanning(true);
    } catch { /* the poller reports failures */ }
  };

  const onUninstall = async (folders: string[]) => {
    if (!folders.length) return;
    await armOrRun(`uninstall:${folders.join(",")}`, "Remove HelixSR", async () => {
      setBusy("games");
      try {
        const r = await apiUninstallGames(folders);
        const lines = (r.results || []).map((x) => `${x.ok ? "✓" : "✗"} ${base(x.folder)}${x.ok ? "" : `: ${x.detail.slice(-300)}`}`);
        setOutput(`Removed:\n${lines.join("\n")}`);
        refreshState();
        onScanQuiet();
      } finally {
        setBusy("");
      }
    });
  };

  const onRepair = async (folders: string[]) => {
    if (!folders.length) return;
    setBusy("games");
    try {
      const r = await apiRepairGames(folders);
      const lines = (r.results || []).map((x) => `${x.ok ? "✓" : "✗"} ${base(x.folder)}${x.ok ? "" : `: ${x.detail.slice(-300)}`}`);
      setOutput(`Repaired:\n${lines.join("\n")}`);
      refreshState();
      onScanQuiet();
    } finally {
      setBusy("");
    }
  };

  const onVerify = async () => {
    setBusy("games");
    try {
      const r = await apiVerifyInstalls();
      if (!r.ok) {
        setOutput(r.error || "verify failed");
        return;
      }
      setVerify(r.games);
      const bad = r.games.filter((g) => !g.ok);
      setOutput(bad.length
        ? `Broken installs:\n${bad.map((g) => `✗ ${g.name}: ${g.problems.join("; ").slice(-300)}`).join("\n")}`
        : `${r.games.length} installed game(s), all verified.`);
    } finally {
      setBusy("");
    }
  };

  const onToggleFg = async (folder: string, on: boolean) => {
    setBusy("games");
    try {
      const r = await apiSetScalerFg(folder, on);
      if (!r.ok) setOutput(r.error || "FG switch failed");
      else {
        setOutput(r.output || `Frame generation ${on ? "on" : "off"}.`);
        onScanQuiet();
      }
    } finally {
      setBusy("");
    }
  };

  const onRollback = async (folder: string) => {
    await armOrRun(`rollback:${folder}`, "Roll back", async () => {
      setBusy("games");
      try {
        const r = await apiRollbackGame(folder);
        if (!r.ok) setOutput(r.error || "rollback failed");
        else {
          setOutput(r.output || "Rolled back.");
          onScanQuiet();
        }
      } finally {
        setBusy("");
      }
    });
  };

  const openSettings = async (folder: string) => {
    setSettingsFor(folder);
    setSettings(null);
    setSettingsDraft({});
    try {
      const [s, p] = await Promise.all([apiGetGameSettings(folder), apiGetIniPresets()]);
      setPresets(p.presets || {});
      if (!s.ok) {
        setOutput(s.error || "settings unavailable");
        return;
      }
      setSettings(s);
      setSettingsDraft(Object.fromEntries(Object.entries(s.values || {}).map(([k, v]) => [k, v ?? ""])));
    } catch (e) {
      console.error("[helixsr] settings failed", e);
    }
  };

  const onSaveSettings = async () => {
    if (!settingsFor) return;
    setBusy("settings");
    try {
      const r = await apiSetGameSettings(settingsFor, settingsDraft);
      if (!r.ok) setOutput(r.error || "save failed");
      else {
        setOutput("Settings saved — restart the game to apply them.");
        openSettings(settingsFor);
      }
    } finally {
      setBusy("");
    }
  };

  const onResetSettings = async () => {
    if (!settingsFor) return;
    await armOrRun(`reset:${settingsFor}`, "Reset settings", async () => {
      setBusy("settings");
      try {
        const r = await apiResetGameSettings(settingsFor);
        if (!r.ok) setOutput(r.error || "reset failed");
        else {
          setOutput(r.output || "Settings back to defaults.");
          openSettings(settingsFor);
        }
      } finally {
        setBusy("");
      }
    });
  };

  const onSavePreset = async () => {
    if (!presetName.trim()) {
      setOutput("Name the preset first.");
      return;
    }
    setBusy("settings");
    try {
      const r = await apiSaveIniPreset(presetName.trim(), settingsDraft);
      if (!r.ok) setOutput(r.error || "preset not saved");
      else {
        setPresets(r.presets || {});
        setOutput(`Preset "${presetName.trim()}" saved${r.overwrote ? " (overwrote)." : "."}`);
      }
    } finally {
      setBusy("");
    }
  };

  const onApplyPreset = async () => {
    if (!settingsFor || !presetName) return;
    setBusy("settings");
    try {
      const r = await apiApplyIniPreset(settingsFor, presetName);
      if (!r.ok) setOutput(r.error || "preset not applied");
      else {
        setOutput(`Preset "${presetName}" applied.`);
        openSettings(settingsFor);
      }
    } finally {
      setBusy("");
    }
  };

  const onDeletePreset = async () => {
    setBusy("settings");
    try {
      const r = await apiDeleteIniPreset(presetName);
      setPresets(r.presets || {});
      setOutput(`Preset "${presetName}" deleted.`);
    } finally {
      setBusy("");
    }
  };

  const openLog = async (folder: string) => {
    setLogFor(folder);
    setGameLog(null);
    try {
      const r = await apiGetGameLog(folder);
      if (!r.ok) {
        setOutput(r.error || "no log yet");
        return;
      }
      setGameLog(r);
    } catch (e) {
      console.error("[helixsr] log failed", e);
    }
  };

  const onApplyFix = async (fixId: string) => {
    if (!logFor) return;
    setBusy("log");
    try {
      const r = await apiApplyLogFix(logFor, fixId);
      if (!r.ok) setOutput(r.output || "fix not applied");
      else {
        setOutput("Fix applied — restart the game.");
        openLog(logFor);
      }
    } finally {
      setBusy("");
    }
  };

  const onRemoveOld = async () => {
    await armOrRun("storage:old", "Remove old versions", async () => {
      setBusy("storage");
      try {
        const r = await apiRemoveOldVersions();
        if (!r.ok) setOutput("remove failed");
        else {
          setOutput(r.removed.length ? `Removed ${r.removed.join(", ")} (freed ${r.freed_mb} MB).` : "Nothing to remove — only the active version is kept.");
          if (picked && r.removed.includes(picked)) setPicked("");
        }
        refreshState();
      } finally {
        setBusy("");
      }
    });
  };

  const onFactoryReset = async () => {
    await armOrRun("storage:factory", "Factory reset", async () => {
      setBusy("storage");
      try {
        const r = await apiFactoryReset();
        if (!r.ok) {
          setOutput(r.error || "factory reset failed");
          return;
        }
        const lines = [
          ...(r.games_uninstalled || []).map((n) => `✓ ${n} uninstalled`),
          ...(r.games_failed || []).map((n) => `✗ ${n}`),
          `versions removed: ${(r.versions_removed || []).join(", ") || "none"}`,
        ];
        setOutput(`Factory reset done:\n${lines.join("\n")}`);
        setGames([]);
        setGamesTag("");
        setPicked("");
        refreshState();
      } finally {
        setBusy("");
      }
    });
  };

  // Decky's privileged installer bridge (same JS realm Decky itself runs in).
  // Null when unreachable (e.g. the desktop port): the backend's in-place
  // swap is then the fallback.
  const getDeckyBridge = (): { call: (route: string, ...args: any[]) => Promise<any> } | null => {
    const b = (window as any).DeckyBackend;
    return b && typeof b.call === "function" ? b : null;
  };

  // Update through Decky's own installer: native consent popup, privileged
  // install (works for the root-owned plugins/ folder Decky owns), hot reload
  // afterwards. Throws only before the popup appears (the caller falls back to
  // the backend flow); once triggered it owns the flow to success or timeout.
  const updateViaDecky = async (
    bridge: { call: (route: string, ...args: any[]) => Promise<any> },
    tag: string,
    url: string,
  ) => {
    // A legacy entry would install side-by-side instead of replacing: refuse
    // (no silent auto-uninstall) and point at the old entry.
    try {
      const plugins = await bridge.call("loader/get_plugins");
      const names = (Array.isArray(plugins) ? plugins : []).map((p: any) => p && p.name).filter(Boolean);
      if (names.includes("HelixSR-Decky-Plugin-Muse")) {
        setOutput("Old 'HelixSR-Decky-Plugin-Muse' entry still installed: uninstall it first (Decky menu), then update.");
        toaster.toast({ title: "Update blocked", body: "uninstall the old entry first" });
        return;
      }
    } catch { /* get_plugins is optional: carry on */ }
    // install_type 2 = UPDATE: Decky shows its install popup for confirmation.
    await bridge.call("utilities/install_plugin", url, "HelixSR Muse Plugin", tag, "", 2);
    setUpdating(true);
    setPu(null);
    setPuRate("");
    puPrev.current = null;
    setOutput(`Confirm Decky's install popup for v${tag} ...`);
    const deadline = Date.now() + 10 * 60 * 1000;
    while (Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, 3000));
      let s: HelixState | null = null;
      try {
        s = await Promise.race([
          apiGetState(),
          new Promise<null>((r) => setTimeout(() => r(null), 8000)),
        ]);
      } catch { /* backend mid-replacement: keep waiting */ }
      if (s && cmpTags(s.plugin_version, tag) === 0) {
        setUpdating(false);
        setOutput(`Updated to v${tag} — reopen this panel for the new UI.`);
        toaster.toast({ title: `Plugin updated to v${tag}`, body: "Reopen this panel" });
        refreshState();
        onCheckPluginUpdate(false);
        return;
      }
    }
    setUpdating(false);
    setOutput("Still on the old version — confirm Decky's popup, or retry the update.");
    toaster.toast({ title: "Update not detected", body: "confirm the popup or retry" });
  };

  const onStartPluginUpdate = async () => {
    // An update replaces the plugin folder: never in the middle of a
    // download or a build.
    if (downloading || building || scanning) {
      setOutput("Finish the running download, build or scan first, then update.");
      toaster.toast({ title: "Update not started", body: "another task is running" });
      return;
    }
    // Decky's installer first: it installs with elevated rights. The
    // backend's own in-place swap cannot rename the plugin folder (Decky
    // owns plugins/ as root), so on a Deck it fails with EACCES.
    const bridge = getDeckyBridge();
    if (bridge && updateInfo?.latest && updateInfo?.url) {
      try {
        await updateViaDecky(bridge, updateInfo.latest, updateInfo.url);
        return;
      } catch (e) {
        console.error("[helixsr] decky install unavailable, falling back", e);
      }
    }
    setBusy("update");
    try {
      const r = await apiStartPluginUpdate();
      if (!r.ok) {
        setOutput(r.error || "update not started");
        return;
      }
      setUpdating(true);
      setOutput(`Updating plugin to v${r.version} ...`);
    } finally {
      setBusy("");
    }
  };

  const onCancelPluginUpdate = async () => {
    try {
      await apiCancelPluginUpdate();
    } catch (e) {
      console.error("[helixsr] cancel_plugin_update failed", e);
    }
  };

  const onHealth = async () => {
    setBusy("health");
    try {
      const r = await apiHealthCheck();
      const lines = (r.checks || []).map((c) => `${c.ok ? "✓" : "✗"} ${c.name} — ${c.detail}`);
      setOutput(`${r.ok ? "All healthy" : "Issues found"}:\n${lines.join("\n")}`);
    } finally {
      setBusy("");
    }
  };

  const heroOp = downloading
    ? { label: "Downloading", pct: dl && dl.total > 0 ? Math.min(100, (100 * dl.downloaded) / dl.total) : -1 }
    : building
      ? (() => { const p = parseBuildProgress(buildLog); return { label: "Building", pct: p ? p.pct : -1 }; })()
      : null;

  // The settings editor and the log viewer are rendered INSIDE the game card
  // that opened them (not at the bottom of the game list).
  const renderSettingsEditor = () => {
    if (!settingsFor || !settings) return null;
    const isScaler = settings.schema === "scaler";
    const labels = isScaler ? SCALER_LABELS : HELIXSR_LABELS;
    const options = isScaler ? SCALER_OPTIONS : HELIXSR_OPTIONS;
    const keys = Object.keys(labels);
    return (
      <Fragment>
        <div style={{ fontWeight: 600, marginTop: M3.space.sm }}>Settings — {base(settingsFor)}</div>
        <div style={{ ...M3.type.caption, opacity: 0.8, overflowWrap: "anywhere" }}>{settings.path}</div>
        {keys.map((key) => {
          const opts = options[key];
          const cur = settingsDraft[key] ?? "";
          if (opts) {
            const idx = Math.max(0, opts.findIndex((o) => o[0] === cur));
            const next = () => {
              const n = opts[(idx + 1) % opts.length][0];
              setSettingsDraft({ ...settingsDraft, [key]: n });
            };
            return (
              <PanelSectionRow key={key}>
                <MButton variant="tonal" onClick={next} description={labels[key]}>
                  {key.split(".")[1]}: {opts[idx][1]}
                </MButton>
              </PanelSectionRow>
            );
          }
          if (key.endsWith("Sharpness")) {
            return (
              <div key={key} style={{ width: "100%", minWidth: 0, maxWidth: "100%", boxSizing: "border-box", marginTop: M3.space.xs }}>
                {/* The slider gets the card's full width: inside a row its
                    track kept its own minimum width and pushed past the
                    card's right edge. */}
                <SliderField
                  layout="below"
                  label="Sharpness"
                  description={labels[key]}
                  value={parseFloat(cur) || 0}
                  min={0}
                  max={1}
                  step={0.05}
                  showValue
                  onChange={(v: number) => setSettingsDraft({ ...settingsDraft, [key]: String(Math.round(v * 100) / 100) })}
                />
              </div>
            );
          }
          return (
            <div key={key} style={{ width: "100%", minWidth: 0, maxWidth: "100%", boxSizing: "border-box", marginTop: M3.space.xs }}>
              <TextField
                label={key.split(".")[1]}
                description={labels[key]}
                value={cur}
                onChange={(e) => setSettingsDraft({ ...settingsDraft, [key]: (e.target as HTMLInputElement).value })}
              />
            </div>
          );
        })}
        {settings.other && Object.keys(settings.other).length > 0 && (
          <PanelSectionRow>
            <div style={M3.type.caption}>
              Other keys in the file: {Object.entries(settings.other).map(([k, v]) => `${k}=${v}`).join(", ")}
            </div>
          </PanelSectionRow>
        )}
        <PanelSectionRow>
          <div style={{ display: "flex", flexWrap: "wrap", gap: M3.space.sm }}>
            <MButton variant="filled" disabled={busy !== ""} onClick={onSaveSettings}>Save</MButton>
            <MButton variant="tonal" disabled={busy !== ""} onClick={() => openSettings(settingsFor)}>Reload</MButton>
            <MButton variant="outlined" disabled={busy !== ""}
              onClick={onResetSettings}>
              {confirm === `reset:${settingsFor}` ? "Tap again to confirm reset" : "Defaults"}
            </MButton>
            <MButton variant="text" onClick={() => { setSettingsFor(null); setSettings(null); }}>Close</MButton>
          </div>
        </PanelSectionRow>
        <PanelSectionRow>
          <div style={{ width: "100%", minWidth: 0, maxWidth: "100%", boxSizing: "border-box" }}>
            <TextField
              label="Preset name"
              value={presetName}
              onChange={(e) => setPresetName((e.target as HTMLInputElement).value.slice(0, 40))}
            />
          </div>
        </PanelSectionRow>        <PanelSectionRow>
          <div style={{ display: "flex", flexWrap: "wrap", gap: M3.space.sm }}>
            <MButton variant="tonal" disabled={busy !== ""} onClick={onSavePreset}>Save preset</MButton>
            <MButton variant="tonal" disabled={busy !== "" || !presetName} onClick={onApplyPreset}>
              Apply {presetName || "preset"}
            </MButton>
            <MButton variant="outlined" disabled={busy !== "" || !presetName} onClick={onDeletePreset}>
              Delete {presetName || "preset"}
            </MButton>
          </div>
        </PanelSectionRow>
        {Object.keys(presets).length > 0 && (
          <PanelSectionRow>
            <div style={{ display: "flex", flexWrap: "wrap", gap: M3.space.sm }}>
              {(["Default", ...Object.keys(presets)]).map((n) => (
                <MButton key={n} variant={presetName === n ? "filled" : "text"} onClick={() => setPresetName(n)}>
                  {n}
                </MButton>
              ))}
            </div>
          </PanelSectionRow>
        )}
      </Fragment>
    );
  };

  const renderLog = () => {
    if (!logFor || !gameLog) return null;
    return (
      <Fragment>
        <div style={{ fontWeight: 600, marginTop: M3.space.sm }}>Log — {base(logFor)}</div>
        {(gameLog.files || []).map((f) => (
          <div key={String(f.path)} style={{ marginTop: M3.space.sm }}>
            <div style={{ ...M3.type.caption, overflowWrap: "anywhere" }}>{String(f.path)} ({f.lines} lines)</div>
            <PagedText text={Array.isArray(f.tail) ? f.tail.join("\n") : String(f.tail || "")} pageSize={30} maxHeight={200} />
          </div>
        ))}
        {(gameLog.important || []).length > 0 && (
          <div style={{ marginTop: M3.space.sm }}>
            <div style={{ fontWeight: 600 }}>Important</div>
            <PagedText text={(gameLog.important || []).join("\n")} pageSize={40} maxHeight={160} />
          </div>
        )}
        {(gameLog.fixes || []).map((fx) => (
          <PanelSectionRow key={fx.id}>
            <MButton variant="tonal" disabled={busy !== ""}
              onClick={() => armOrRun(`fix:${logFor}:${fx.id}`, fx.label, () => onApplyFix(fx.id))}>
              {confirm === `fix:${logFor}:${fx.id}` ? "Tap again to apply fix" : `Fix: ${fx.label}`}
            </MButton>
          </PanelSectionRow>
        ))}
        <PanelSectionRow>
          <MButton variant="text" onClick={() => { setLogFor(null); setGameLog(null); }}>Close</MButton>
        </PanelSectionRow>
      </Fragment>
    );
  };

  const renderGame = (g: HelixGame) => {
    const way = ways[g.folder] || "auto";
    // An anti-cheat game only gets the FSR drop-in (HelixSR Scaler is not
    // used there); the other games get both ways.
    const allowed = waysFor(g);
    const wayChoices = allowed.length ? ["auto", ...allowed] : [];
    // With one way only, "auto" is that way.
    const effWay = allowed.length === 1 ? allowed[0] : way;
    // What the install action uses: the picked way, or the way the game
    // already has when nothing is picked (an update keeps that way).
    const installWay = installWayFor(effWay, g.installed, allowed);
    // The picked way is not the installed one -> the action switches it.
    const switching = isWaySwitch(effWay, g.installed);
    const installId = `install:${g.folder}:${installWay}`;
    // The chip shows what the game gets: the installed one, the recommended
    // one, or the picked one when it is another way than both.
    const currentWay = g.installed || g.recommended || "";
    const pending = effWay !== "auto" && effWay !== currentWay;
    // Short state chip. It must stay short: a long chip squeezed the game's
    // name out of the header row and wrote it vertically, one letter per line.
    const chipText = pending
      ? `→ ${WAY_SHORT[effWay] || effWay}`
      : g.installed
        ? `✓ ${ROUTE_SHORT[g.installed] || g.installed}${g.version ? ` ${g.version}` : ""}`
        : (g.blocked
            ? ROUTE_SHORT.blocked
            : (ROUTE_SHORT[g.recommended || ""] || (g.supported ? "Recommended" : "Not supported")));
    // The Scaler is not used in an anti-cheat game: no Scaler frame
    // generation there, and an install of an earlier Scaler is not
    // repeated.
    const scalerBlocked = g.anticheat.length > 0;
    return (
      <Card key={g.folder}>
        {/* flexWrap + a flex basis on the name: the chip drops to its own line
            instead of squeezing the name to zero width. */}
        <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", justifyContent: "space-between", gap: M3.space.sm }}>
          <div style={{ flex: "1 1 40%", minWidth: 0, fontWeight: 600, overflowWrap: "break-word" }}>{g.name}</div>
          <Chip tone={pending ? "warning" : g.installed ? "success" : "neutral"}>{chipText}</Chip>
        </div>
        <div style={{ ...M3.type.caption, opacity: 0.8, overflowWrap: "anywhere" }}>{g.folder}</div>
        <div style={M3.type.caption}>
          {(g.inputs || []).join(" · ")}
          {g.anticheat.length > 0 ? ` — anti-cheat (${g.anticheat.join(", ")})` : ""}
        </div>
        {g.installed === "scaler" && (
          <div style={M3.type.caption}>
            FG: {g.frame_generation == null ? "unknown" : g.frame_generation ? "on" : "off"}
            {g.fg_other ? " (game picks another provider)" : ""}
            {g.fg_unsupported ? " (unsupported GPU)" : ""}
            {g.scaler_missing.length > 0 ? ` — missing: ${g.scaler_missing.join(", ")}` : ""}
          </div>
        )}
        {g.launch_options && (
          <div style={{ ...M3.type.caption, overflowWrap: "anywhere" }}>Launch options: {g.launch_options}</div>
        )}
        {g.previous ? (
          <div style={M3.type.caption}>A previous install is kept — rollback can restore it.</div>
        ) : null}
        <PanelSectionRow>
          <div style={{ display: "flex", flexWrap: "wrap", gap: M3.space.sm }}>
            {wayChoices.map((w) => (
              <MButton key={w} variant={way === w ? "filled" : "tonal"}
                disabled={busy !== "" || !g.supported}
                description={WAY_HINTS[w]}
                onClick={() => pickWay(g.folder, w)}>
                {WAY_LABELS[w] || w}
              </MButton>
            ))}
          </div>
        </PanelSectionRow>
        {(effWay === "scaler" || (effWay === "auto" && g.recommended === "scaler")) && g.fg_possible && !scalerBlocked && (
          <PanelSectionRow>
            <ToggleField
              label="Frame generation with Scaler"
              description="Needs an FG build (games with DLSS Frame Generation)"
              checked={!!fgFor[g.folder]}
              onChange={(c: boolean) => setFgFor({ ...fgFor, [g.folder]: c })}
            />
          </PanelSectionRow>
        )}
        <PanelSectionRow>
          <div style={{ display: "flex", flexWrap: "wrap", gap: M3.space.sm }}>
            {!g.installed && allowed.length > 0 && (
              <MButton variant="filled" disabled={busy !== "" || !g.supported}
                onClick={() => onInstall([g.folder], installWay)}>
                {confirm.startsWith(`install:${g.folder}`) ? "Tap again to confirm" : "Install"}
              </MButton>
            )}
            {g.installed && (
              <Fragment>
                {allowed.length > 0 && (
                  <MButton variant="tonal" disabled={busy !== ""}
                    onClick={() => onInstall([g.folder], installWay)}>
                    {confirm === installId
                      ? "Tap again to confirm"
                      : switching
                        ? `Switch to ${WAY_SHORT[effWay] || effWay}`
                        : "Update"}
                  </MButton>
                )}
                <MButton variant="outlined" disabled={busy !== ""}
                  onClick={() => onUninstall([g.folder])}>
                  {confirm.startsWith(`uninstall:${g.folder}`) ? "Tap again to confirm" : "Remove"}
                </MButton>
                {!scalerBlocked && (
                  <MButton variant="text" disabled={busy !== ""} onClick={() => onRepair([g.folder])}>Repair</MButton>
                )}
                {g.installed === "scaler" && g.fg_possible && !scalerBlocked && (
                  <MButton variant="tonal" disabled={busy !== ""}
                    onClick={() => onToggleFg(g.folder, !(g.frame_generation ?? false))}>
                    FG {g.frame_generation ? "off" : "on"}
                  </MButton>
                )}
                {g.previous ? (
                  <MButton variant="outlined" disabled={busy !== ""}
                    onClick={() => onRollback(g.folder)}>
                    {confirm === `rollback:${g.folder}` ? "Tap again to confirm" : "Roll back"}
                  </MButton>
                ) : null}
                <MButton variant="text" disabled={busy !== ""} onClick={() => openSettings(g.folder)}>Settings</MButton>
                <MButton variant="text" disabled={busy !== ""} onClick={() => openLog(g.folder)}>Log</MButton>
              </Fragment>
            )}
          </div>
        </PanelSectionRow>
        {/* The settings editor and the log open inside this card, not at the
            bottom of the game list. */}
        {settingsFor === g.folder && settings && (
          <div style={{ marginTop: M3.space.sm, minWidth: 0 }}>{renderSettingsEditor()}</div>
        )}
        {logFor === g.folder && gameLog && (
          <div style={{ marginTop: M3.space.sm, minWidth: 0 }}>{renderLog()}</div>
        )}
      </Card>
    );
  };

  return (
    <div>
      {heroOp && (
        <PanelSectionRow>
          <div style={{ width: "100%" }}>
            <div style={M3.type.caption}>{heroOp.label}{dlRate ? ` — ${dlRate}` : ""}</div>
            {heroOp.pct < 0
              ? <ProgressBar indeterminate nTransitionSec={M3.motion.bar} />
              : <ProgressBar nProgress={heroOp.pct} nTransitionSec={M3.motion.bar} />}
          </div>
        </PanelSectionRow>
      )}
      {downloading && (
        <PanelSectionRow>
          <MButton variant="outlined" onClick={onCancelDownload}>Cancel download</MButton>
        </PanelSectionRow>
      )}
      {building && (
        <PanelSectionRow>
          <MButton variant="outlined" onClick={onCancelBuild}>Cancel build</MButton>
        </PanelSectionRow>
      )}

      <PanelSection title="Version">
        <PanelSectionRow>
          <div style={{ display: "flex", flexWrap: "wrap", gap: M3.space.sm, width: "100%" }}>
            {listedReleases.map((r) => (
              <MButton key={r.tag} variant={picked === r.tag ? "filled" : "tonal"}
                onClick={() => setPicked(r.tag)}
                description={`${r.zip_name} · ${fmtSize(r.size)}`}>
                {r.tag}{downloadedTags.has(r.tag) ? " ✓" : ""}
              </MButton>
            ))}
          </div>
        </PanelSectionRow>
        {!showAllReleases && releases.length > 3 && (
          <PanelSectionRow>
            <MButton variant="text" onClick={() => setShowAllReleases(true)}>
              Show all {releases.length} releases
            </MButton>
          </PanelSectionRow>
        )}
        <PanelSectionRow>
          <div style={{ display: "flex", flexWrap: "wrap", gap: M3.space.sm }}>
            <MButton variant="filled" disabled={busy !== "" || downloading || !picked}
              onClick={onDownloadOrUse}>
              {pickedDownloaded ? `Switch to ${picked}` : downloading ? "Downloading ..." : `Download ${picked}`}
            </MButton>
            <MButton variant="tonal" disabled={busy !== ""} onClick={() => { refreshReleases(); }}>
              Reload releases
            </MButton>
            <MButton variant="text" onClick={() => setShowPrereleases(!showPrereleases)}>
              {showPrereleases ? "Hide prereleases" : "Show prereleases"}
            </MButton>
          </div>
        </PanelSectionRow>
        {pickedRelease && !pickedDownloaded && pickedRelease.body && (
          <PanelSectionRow>
            <PagedText text={pickedRelease.body.slice(0, 800)} pageSize={12} maxHeight={160} />
          </PanelSectionRow>
        )}
        {(state?.versions || []).length > 0 && (
          <PanelSectionRow>
            <div style={{ width: "100%" }}>
              {(state?.versions || []).slice().sort((a, b) => cmpTags(b.tag, a.tag)).map((v) => (
                <div key={v.tag} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: M3.space.sm, marginBottom: M3.space.xs }}>
                  <div style={{ minWidth: 0 }}>
                    <span style={{ fontWeight: state?.active_tag === v.tag ? 700 : 400 }}>{v.tag}</span>
                    <span style={M3.type.caption}>
                      {` · ${v.dll_version} · ${v.size_mb} MB · network ${v.network_built ? "built" : "missing"} · FG ${v.fg_built ? "built" : "—"}`}
                      {v.scaler_version ? ` · Scaler ${v.scaler_version}` : ""}
                    </span>
                  </div>
                  <div style={{ display: "flex", gap: M3.space.xs }}>
                    {state?.active_tag !== v.tag && (
                      <MButton variant="text" disabled={busy !== ""} onClick={() => onDownloadOrUsePick(v.tag)}>Use</MButton>
                    )}
                    <MButton variant="text" disabled={busy !== ""}
                      onClick={() => armOrRun(`rm:${v.tag}`, `Remove ${v.tag}`, () => onRemoveVersion(v.tag))}>
                      {confirm === `rm:${v.tag}` ? "Confirm?" : "Remove"}
                    </MButton>
                  </div>
                </div>
              ))}
            </div>
          </PanelSectionRow>
        )}
      </PanelSection>

      <PanelSection title="Build">
        <PanelSectionRow>
          <ToggleField
            label="Frame generation"
            description="Also build DLSS frame generation (needs game closed, extra downloads)"
            checked={fgBuild}
            onChange={(c: boolean) => { setFgBuild(c); setReadiness(null); }}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <div style={{ display: "flex", flexWrap: "wrap", gap: M3.space.sm }}>
            <MButton variant="tonal" disabled={busy !== "" || building || !activeVersion}
              onClick={onCheckReady}>
              {busy === "ready" ? "Checking ..." : "Check readiness"}
            </MButton>
            <MButton variant="filled" disabled={busy !== "" || building || !activeVersion}
              onClick={() => onStartBuild(false)}>
              {building ? "Building ..." : "Build"}
            </MButton>
            <MButton variant="outlined" disabled={busy !== "" || building || !activeVersion}
              onClick={() => armOrRun("build:rebuild", "Rebuild", () => onStartBuild(true))}>
              {confirm === "build:rebuild" ? "Tap again to rebuild" : "Rebuild"}
            </MButton>
          </div>
        </PanelSectionRow>
        {readiness && (
          <PanelSectionRow>
            <div style={{ width: "100%" }}>
              {readiness.map((c) => (
                <div key={c.name} style={M3.type.caption}>
                  {c.ok ? "✓" : "✗"} {c.name} — {c.detail}
                </div>
              ))}
            </div>
          </PanelSectionRow>
        )}
        {buildLog && (
          <PanelSectionRow>
            <PagedText text={buildLog.slice(-6000)} pageSize={40} maxHeight={220} />
          </PanelSectionRow>
        )}
      </PanelSection>

      <PanelSection title="Games">
        <PanelSectionRow>
          <div style={{ display: "flex", flexWrap: "wrap", gap: M3.space.sm }}>
            <MButton variant="filled" disabled={busy !== "" || scanning || !activeVersion} onClick={onScan}>
              {scanning ? "Scanning ..." : games.length ? "Re-scan" : "Scan games"}
            </MButton>
            {scanning && <MButton variant="outlined" onClick={onCancelScan}>Cancel</MButton>}
            <MButton variant="tonal" disabled={busy !== "" || !games.length} onClick={onVerify}>Verify installs</MButton>
          </div>
        </PanelSectionRow>
        <PanelSectionRow>
          <TextField
            label="Extra game folder"
            description="A game outside Steam libraries"
            value={folderInput}
            onChange={(e) => setFolderInput((e.target as HTMLInputElement).value)}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <div style={{ display: "flex", flexWrap: "wrap", gap: M3.space.sm }}>
            <MButton variant="tonal" disabled={busy !== "" || !folderInput.trim()} onClick={onAddFolder}>Add folder</MButton>
            {(state?.extra_folders || []).map((f) => (
              <MButton key={f} variant="text" disabled={busy !== ""}
                description={f}
                onClick={() => apiRemoveScanFolder(f).then(() => refreshState())}>
                ✕ {base(f)}
              </MButton>
            ))}
          </div>
        </PanelSectionRow>
        {gamesTag && (
          <PanelSectionRow>
            <div style={M3.type.caption}>
              {games.length} game(s) scanned with {gamesTag}{verify ? ` — ${verify.filter((v) => v.ok).length}/${verify.length} verified` : ""}.
            </div>
          </PanelSectionRow>
        )}
        {/* The game cards in a column with a gap: they used to sit flush
            against each other. */}
        <div style={{ display: "flex", flexDirection: "column", gap: M3.space.md, width: "100%", minWidth: 0 }}>
          {games.map(renderGame)}
        </div>
      </PanelSection>

      <PanelSection title="Storage & updates">
        <PanelSectionRow>
          <div style={{ display: "flex", flexWrap: "wrap", gap: M3.space.sm }}>
            <MButton variant="tonal" disabled={busy !== ""}
              onClick={onRemoveOld}>
              {confirm === "storage:old" ? "Tap again to confirm" : "Remove old versions"}
            </MButton>
            <MButton variant="outlined" disabled={busy !== ""}
              onClick={onFactoryReset}>
              {confirm === "storage:factory" ? "Tap again to confirm" : "Factory reset"}
            </MButton>
            <MButton variant="tonal" disabled={busy !== ""} onClick={onHealth}>
              {busy === "health" ? "Checking ..." : "Health check"}
            </MButton>
          </div>
        </PanelSectionRow>
        <PanelSectionRow>
          <div style={{ display: "flex", flexWrap: "wrap", gap: M3.space.sm, alignItems: "center" }}>
            <span style={M3.type.caption}>
              Plugin v{PLUGIN_VERSION}
              {updateInfo?.update_available ? ` — v${updateInfo.latest} available` : updateInfo && !updateInfo.ok ? " — update check failed" : " — up to date"}
            </span>
            <MButton variant="tonal" disabled={busy !== "" || updating}
              onClick={() => onCheckPluginUpdate(true)}>
              Check for update
            </MButton>
            {updateInfo?.update_available && (
              <MButton variant="filled" disabled={busy !== "" || updating} onClick={onStartPluginUpdate}>
                Update to v{updateInfo.latest}
              </MButton>
            )}
            {updating && (
              <MButton variant="outlined" onClick={onCancelPluginUpdate}>Cancel</MButton>
            )}
          </div>
        </PanelSectionRow>
        {pu && updating && (
          <PanelSectionRow>
            <div style={{ width: "100%" }}>
              <div style={M3.type.caption}>
                {pu.downloaded > 0 ? `${fmtSize(pu.downloaded)}${pu.total ? ` / ${fmtSize(pu.total)}` : ""}${puRate ? ` — ${puRate}` : ""}` : "Starting ..."}
              </div>
              {pu.total > 0
                ? <ProgressBar nProgress={Math.min(100, (100 * pu.downloaded) / pu.total)} nTransitionSec={M3.motion.bar} />
                : <ProgressBar indeterminate nTransitionSec={M3.motion.bar} />}
            </div>
          </PanelSectionRow>
        )}
        <PanelSectionRow>
          <span className={staticClasses.Text} style={M3.type.caption}>
            HelixSR 2.0+ only. The game's own files are backed up before every install.
            {state?.update_pending ? ` Plugin update ${state.update_pending} installed — restart Decky to apply.` : ""}
          </span>
        </PanelSectionRow>
      </PanelSection>

      {output && (
        <PanelSection title="Output">
          <PanelSectionRow>
            <PagedText text={output.slice(-4000)} pageSize={30} maxHeight={220} />
          </PanelSectionRow>
        </PanelSection>
      )}
    </div>
  );
}

export default definePlugin(() => {
  console.log("HelixSR initializing");

  const listener = addEventListener<[ok: boolean, rc: number]>("helixsr_build_done", (ok, rc) => {
    if (!panelOpen) {
      toaster.toast({ title: ok ? "HelixSR build done" : "HelixSR build failed", body: `rc=${rc}` });
    }
  });

  return {
    name: "HelixSR Muse Plugin",
    titleView: <div className={staticClasses.Title}>HelixSR</div>,
    content: <Content />,
    icon: <MdMemory />,
    onDismount() {
      console.log("Unloading HelixSR");
      removeEventListener("helixsr_build_done", listener);
    },
  };
});
