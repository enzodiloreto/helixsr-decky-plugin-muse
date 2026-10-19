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
import { Card, Chip, MButton, PagedText, Stepper } from "./components";
import { M3 } from "./theme";
import {
  HelixGame,
  HelixRelease,
  HelixState,
  DownloadStatus,
  GameLog,
  GameSettings,
  HealthCheck,
  LogFix,
  MigrationStatus,
  PluginUpdateInfo,
  PluginUpdateStatus,
  ReadinessCheck,
  ScalerDownloadStatus,
  ScalerRelease,
  ScalerSettings,
  ScalerState,
  ScanCache,
  ScanStatus,
  VerifyResult,
  apiAddScanFolder,
  apiApplyIniPreset,
  apiApplyLogFix,
  apiCancelBuild,
  apiCancelDownload,
  apiCancelPluginUpdate,
  apiCancelScan,
  apiCancelScalerDownload,
  apiCheckBuildReady,
  apiCheckPluginUpdate,
  apiClearDlssCache,
  apiDeleteIniPreset,
  apiFactoryReset,
  apiGetBuildStatus,
  apiGetDownloadStatus,
  apiGetGameLog,
  apiGetGameSettings,
  apiGetIniPresets,
  apiGetLastScan,
  apiGetMigrationStatus,
  apiGetPluginUpdateStatus,
  apiGetReleases,
  apiGetScanStatus,
  apiGetScalerDownloadStatus,
  apiGetScalerReleases,
  apiGetScalerSettings,
  apiGetScalerState,
  apiGetState,
  apiHealthCheck,
  apiInstallGames,
  apiRemoveOldVersions,
  apiRemoveScanFolder,
  apiRemoveVersion,
  apiRepairGames,
  apiSaveIniPreset,
  apiSetActiveVersion,
  apiSetGameSettings,
  apiSetScalerSettings,
  apiStartBuild,
  apiStartDownload,
  apiStartMigration,
  apiStartPluginUpdate,
  apiStartScan,
  apiStartScalerDownload,
  apiUninstallGames,
  apiVerifyInstalls,
} from "./services/api";
import { PLUGIN_VERSION } from "./version";
import {
  base,
  cmpTags,
  fmtAge,
  fmtSize,
  parseBuildProgress,
  ROUTE_LABELS,
  ROUTE_SHORT,
  shortRoute,
} from "./utils";

// True while the panel is open; the plugin-scope build listener below only
// toasts when it isn't, so completions are announced exactly once.
let panelOpen = false;

function presetsEqual(a: GameSettings, b: GameSettings): boolean {
  if (a.Sharpener !== b.Sharpener || a.Sharpness !== b.Sharpness) return false;
  const wa = a.WaveSize === "auto" ? "default" : a.WaveSize;
  const wb = b.WaveSize === "auto" ? "default" : b.WaveSize;
  if (wa !== wb) return false;
  if ("UseGameSlider" in a && "UseGameSlider" in b) return a.UseGameSlider === b.UseGameSlider;
  return true;
}

function formatPresetSummary(v: GameSettings): string {
  const wave = `wave ${v.WaveSize}`;
  if (!("UseGameSlider" in v)) return `${v.Sharpener} · ${v.Sharpness} · ${wave}`;
  const slider = v.UseGameSlider === "true" ? "game-slider" : "manual";
  return `${v.Sharpener} · ${v.Sharpness} · ${slider} · ${wave}`;
}

// Built-in preset shown first in the picker unless the user saved their own
// preset with the same name (case-insensitive), which then takes over.
const DEFAULT_PRESET_NAME = "Default";

// HelixSR Scaler settings (OptiScaler.ini [HelixSR]): the only place to
// change them besides the in-game menu (Insert). Sharpening is OptiScaler's.
// 2.0+'s Scaler 1.1.0 renamed FrameGeneration to a preset (FGPreset: c =
// NVIDIA's light mode, the file's auto default) and re-spelled QuickFlow.
const SCALER_SETTING_LABELS: Record<string, string> = {
  Int8: "Speed boost — faster int8 math (auto decides; off if the picture shimmers)",
  WaveSize: "Wave size — 64 for RX 7000 / 9000 if the picture is broken",
  FrameGeneration: "Frame generation — needs an FG build (off if crashes or stutters)",
  QuickFlow: "FG quality — Best, Fast or Fastest (auto is Best)",
};
const SCALER_SETTING_OPTIONS: Record<string, [string, string][]> = {
  Int8: [["auto", "Auto"], ["on", "On"], ["off", "Off"]],
  WaveSize: [["auto", "Auto"], ["64", "64"], ["32", "32"]],
  FrameGeneration: [["auto", "Auto"], ["on", "On"], ["off", "Off"]],
  QuickFlow: [["auto", "Auto"], ["off", "Best"], ["on", "Fast"], ["fastest", "Fastest"]],
};
const SCALER_SETTING_LABELS_G3: Record<string, string> = {
  Int8: "Speed boost — faster int8 math (auto decides; off if the picture shimmers)",
  WaveSize: "Wave size — 64 for RX 7000 / 9000 if the picture is broken",
  FGPreset: "Frame generation — Light (NVIDIA's light mode, fast) or Full",
  QuickFlow: "FG motion estimate — runs with Full only",
};
const SCALER_SETTING_OPTIONS_G3: Record<string, [string, string][]> = {
  Int8: [["auto", "Auto"], ["on", "On"], ["off", "Off"]],
  WaveSize: [["auto", "Auto"], ["64", "64"], ["32", "32"]],
  FGPreset: [["auto", "Auto (Light)"], ["c", "Light"], ["full", "Full"]],
  QuickFlow: [["auto", "Auto"], ["off", "Off"], ["fast", "Fast"], ["fastest", "Fastest"]],
};

function Content() {
  const [releases, setReleases] = useState<HelixRelease[]>([]);
  const [state, setState] = useState<HelixState | null>(null);
  const [picked, setPicked] = useState<string>("");
  const [games, setGames] = useState<HelixGame[]>([]);
  const [gamesTag, setGamesTag] = useState<string>("");
  const [busy, setBusy] = useState<string>("");
  const [output, setOutput] = useState<string>("");
  const [building, setBuilding] = useState<boolean>(false);
  const [fgBuild, setFgBuild] = useState<boolean>(false);
  const [downloading, setDownloading] = useState<boolean>(false);
  const [dl, setDl] = useState<DownloadStatus | null>(null);
  const [dlRate, setDlRate] = useState<string>("");
  const dlPrev = useRef<{ t: number; bytes: number } | null>(null);
  const [scalerReleases, setScalerReleases] = useState<ScalerRelease[]>([]);
  const [scalerState, setScalerState] = useState<ScalerState | null>(null);
  const [scalerPicked, setScalerPicked] = useState<string>("");
  const [scalerDownloading, setScalerDownloading] = useState<boolean>(false);
  const [scDl, setScDl] = useState<ScalerDownloadStatus | null>(null);
  const [scDlRate, setScDlRate] = useState<string>("");
  const scPrev = useRef<{ t: number; bytes: number } | null>(null);
  const [scIniFor, setScIniFor] = useState<number | null>(null);
  const [scIni, setScIni] = useState<ScalerSettings | null>(null);
  const [scIniPath, setScIniPath] = useState<string>("");
  const [scSchema, setScSchema] = useState<string>("legacy");
  const [scanning, setScanning] = useState<boolean>(false);
  const [routes, setRoutes] = useState<Record<number, string>>({});
  const [fgWithScaler, setFgWithScaler] = useState<boolean>(true);
  const [iniFor, setIniFor] = useState<number | null>(null);
  const [ini, setIni] = useState<GameSettings | null>(null);
  const [iniSchema, setIniSchema] = useState<string>("legacy");
  const [iniPath, setIniPath] = useState<string>("");
  const [presets, setPresets] = useState<Record<string, GameSettings>>({});
  const [presetDefaults, setPresetDefaults] = useState<GameSettings | null>(null);
  const [presetPick, setPresetPick] = useState<string>("");
  const [presetName, setPresetName] = useState<string>("");
  const [logFor, setLogFor] = useState<number | null>(null);
  const [gameLogs, setGameLogs] = useState<GameLog[]>([]);
  const [logFixes, setLogFixes] = useState<LogFix[]>([]);
  const [verify, setVerify] = useState<VerifyResult[] | null>(null);
  const [filter, setFilter] = useState<string>("");
  const [showPrereleases, setShowPrereleases] = useState<boolean>(false);
  const [showAllReleases, setShowAllReleases] = useState<boolean>(false);
  const [expanded, setExpanded] = useState<Record<number, boolean>>({});
  const [showMode, setShowMode] = useState<string>("all");
  const [openGroups, setOpenGroups] = useState<Record<string, boolean | undefined>>({});
  const [health, setHealth] = useState<HealthCheck[] | null>(null);
  const [migrating, setMigrating] = useState<boolean>(false);
  const [mig, setMig] = useState<MigrationStatus | null>(null);
  const [folderInput, setFolderInput] = useState<string>("");
  const [puCheck, setPuCheck] = useState<PluginUpdateInfo | null>(null);
  const [checkingUpdate, setCheckingUpdate] = useState<boolean>(false);
  const [updating, setUpdating] = useState<boolean>(false);
  const [pu, setPu] = useState<PluginUpdateStatus | null>(null);
  const [puRate, setPuRate] = useState<string>("");
  const puPrev = useRef<{ t: number; bytes: number } | null>(null);
  const [confirm, setConfirm] = useState<string>("");
  const confirmTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Two-tap confirmation for destructive actions: first tap arms (with an
  // expiry), second tap runs. Arming another action replaces the first.
  const armOrRun = async (key: string, prompt: string, fn: () => Promise<void>) => {
    if (confirm !== key) {
      setConfirm(key);
      setOutput(prompt);
      if (confirmTimer.current) clearTimeout(confirmTimer.current);
      confirmTimer.current = setTimeout(() => setConfirm(""), 15000);
      return;
    }
    if (confirmTimer.current) clearTimeout(confirmTimer.current);
    setConfirm("");
    await fn();
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
      if (relsRes.error) {
        setOutput(`Failed to load HelixSR releases: ${relsRes.error}`);
      } else if (relsRes.releases.length) {
        setOutput("");
      }
      return relsRes;
    } catch (e) {
      console.error("[helixsr] get_releases failed", e);
      return null;
    }
  };

  const refreshScalerState = async () => {
    try {
      const s = await apiGetScalerState();
      setScalerState(s);
      return s;
    } catch (e) {
      console.error("[helixsr] get_scaler_state failed", e);
      return null;
    }
  };

  const refreshScalerReleases = async () => {
    try {
      const r = await apiGetScalerReleases(showPrereleases);
      setScalerReleases(r.releases);
      if (!scalerPicked) setScalerPicked(r.releases[0]?.tag || "");
      if (r.error) setOutput(`Failed to load Scaler releases: ${r.error}`);
      return r;
    } catch (e) {
      console.error("[helixsr] get_scaler_releases failed", e);
      return null;
    }
  };

  const onTogglePrereleases = async (c: boolean) => {
    setShowPrereleases(c);
    try {
      const [relsRes, scalerRes] = await Promise.all([apiGetReleases(c), apiGetScalerReleases(c)]);
      setReleases(relsRes.releases);
      setScalerReleases(scalerRes.releases);
      if (!relsRes.releases.some((r) => r.tag === picked)) setPicked(relsRes.releases[0]?.tag || "");
      if (!scalerRes.releases.some((r) => r.tag === scalerPicked)) setScalerPicked(scalerRes.releases[0]?.tag || "");
      if (relsRes.error) setOutput(`Failed to load HelixSR releases: ${relsRes.error}`);
    } catch (e) {
      console.error("[helixsr] prerelease toggle failed", e);
    }
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
          const cached: ScanCache = await apiGetLastScan();
          if (cached.games.length) {
            setGames(cached.games);
            setGamesTag(cached.tag || "");
            const rm: Record<number, string> = {};
            cached.games.forEach((g) => { rm[g.index] = g.route; });
            setRoutes(rm);
            if (!relsRes.error) {
              const tagNote = s.active_tag && cached.tag && cached.tag !== s.active_tag
                ? ` (scanned with ${cached.tag})` : "";
              setOutput(`Showing games scanned ${fmtAge(cached.scanned_at)}${tagNote} — Scan to refresh.`);
            }
          }
        } catch { /* no cached scan yet */ }
        try {
          // Resume polling for operations still running (e.g. panel was closed).
          const [dl0, sc0, mg0, pu0, scdl0] = await Promise.all([
            apiGetDownloadStatus(), apiGetScanStatus(), apiGetMigrationStatus(),
            apiGetPluginUpdateStatus(), apiGetScalerDownloadStatus()]);
          if (dl0.running) { setDl(dl0); setDownloading(true); }
          if (sc0.running) setScanning(true);
          if (mg0.running) { setMig(mg0); setMigrating(true); }
          if (pu0.running) { setPu(pu0); setUpdating(true); }
          if (scdl0.running) { setScDl(scdl0); setScalerDownloading(true); }
        } catch { /* nothing running */ }
        try {
          const [scalerRels, scalerSt] = await Promise.all([apiGetScalerReleases(), apiGetScalerState()]);
          setScalerReleases(scalerRels.releases);
          setScalerState(scalerSt);
          setScalerPicked(scalerRels.releases[0]?.tag || "");
          if (scalerRels.error) console.error("[helixsr] scaler releases:", scalerRels.error);
        } catch { /* scaler feed is best-effort */ }
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
          setOutput(st.log || `Build finished rc=${rc}, no log captured (full log: ${st.log_path}).`);
        } catch {
          setOutput(`Build finished rc=${rc}, could not read build log.`);
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

  // Poll scaler download progress while one runs
  useEffect(() => {
    if (!scalerDownloading) return;
    const t = setInterval(async () => {
      try {
        const st = await apiGetScalerDownloadStatus();
        setScDl(st);
        const now = Date.now();
        const prev = scPrev.current;
        scPrev.current = { t: now, bytes: st.downloaded };
        if (prev && now > prev.t && st.downloaded >= prev.bytes && !st.done) {
          const speed = ((st.downloaded - prev.bytes) * 1000) / (now - prev.t);
          if (speed > 0) {
            const left = st.total > st.downloaded ? (st.total - st.downloaded) / speed : 0;
            setScDlRate(`${fmtSize(speed)}/s${st.total ? ` — ~${Math.max(1, Math.ceil(left))}s left` : ""}`);
          }
        }
        if (st.done) {
          setScalerDownloading(false);
          setScDlRate("");
          if (st.cancelled) {
            setOutput("Scaler download cancelled.");
          } else if (st.ok) {
            setOutput(`Downloaded Scaler ${st.tag} (${st.dir}).`);
            toaster.toast({ title: `Downloaded Scaler ${st.tag}`, body: "" });
          } else {
            toaster.toast({ title: "Scaler download failed", body: st.error || "" });
            setOutput(st.error || "scaler download failed");
          }
          refreshScalerState();
        }
      } catch { /* ignore */ }
    }, 500);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scalerDownloading]);

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
            setIniFor(null);
            setIni(null);
            setLogFor(null);
            setGameLogs([]);
            setLogFixes([]);
            const rm: Record<number, string> = {};
            st.games.forEach((g) => { rm[g.index] = g.route; });
            setRoutes(rm);
            setOutput(`Found ${st.games.length} compatible game(s) with ${st.tag}.${st.skipped ? ` ${st.skipped} folder(s) skipped (unreadable).` : ""}`);
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
            if (st.mode === "manual") {
              setOutput(`Update v${st.version} downloaded to:\n${st.path}\n\nInstall it via Decky > Settings > Developer > Install Plugin from ZIP File, then restart Decky.`);
              toaster.toast({ title: `Update downloaded`, body: "Install it from Decky settings" });
            } else {
              setOutput(`Plugin update to v${st.version} installed — restart Decky to apply it.`);
              toaster.toast({ title: `Plugin updated to v${st.version}`, body: "Restart Decky to apply" });
            }
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

  // Poll migration status while a migration runs
  useEffect(() => {
    if (!migrating) return;
    const t = setInterval(async () => {
      try {
        const st = await apiGetMigrationStatus();
        setMig(st);
        if (st.done) {
          setMigrating(false);
          const done = st.results.filter((r) => r.ok).length;
          const lines = st.results.map((r) => `${r.ok ? "✓" : "✗"} ${r.name}${r.ok ? "" : `: ${r.detail.slice(-300)}`}`);
          setOutput(`Migration to ${st.tag}: ${done}/${st.results.length} moved.${lines.length ? "\n" + lines.join("\n") : ""}${st.error ? `\n${st.error}` : ""}`);
          toaster.toast({ title: st.ok ? `Migrated to ${st.tag}` : "Migration finished with errors", body: `${done}/${st.results.length} games` });
          refreshState();
          onScan();
        }
      } catch { /* ignore */ }
    }, 1000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [migrating]);

  // Poll build log while a build runs
  useEffect(() => {
    if (!building) return;
    const t = setInterval(async () => {
      try {
        const st = await apiGetBuildStatus();
        setOutput(st.log);
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
  // Downloaded versions plus the newest few; the rest hide behind an expander
  // so a long release history doesn't dominate the panel.
  const listedReleases = showAllReleases
    ? releases
    : releases.filter((r, i) => downloadedTags.has(r.tag) || i < 3);
  const activeVersion = state?.versions.find((v) => v.tag === state.active_tag);
  const pickedRelease = releases.find((r) => r.tag === picked);
  const pickedDownloaded = picked ? downloadedTags.has(picked) : false;
  const scalerPickedRelease = scalerReleases.find((r) => r.tag === scalerPicked);

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
        // the FG flag can't carry across to a build without the FG engine
        const nv = (st.versions || []).find((v) => v.tag === st.active_tag);
        if (nv && nv.fg_available === false) setFgBuild(false);
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

  const onCancelScalerDownload = async () => {
    try {
      await apiCancelScalerDownload();
    } catch (e) {
      console.error("[helixsr] cancel_scaler_download failed", e);
    }
  };

  const onScalerDownload = async () => {
    const rel = scalerReleases.find((r) => r.tag === scalerPicked);
    if (!rel) {
      setOutput("No Scaler release selected.");
      return;
    }
    setScDl(null);
    setScDlRate("");
    scPrev.current = null;
    try {
      const r = await apiStartScalerDownload(rel.tag, rel.tar_url);
      if (!r.ok) {
        toaster.toast({ title: "Scaler download not started", body: r.error || "" });
        setOutput(r.error || "scaler download not started");
        return;
      }
      setScalerDownloading(true);
      setOutput(`Downloading ${rel.tar_name} (${fmtSize(rel.size)}) ...`);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      toaster.toast({ title: "Scaler download failed to start", body: msg });
      setOutput(`Could not start scaler download: ${msg}`);
      console.error("[helixsr] start_scaler_download failed", e);
    }
  };

  const onOpenScIni = async (g: HelixGame) => {
    setBusy(`scini-${g.index}`);
    try {
      const r = await apiGetScalerSettings(g.index);
      if (!r.ok || !r.values) {
        setOutput(r.error || "cannot read scaler settings");
        return;
      }
      setScIni(r.values);
      setScIniPath(r.path || "");
      setScSchema(r.schema || "legacy");
      setScIniFor(g.index);
    } finally {
      setBusy("");
    }
  };

  const onSaveScIni = async () => {
    if (scIniFor === null || !scIni) return;
    setBusy("scini-save");
    try {
      const r = await apiSetScalerSettings(scIniFor, scIni);
      setOutput(r.output || "");
      toaster.toast({ title: r.ok ? "Scaler settings saved" : "Save failed", body: r.ok ? "" : (r.output || "").slice(-200) });
    } finally {
      setBusy("");
    }
  };

  const onCopyLaunchOptions = async (text: string) => {
    const done = () => toaster.toast({ title: "Launch options copied", body: "Paste in Steam → Properties → Launch Options" });
    try {
      await navigator.clipboard.writeText(text);
      done();
      return;
    } catch (e) {
      console.error("[helixsr] async clipboard failed, trying fallback", e);
    }
    // Fallback for CEF without async clipboard permission.
    try {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      if (!document.execCommand("copy")) throw new Error("execCommand returned false");
      document.body.removeChild(ta);
      done();
    } catch (e) {
      console.error("[helixsr] clipboard fallback failed", e);
      setOutput(`Copy failed — select the text by hand:\n${text}`);
      toaster.toast({ title: "Copy failed", body: "select the text manually" });
    }
  };

  const pickedRoute = (g: HelixGame): string => {
    const avail = g.available || [];
    if (avail.length === 1) return avail[0];
    return routes[g.index] || g.route;
  };

  const onRemove = async () => {
    if (!picked || !pickedDownloaded) return;
    await armOrRun(`remove ${picked}`,
      `Tap again to confirm removing ${picked} files.`,
      async () => {
        setBusy("version");
        try {
          await apiRemoveVersion(picked);
          setOutput(`Removed ${picked}.`);
          setGames([]);
          setGamesTag("");
        } finally {
          setBusy("");
          refreshState();
        }
      });
  };

  const runReadiness = async (): Promise<ReadinessCheck[] | null> => {
    const c = await apiCheckBuildReady(activeVersion?.tag || null, fgBuild);
    const lines = (c.checks || []).map((x) => `${x.ok ? "✓" : "✗"} ${x.name}: ${x.detail}`);
    setOutput([(c.error || (!c.ok ? "Not ready to build:" : "Ready to build.")), ...lines].join("\n"));
    return c.ok ? (c.checks || []) : null;
  };

  const onCheckBuild = async () => {
    setBusy("ready");
    try {
      await runReadiness();
    } finally {
      setBusy("");
    }
  };

  const onCancelBuild = async () => {
    try {
      const r = await apiCancelBuild();
      if (!r.ok) setOutput(r.error || "build not cancelled");
    } catch (e) {
      console.error("[helixsr] cancel_build failed", e);
    }
  };

  const onBuild = async (rebuild: boolean) => {
    if (rebuild) {
      await armOrRun("rebuild network",
        "Tap again to confirm: rebuild the network from scratch (takes a while).",
        () => doBuild(true));
    } else {
      await doBuild(false);
    }
  };

  const doBuild = async (rebuild: boolean) => {
    setBusy("build");
    try {
      if (!(await runReadiness())) {
        toaster.toast({ title: "Build not ready", body: "see Log" });
        return;
      }
      const r = await apiStartBuild(rebuild, fgBuild);
      if (!r.ok) {
        toaster.toast({ title: "Build not started", body: r.error || "" });
        setOutput(r.error || "build not started");
        return;
      }
      setBuilding(true);
      setOutput("Build started (compiling shaders — can take several minutes). Polling log ...");
      try {
        const s0 = await apiGetBuildStatus();
        if (s0.log) setOutput(s0.log);
      } catch { /* first poll will retry */ }
    } finally {
      setBusy("");
    }
  };

  const onScan = async () => {
    try {
      const r = await apiStartScan();
      if (!r.ok) {
        setOutput(r.error || "scan not started");
        return;
      }
      setScanning(true);
      setOutput("Scanning Steam libraries for DLSS / XeSS / FSR games ...");
    } catch (e) {
      console.error("[helixsr] start_scan failed", e);
    }
  };

  const onCancelScan = async () => {
    try {
      await apiCancelScan();
    } catch (e) {
      console.error("[helixsr] cancel_scan failed", e);
    }
  };

  // Refresh the games list from the persisted scan (the backend rewrites it
  // after every install/uninstall): an uninstall doesn't warrant a
  // minutes-long library re-walk. Falls back to a real scan when there is
  // no cached list, and mimics what a scan completion resets.
  const refreshGamesFromCache = async () => {
    try {
      const cached = await apiGetLastScan();
      if (!cached.games.length) return false;
      setGames(cached.games);
      setVerify(null);
      setIniFor(null);
      setIni(null);
      setLogFor(null);
      setGameLogs([]);
      setLogFixes([]);
      return true;
    } catch {
      return false;
    }
  };

  const onOpenIni = async (g: HelixGame) => {
    setBusy(`ini-${g.index}`);
    try {
      const r = await apiGetGameSettings(g.index);
      if (!r.ok || !r.values) {
        setOutput(r.error || "cannot read settings");
        return;
      }
      setIni(r.values);
      setIniSchema(r.layout === "modern" ? "modern" : "legacy");
      setIniPath(r.path || "");
      setIniFor(g.index);
      setPresetPick("");
      setPresetName("");
      await onLoadPresets();
      if (r.note) setOutput(r.note);
    } finally {
      setBusy("");
    }
  };

  const onSaveIni = async () => {
    if (iniFor === null || !ini) return;
    setBusy("ini-save");
    try {
      const r = await apiSetGameSettings(iniFor, ini);
      setOutput(r.output || "");
      toaster.toast({ title: r.ok ? "Settings saved" : "Save failed", body: r.ok ? "" : (r.output || "").slice(-200) });
    } finally {
      setBusy("");
    }
  };

  const onHealth = async () => {
    setBusy("health");
    try {
      const r = await apiHealthCheck();
      if (!r.checks) {
        setOutput(r.error || "health check failed");
        return;
      }
      setHealth(r.checks);
      const bad = r.checks.filter((c) => !c.ok);
      setOutput(bad.length
        ? `Health: ${bad.length} issue(s):\n${bad.map((c) => `✗ ${c.name}: ${c.detail}`).join("\n")}`
        : "Health: all checks pass.");
    } catch (e) {
      console.error("[helixsr] health_check failed", e);
      setOutput("health check failed");
    } finally {
      setBusy("");
    }
  };

  const onVerify = async () => {
    setBusy("verify");
    try {
      const r = await apiVerifyInstalls();
      if (!r.ok) {
        setOutput(r.error || "verify failed");
        return;
      }
      setVerify(r.games);
      const broken = r.games.filter((g) => !g.ok);
      setOutput(broken.length
        ? `${broken.length} game(s) need repair:\n${broken.map((g) => `✗ ${g.name}: ${g.problems.join("; ")}`).join("\n")}`
        : `All ${r.games.length} installed game(s) verified OK.`);
    } finally {
      setBusy("");
    }
  };

  const onRepair = async (indices: number[]) => {
    if (!indices.length) return;
    setBusy("repair");
    try {
      const r = await apiRepairGames(indices);
      setOutput(r.output || "");
      toaster.toast({ title: r.ok ? "Repair done" : "Repair failed", body: r.ok ? "" : (r.output || "").slice(-200) });
      if (r.ok) onVerify();
    } finally {
      setBusy("");
    }
  };

  const onOpenLog = async (g: HelixGame) => {
    setBusy(`log-${g.index}`);
    try {
      const r = await apiGetGameLog(g.index);
      if (!r.ok) {
        setOutput(r.error || "cannot read log");
        return;
      }
      setGameLogs(r.logs || []);
      setLogFixes(r.fixes || []);
      setLogFor(g.index);
    } finally {
      setBusy("");
    }
  };

  const onApplyFix = async (fixId: string) => {
    if (logFor === null) return;
    setBusy("log-fix");
    try {
      const r = await apiApplyLogFix(logFor, fixId);
      setOutput(r.output || "");
      toaster.toast({ title: r.ok ? "Fix applied" : "Fix failed", body: r.ok ? "" : (r.output || "").slice(-200) });
      if (r.ok) onOpenLog({ index: logFor } as HelixGame);
    } finally {
      setBusy("");
    }
  };

  const onLoadPresets = async () => {
    try {
      const r = await apiGetIniPresets();
      if (r.ok) {
        setPresets(r.presets || {});
        if (r.defaults) setPresetDefaults(r.defaults);
      }
    } catch (e) {
      console.error("[helixsr] get_ini_presets failed", e);
    }
  };

  const onSavePreset = async () => {
    if (!ini) return;
    const trimmed = presetName.trim();
    if (!trimmed) return;
    setBusy("preset");
    try {
      const r = await apiSaveIniPreset(trimmed, { ...ini });
      if (!r.ok) {
        setOutput(r.error || "cannot save preset");
        return;
      }
      setPresets(r.presets || {});
      setPresetPick(trimmed);
      setPresetName("");
      setOutput(r.overwrote ? `Preset "${trimmed}" updated.` : `Preset "${trimmed}" saved.`);
    } finally {
      setBusy("");
    }
  };

  const onDeletePreset = async () => {
    if (!presetPick) return;
    await armOrRun(`delete preset ${presetPick}`,
      `Tap again to confirm deleting preset "${presetPick}".`,
      async () => {
        setBusy("preset");
        try {
          const r = await apiDeleteIniPreset(presetPick);
          if (r.ok) setPresets(r.presets || {});
          setPresetPick("");
        } finally {
          setBusy("");
        }
      });
  };

  const onApplyPreset = async () => {
    if (iniFor === null || !presetPick) return;
    const applied = presetPick;
    setBusy("preset");
    try {
      // Stored presets are translated to the game's schema server-side; the
      // built-in Default resolves to that game's defaults the same way.
      const r = await apiApplyIniPreset(iniFor, applied);
      setOutput(r.output || "");
      toaster.toast({ title: r.ok ? `Preset "${applied}" applied` : "Apply failed", body: r.ok ? "" : (r.output || "").slice(-200) });
      if (r.ok) {
        const g = games.find((x) => x.index === iniFor);
        if (g) await onOpenIni(g);
        setPresetPick(applied);
      }
    } finally {
      setBusy("");
    }
  };

  const onResetAll = async () => {
    await armOrRun("reset everything",
      "Tap again to confirm: uninstall everywhere and wipe versions, caches and settings (presets are kept).",
      async () => {
        setBusy("reset");
        try {
          const r = await apiFactoryReset();
          if (!r.ok) {
            setOutput(r.error || "reset failed");
            return;
          }
          const lines = [
            `Uninstalled: ${(r.games_uninstalled || []).join(", ") || "none"}`,
            ...((r.games_failed || []).map((x) => `FAILED: ${x}`)),
            `Versions removed: ${(r.versions_removed || []).join(", ") || "none"}`,
            ...((r.versions_skipped || []).map((x) => `kept ${x}`)),
            `Freed ${r.freed_mb} MB.`,
            r.note || "",
          ];
          setOutput(lines.join("\n"));
          setGames([]);
          setGamesTag("");
          setVerify(null);
          refreshState();
        } finally {
          setBusy("");
        }
      });
  };

  const onInstallAll = async () => {
    const targets = installAllTargets;
    if (!targets.length) return;
    setBusy("games-all");
    try {
      const r = await apiInstallGames(targets.map((g) => g.index), "");
      setOutput(r.output);
      const note = r.output.includes("skipped") || !r.ok ? r.output.slice(-200) : "";
      toaster.toast({ title: r.ok ? `Installed in ${targets.length} game(s)` : "Install failed", body: note });
      if (r.ok) {
        const done = new Set(targets.map((t) => t.index));
        setGames((gs) => gs.map((x) => (!x.installed && done.has(x.index)
          ? { ...x, installed: true, status: "HelixSR installed" } : x)));
        refreshState();
      }
    } finally {
      setBusy("");
    }
  };

  const onRemoveAll = async () => {
    const n = visibleGames.filter((g) => g.installed).length;
    await armOrRun("remove all games",
      `Tap again to confirm removing HelixSR from ${n} game(s).`,
      async () => {
        const targets = visibleGames.filter((g) => g.installed);
        if (!targets.length) return;
        setBusy("games-all");
        try {
          const r = await apiUninstallGames(targets.map((g) => g.index));
          setOutput(r.output);
          toaster.toast({ title: r.ok ? `Removed from ${targets.length} game(s)` : "Uninstall failed", body: r.ok ? "" : r.output.slice(-200) });
          if (r.ok && !(await refreshGamesFromCache())) onScan();
        } finally {
          setBusy("");
        }
      });
  };

  const onAddFolder = async () => {
    const path = folderInput.trim();
    if (!path) return;
    setBusy("folder");
    try {
      const r = await apiAddScanFolder(path);
      if (!r.ok) {
        setOutput(r.error || "cannot add folder");
        return;
      }
      setFolderInput("");
      refreshState();
      setOutput(`Scan folder added. Rescan to include it.`);
    } finally {
      setBusy("");
    }
  };

  const onRemoveFolder = async (path: string) => {
    setBusy("folder");
    try {
      await apiRemoveScanFolder(path);
      refreshState();
      setOutput(`Removed scan folder. Rescan to update the list.`);
    } finally {
      setBusy("");
    }
  };

  const onRemoveOld = async () => {
    await armOrRun("remove old versions",
      "Tap again to confirm removing all versions except the active one.",
      async () => {
        setBusy("storage");
        try {
          const r = await apiRemoveOldVersions();
          const lines = [...r.removed.map((t) => `removed ${t}`), ...r.skipped.map((t) => `kept ${t}`)];
          setOutput(`Freed ${r.freed_mb} MB.\n${lines.join("\n")}`);
          refreshState();
        } finally {
          setBusy("");
        }
      });
  };

  const onClearDlss = async () => {
    await armOrRun("clear SDK cache",
      "Tap again to confirm deleting cached NVIDIA DLLs (re-downloaded on next build).",
      async () => {
        setBusy("storage");
        try {
          const r = await apiClearDlssCache();
          setOutput(`Freed ${r.freed_mb} MB.\n${r.cleared.join("\n") || "nothing cached"}`);
        } finally {
          setBusy("");
        }
      });
  };

  const onCheckPluginUpdate = async (force: boolean) => {
    setCheckingUpdate(true);
    try {
      setPuCheck(await apiCheckPluginUpdate(force));
    } catch (e) {
      console.error("[helixsr] check_plugin_update failed", e);
      setPuCheck({ ok: false, error: "update check failed" });
    } finally {
      setCheckingUpdate(false);
    }
  };

  // Decky's privileged installer bridge (same JS realm Decky itself runs in).
  // Null when unreachable: callers fall back to the backend flows.
  const getDeckyBridge = (): { call: (route: string, ...args: any[]) => Promise<any> } | null => {
    const b = (window as any).DeckyBackend;
    return b && typeof b.call === "function" ? b : null;
  };

  const onReloadPlugin = async () => {
    const bridge = getDeckyBridge();
    if (!bridge) {
      setOutput("Decky bridge unavailable — reinstall the plugin zip and restart Decky.");
      return;
    }
    if (downloading || scanning || migrating || building || updating) {
      setOutput("Finish the running task first, then reload.");
      toaster.toast({ title: "Reload blocked", body: "another task is running" });
      return;
    }
    setBusy("reload");
    try {
      let triggered = false;
      for (const name of ["HelixSR Muse Plugin", "HelixSR-Decky-Plugin-Muse"]) {
        try {
          // Stops our backend and re-imports it (plus fresh frontend bundle):
          // heals UI-newer mismatches immediately, no reinstall or restart.
          await bridge.call("utilities/enable_plugin", name);
          triggered = true;
          break;
        } catch { /* unknown name on this Decky: try the other */ }
      }
      if (!triggered) {
        setOutput("Decky refused the reload — reinstall the plugin zip and restart Decky.");
        return;
      }
    } finally {
      setBusy("");
    }
    const deadline = Date.now() + 60000;
    while (Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, 2000));
      let s = null;
      try {
        s = await Promise.race([
          apiGetState(),
          new Promise<null>((r) => setTimeout(() => r(null), 8000)),
        ]);
      } catch { /* backend mid-replacement: keep waiting */ }
      if (s && s.plugin_version === PLUGIN_VERSION) {
        setOutput("Plugin reloaded — UI and backend match again.");
        toaster.toast({ title: "Plugin reloaded", body: "versions match" });
        refreshState();
        return;
      }
    }
    setOutput("Reloaded, but versions still differ — reopen this panel; if it persists, reinstall the zip and restart Decky.");
  };

  const onStartPluginUpdate = async () => {
    const bridge = getDeckyBridge();
    if (bridge && puCheck?.latest && puCheck?.url) {
      try {
        await updateViaDecky(bridge, puCheck.latest, puCheck.url);
        return;
      } catch (e) {
        console.error("[helixsr] decky install unavailable, falling back", e);
      }
    }
    try {
      const r = await apiStartPluginUpdate();
      if (!r.ok) {
        setOutput(r.error || "update not started");
        toaster.toast({ title: "Update not started", body: r.error || "" });
        return;
      }
      setUpdating(true);
      setPu(null);
      setPuRate("");
      puPrev.current = null;
      setOutput(`Downloading plugin update v${r.version} ...`);
    } catch (e) {
      console.error("[helixsr] start_plugin_update failed", e);
    }
  };

  // Update through Decky's own machinery: native consent modal, privileged
  // install (works for root-owned installs too), hot-reload afterwards.
  // Throws only before Decky's popup appears (caller falls back); once the
  // install is triggered this owns the flow to success or timeout.
  const updateViaDecky = async (
    bridge: { call: (route: string, ...args: any[]) => Promise<any> },
    tag: string,
    url: string,
  ) => {
    if (downloading || scanning || migrating || building) {
      setOutput("Finish the running download, scan, build or migration first.");
      toaster.toast({ title: "Update not started", body: "another task is running" });
      return;
    }
    // A legacy entry would install side-by-side instead of replacing: refuse
    // (no silent auto-uninstall) and point at the old entry.
    const plugins = await bridge.call("loader/get_plugins");
    const names = (Array.isArray(plugins) ? plugins : []).map((p) => p && p.name).filter(Boolean);
    if (names.includes("HelixSR-Decky-Plugin-Muse")) {
      setOutput("Old 'HelixSR-Decky-Plugin-Muse' entry still installed: uninstall it first (Decky menu), then update.");
      toaster.toast({ title: "Update blocked", body: "uninstall the old entry first" });
      return;
    }
    // install_type 2 = UPDATE. Shows Decky's install popup for confirmation.
    await bridge.call("utilities/install_plugin", url, "HelixSR Muse Plugin", tag, "", 2);
    setUpdating(true);
    setPu(null);
    setPuRate("");
    puPrev.current = null;
    setOutput(`Confirm Decky's install popup for ${tag} ...`);
    const deadline = Date.now() + 10 * 60 * 1000;
    while (Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, 3000));
      let s = null;
      try {
        s = await Promise.race([
          apiGetState(),
          new Promise<null>((r) => setTimeout(() => r(null), 8000)),
        ]);
      } catch { /* backend mid-replacement: keep waiting */ }
      if (s && cmpTags(s.plugin_version, tag) === 0) {
        setUpdating(false);
        setOutput(`Updated to ${tag} — reopen this panel for the new UI.`);
        toaster.toast({ title: `Plugin updated to ${tag}`, body: "Reopen this panel" });
        try {
          await refreshState();
        } catch { /* ignore */ }
        onCheckPluginUpdate(false);
        return;
      }
    }
    setUpdating(false);
    setOutput("Still on the old version — confirm Decky's popup, or retry the update.");
    toaster.toast({ title: "Update not detected", body: "confirm the popup or retry" });
  };

  const onMigrate = async () => {
    if (!activeVersion) return;
    await armOrRun(`migrate to ${activeVersion.tag}`,
      `Tap again to confirm migrating ${migCandidates.length} game(s) to ${activeVersion.tag}.`,
      async () => {
        try {
          const r = await apiStartMigration(activeVersion.tag);
          if (!r.ok) {
            setOutput(r.error || "migration not started");
            return;
          }
          setMigrating(true);
          setMig(null);
          setOutput(`Migrating games to ${activeVersion.tag} ...`);
        } catch (e) {
          console.error("[helixsr] start_migration failed", e);
        }
      });
  };

  const onInstall = async (g: HelixGame) => {
    const route = pickedRoute(g);
    if (route === "scaler") {
      const fgNote = activeVersion?.generation === 3
        ? (fgWithScaler && activeVersion?.fg_built
          ? " Frame generation goes in with it."
          : (activeVersion?.fg_built ? "" : " (No FG built: upscaling only.)"))
        : "";
      await armOrRun(`install scaler ${g.index}`,
        `Tap again to confirm installing HelixSR Scaler in "${g.name}". Not for online games or games with anti-cheat (ban risk).${fgNote} Steam launch options only if the game's row below says so.`,
        async () => {
          setBusy(`game-${g.index}`);
          try {
            const fg = activeVersion?.generation === 3 ? fgWithScaler : undefined;
            const r = await apiInstallGames([g.index], route, fg);
            setOutput(r.output);
            toaster.toast({ title: r.ok ? `Scaler installed in ${g.name}` : "Install failed", body: r.ok ? (fg ? "" : "Scaler installed (frame generation off)") : r.output.slice(-200) });
            if (r.ok && !(await refreshGamesFromCache())) onScan();
          } finally {
            setBusy("");
          }
        });
      return;
    }
    setBusy(`game-${g.index}`);
    try {
      const r = await apiInstallGames([g.index], route);
      setOutput(r.output);
      toaster.toast({ title: r.ok ? `Installed in ${g.name}` : "Install failed", body: r.ok ? "" : r.output.slice(-200) });
      if (r.ok) setGames((gs) => gs.map((x) => (x.index === g.index ? { ...x, installed: true, status: "HelixSR installed" } : x)));
    } finally {
      setBusy("");
    }
  };

  const onUninstall = async (g: HelixGame) => {
    setBusy(`game-${g.index}`);
    try {
      const r = await apiUninstallGames([g.index]);
      setOutput(r.output);
      toaster.toast({ title: r.ok ? `Removed from ${g.name}` : "Uninstall failed", body: r.ok ? "" : r.output.slice(-200) });
      if (r.ok && !(await refreshGamesFromCache())) onScan();
    } finally {
      setBusy("");
    }
  };

  const hasVersions = (state?.versions.length || 0) > 0;
  const brokenSet = verify ? new Set(verify.filter((v) => !v.ok).map((v) => v.index)) : new Set<number>();
  const statusGames = showMode === "installed" ? games.filter((g) => g.installed)
    : showMode === "available" ? games.filter((g) => !g.installed)
    : showMode === "repair" ? games.filter((g) => brokenSet.has(g.index))
    : games;
  const visibleGames = filter.trim()
    ? statusGames.filter((g) => g.name.toLowerCase().includes(filter.trim().toLowerCase()))
    : statusGames;
  const sortedGames = [...visibleGames].sort((a, b) =>
    Number(b.installed) - Number(a.installed) || a.index - b.index);
  const installedGroup = visibleGames.filter((g) => g.installed);
  const availGroup = visibleGames.filter((g) => !g.installed);
  // undefined = auto: the sole non-empty group starts expanded (a first scan
  // shouldn't present two collapsed headers); an explicit tap wins over the
  // default from then on.
  const openInstalled = openGroups.installed ?? (installedGroup.length > 0 && availGroup.length === 0);
  const openNot = openGroups.not ?? (availGroup.length > 0 && installedGroup.length === 0);
  const installedCount = games.filter((g) => g.installed).length;
  const visInstalled = visibleGames.filter((g) => g.installed).length;
  const downloadedVersions = (state?.versions || []).filter((v) => downloadedTags.has(v.tag));
  const totalMB = downloadedVersions.reduce((a, v) => a + (v.size_mb || 0), 0);
  const activeLayout = state?.versions.find((v) => v.tag === state?.active_tag)?.layout || "legacy";
  // "Install all" can only cover games the active HelixSR can actually install
  // into: on 1.9.0+ (modern layout) that is games with FSR 3.1 files (the
  // drop-in's requirement); the backend skips the rest with a note.
  const installAllTargets = visibleGames.filter((g) => !g.installed
    && (activeLayout !== "modern" || (g.fsr || []).length > 0));
  const buildProg = building ? parseBuildProgress(output) : null;
  // Live operation reflected in the hero so deep-scrolled users see state.
  const heroOp = downloading
    ? {
        text: `Downloading ${dl?.tag || ""} — ${dl && dl.total > 0 ? `${Math.floor((100 * dl.downloaded) / dl.total)}%${dlRate ? ` · ${dlRate}` : ""}` : "starting…"}`,
        pct: dl && dl.total > 0 ? Math.min(100, (100 * dl.downloaded) / dl.total) : null,
      }
    : building
    ? {
        text: buildProg ? `Building — ${buildProg.pct}% · ${buildProg.label}` : "Building network…",
        pct: buildProg?.pct ?? null,
      }
    : scanning
    ? { text: "Scanning games…", pct: null }
    : migrating
    ? {
        text: mig && mig.total > 0 ? `Migrating ${mig.current}/${mig.total}${mig.current_name ? ` · ${mig.current_name}` : ""}…` : "Starting migration…",
        pct: mig && mig.total > 0 ? Math.min(100, (100 * mig.current) / mig.total) : null,
      }
    : updating
    ? {
        text: `Updating plugin${pu && pu.total > 0 ? ` — ${Math.floor((100 * pu.downloaded) / pu.total)}%` : "…"}`,
        pct: pu && pu.total > 0 ? Math.min(100, (100 * pu.downloaded) / pu.total) : null,
      }
    : null;
  const newestRelease = releases.length ? releases[0] : null;
  const updateAvailable = newestRelease && ![...downloadedTags].some((t) => cmpTags(t, newestRelease.tag) >= 0);
  const step = !state || !state.versions.length ? 1 : !activeVersion?.network_built ? 2 : !games.length ? 3 : 4;
  const STEP_TEXT = ["", "Download a HelixSR release", "Build the network", "Scan your games", "Install into games"];
  const migCandidates = activeVersion && activeVersion.dll_version && activeVersion.dll_version !== "?"
    ? games.filter((g) => g.installed && !(g.installed_versions || []).includes(activeVersion.dll_version))
    : [];
  const presetNames = Object.keys(presets).sort((a, b) => a.localeCompare(b));
  const showBuiltinDefault = presetDefaults !== null
    && !presetNames.some((n) => n.toLowerCase() === DEFAULT_PRESET_NAME.toLowerCase());
  const allPresetNames = showBuiltinDefault ? [DEFAULT_PRESET_NAME, ...presetNames] : presetNames;
  const presetValues = (n: string): GameSettings | undefined => {
    if (n === DEFAULT_PRESET_NAME && showBuiltinDefault && presetDefaults !== null) return presetDefaults;
    return presets[n];
  };
  const pickedPresetValues = presetPick ? presetValues(presetPick) : undefined;
  const presetMatch = ini ? allPresetNames.find((n) => {
    const v = presetValues(n);
    return v !== undefined && presetsEqual(v, ini);
  }) : undefined;
  const trimmedPresetName = presetName.trim();
  const overwriteTarget = trimmedPresetName
    ? (presetNames.find((n) => n.toLowerCase() === trimmedPresetName.toLowerCase())
      ?? (showBuiltinDefault && trimmedPresetName.toLowerCase() === DEFAULT_PRESET_NAME.toLowerCase()
        ? DEFAULT_PRESET_NAME : undefined))
    : undefined;

  return (
    <div>
      <Card
        variant="elevated"
        tone={hasVersions ? "primary" : "neutral"}
        radius={M3.shape.xl}
        style={{ marginBottom: M3.space.md }}
      >
        <div style={{ ...M3.type.headline, marginBottom: M3.space.sm }}>
          {hasVersions ? `Active ${state?.active_tag || "none"}` : "Welcome to HelixSR"}
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: M3.space.md }}>
          {activeVersion ? (
            activeVersion.network_built
              ? <Chip tone="success">Network built</Chip>
              : <Chip tone="warning">Network NOT built</Chip>
          ) : (
            <Chip tone="neutral">No active version</Chip>
          )}
          {games.length > 0 ? (
            <Chip tone="secondary">{installedCount}/{games.length} games{gamesTag ? ` · ${gamesTag}` : ""}</Chip>
          ) : (
            <Chip tone="neutral">Not scanned</Chip>
          )}
          {totalMB > 0 && <Chip tone="neutral">{totalMB.toFixed(1)} MB</Chip>}
        </div>
        <Stepper step={step} />
        <div style={{ ...M3.type.caption, color: M3.color.onSurfaceVariant, marginTop: M3.space.sm }}>
          Step {step}/4 — {STEP_TEXT[step]}
        </div>
        {step === 2 && activeVersion && !building && (
          <div style={{ marginTop: M3.space.sm }}>
            <MButton
              disabled={busy !== "" || downloading || scanning || migrating || updating}
              description="Compiles the shaders HelixSR needs — takes several minutes"
              variant="filled"
              onClick={() => doBuild(false)}
            >
              Build network now
            </MButton>
          </div>
        )}
        {step === 3 && games.length === 0 && !scanning && (
          <div style={{ marginTop: M3.space.sm }}>
            <MButton
              disabled={busy !== "" || downloading || building || migrating || updating}
              description="Finds DLSS / XeSS / FSR titles in your Steam libraries"
              variant="filled"
              onClick={onScan}
            >
              Scan games now
            </MButton>
          </div>
        )}
        {heroOp && (
          <div style={{ marginTop: M3.space.sm }}>
            {heroOp.pct === null
              ? <ProgressBar indeterminate nTransitionSec={M3.motion.bar} />
              : <ProgressBar nProgress={heroOp.pct} nTransitionSec={M3.motion.bar} />}
            <div style={{ ...M3.type.caption, marginTop: M3.space.xs, fontVariantNumeric: M3.tabular }}>{heroOp.text}</div>
          </div>
        )}
      </Card>
      {state && state.update_pending ? (
        <Card tone="primary" radius={M3.shape.md} style={{ marginBottom: M3.space.md }}>
          <div style={{ ...M3.type.title, marginBottom: 2 }}>Restart needed</div>
          <div style={M3.type.body}>v{state.update_pending} downloaded — restart Decky to apply it.</div>
        </Card>
      ) : state && state.plugin_version !== PLUGIN_VERSION ? (
        <Card tone="error" radius={M3.shape.md} style={{ marginBottom: M3.space.md }}>
          <div style={{ ...M3.type.title, marginBottom: 2 }}>Partial update</div>
          <div style={{ ...M3.type.body, marginBottom: M3.space.sm }}>
            UI v{PLUGIN_VERSION} but backend v{state.plugin_version || "unknown"}.
          </div>
          <MButton variant="tonal" disabled={busy !== ""} onClick={onReloadPlugin}>
            Reload plugin
          </MButton>
          <div style={{ ...M3.type.caption, color: M3.color.onSurfaceVariant, marginTop: M3.space.xs }}>
            If it still shows after reload, reopen this panel — then reinstall the zip and restart Decky.
          </div>
        </Card>
      ) : null}
      {output && (
        <PanelSection title="Log">
          <PanelSectionRow>
            <Card tone="neutral" radius={M3.shape.md} style={{ background: M3.color.surfaceContainerLow, width: "100%" }}>
              <PagedText text={output.slice(-4000)} pageSize={40} maxHeight={240} />
            </Card>
          </PanelSectionRow>
          <PanelSectionRow>
            <MButton variant="text" onClick={() => setOutput("")}>
              Clear log
            </MButton>
          </PanelSectionRow>
        </PanelSection>
      )}
      <PanelSection title="1 · Version">
        <>
        {updateAvailable && newestRelease && (
          <PanelSectionRow>
            <Card tone="tertiary" radius={M3.shape.md}>
              <div style={M3.type.label}>Update available: {newestRelease.tag} — select it below to download.</div>
            </Card>
          </PanelSectionRow>
        )}
        <PanelSectionRow>
          <div style={{ ...M3.type.caption, color: M3.color.onSurfaceVariant }}>
            {activeVersion ? `Active: ${activeVersion.tag} (network ${activeVersion.network_built ? "built" : "NOT built"}${activeVersion.layout === "modern" ? `, FG ${activeVersion.fg_built ? "built" : "not built"}` : ""})` : "No version downloaded yet"}
          </div>
        </PanelSectionRow>
        {listedReleases.map((r) => {
          const dl = (state?.versions || []).find((v) => v.tag === r.tag);
          return (
            <PanelSectionRow key={r.tag}>
              <MButton
                variant={picked === r.tag ? "filled" : "outlined"}
                description={dl ? `${dl.size_mb} MB on disk` : `${r.published_at} · ${fmtSize(r.size)} download`}
                onClick={() => setPicked(r.tag)}
              >
                {`${r.tag}${dl ? " ✓" : ""}`}
              </MButton>
            </PanelSectionRow>
          );
        })}
        {releases.length > listedReleases.length && (
          <PanelSectionRow>
            <MButton variant="text" onClick={() => setShowAllReleases(true)}>
              {`Show all ${releases.length} releases`}
            </MButton>
          </PanelSectionRow>
        )}
        {showAllReleases && releases.length > 3 && (
          <PanelSectionRow>
            <MButton variant="text" onClick={() => setShowAllReleases(false)}>
              Show fewer
            </MButton>
          </PanelSectionRow>
        )}
        {pickedRelease && !pickedDownloaded && pickedRelease.body && (
          <PanelSectionRow>
            <Card tone="neutral" radius={M3.shape.md}>
              <PagedText text={pickedRelease.body.slice(0, 800)} pageSize={12} maxHeight={160} />
            </Card>
          </PanelSectionRow>
        )}
        <PanelSectionRow>
          <MButton  disabled={busy !== "" || !releases.length} variant="tonal" onClick={() => refreshReleases()}>
            Reload releases
          </MButton>
        </PanelSectionRow>
        <PanelSectionRow>
          <ToggleField
            label="Include prereleases"
            description="Show prerelease builds (may be unstable)"
            checked={showPrereleases}
            onChange={(c: boolean) => onTogglePrereleases(c)}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <MButton
            
            disabled={busy !== "" || downloading || !picked}
            description={!releases.length ? "No releases loaded — Reload above" : undefined}
            variant="filled" onClick={onDownloadOrUse}
          >
            {pickedDownloaded ? `Switch to ${picked}` : downloading ? "Downloading ..." : `Download ${picked}`}
          </MButton>
        </PanelSectionRow>
        {downloading && (
          <PanelSectionRow>
            <div style={{ width: "100%", maxWidth: "100%", boxSizing: "border-box" }}>
              <ProgressBar nProgress={dl && dl.total > 0 ? Math.min(100, (100 * dl.downloaded) / dl.total) : 0} nTransitionSec={M3.motion.bar} />
              <div style={{ ...M3.type.caption, color: M3.color.onSurfaceVariant, marginTop: M3.space.xs, fontVariantNumeric: M3.tabular }}>
                {dl && dl.total > 0
                  ? `${fmtSize(dl.downloaded)} / ${fmtSize(dl.total)} (${Math.floor((100 * dl.downloaded) / dl.total)}%)${dlRate ? ` — ${dlRate}` : ""}`
                  : dl ? `${fmtSize(dl.downloaded)} downloaded ...${dlRate ? ` — ${dlRate}` : ""}` : "Starting download ..."}
              </div>
            </div>
          </PanelSectionRow>
        )}
        {downloading && (
          <PanelSectionRow>
            <MButton  variant="text" onClick={onCancelDownload}>
              Cancel download
            </MButton>
          </PanelSectionRow>
        )}
        {pickedDownloaded && (
          <PanelSectionRow>
            <MButton  disabled={busy !== ""} variant={confirm === `remove ${picked}` ? "danger" : "danger-outline"} onClick={onRemove}>
              {confirm === `remove ${picked}` ? `Confirm remove ${picked}?` : `Remove ${picked} files`}
            </MButton>
          </PanelSectionRow>
        )}
        </>
      </PanelSection>
      {hasVersions && (
      <PanelSection title="2 · Network build">
        <>
        <PanelSectionRow>
          <MButton
            
            disabled={busy !== "" || building || !activeVersion}
            description={!activeVersion ? "Download a version first" : undefined}
            variant={confirm === "rebuild network" ? "danger" : "filled"} onClick={() => onBuild(Boolean(activeVersion?.network_built))}
          >
            {building ? "Building network ..." : activeVersion?.network_built ? confirm === "rebuild network" ? "Confirm rebuild?" : "Rebuild network" : "Build network"}
          </MButton>
        </PanelSectionRow>
        <PanelSectionRow>
          <MButton  disabled={busy !== "" || building || !activeVersion} variant="tonal" onClick={onCheckBuild}>
            Check build readiness
          </MButton>
        </PanelSectionRow>
        {activeVersion?.layout === "modern" && (
        <PanelSectionRow>
          <ToggleField
            label="Frame generation (experimental)"
            description={activeVersion?.fg_available === false
              ? "No FG engine in this HelixSR build (withdrawn in 1.9.3+ — Scaler's providers cover FG)"
              : "Builds the FG engine for Scaler games; 2.0+ installs it with every Scaler (~400 MB extra downloads, longer build)"}
            checked={fgBuild}
            disabled={activeVersion?.fg_available === false}
            onChange={(c: boolean) => setFgBuild(c)}
          />
        </PanelSectionRow>
        )}
        {building && buildProg && (
          <PanelSectionRow>
            <div style={{ width: "100%", maxWidth: "100%", boxSizing: "border-box" }}>
              <ProgressBar nProgress={buildProg.pct} nTransitionSec={M3.motion.bar} />
              <div style={{ ...M3.type.caption, color: M3.color.onSurfaceVariant, marginTop: M3.space.xs, fontVariantNumeric: M3.tabular }}>{buildProg.pct}% — {buildProg.label}</div>
            </div>
          </PanelSectionRow>
        )}
        {building && (
          <PanelSectionRow>
            <MButton variant="text" onClick={onCancelBuild}>
              Cancel build
            </MButton>
          </PanelSectionRow>
        )}
        </>
      </PanelSection>
      )}
      {hasVersions && (
      <PanelSection title="3 · Games">
        <>
        <PanelSectionRow>
          <MButton
            
            disabled={scanning || busy !== "" || !activeVersion}
            description={!activeVersion ? "Download and build a version first" : busy !== "" ? "Busy — wait for the current task" : undefined}
            variant="filled" onClick={onScan}
          >
            {scanning ? "Scanning ..." : `Scan games${gamesTag ? ` (${gamesTag})` : ""}`}
          </MButton>
        </PanelSectionRow>
        {activeVersion?.generation === 3 && (
        <PanelSectionRow>
          <ToggleField
            label="Install frame generation with Scaler"
            description={activeVersion?.fg_built
              ? "2.0+ Scaler games get DLSS frame generation (turn it on in the game; change quality in Scaler settings)"
              : "No FG built — run the network build with frame generation first"}
            checked={fgWithScaler && !!activeVersion?.fg_built}
            disabled={!activeVersion?.fg_built}
            onChange={(c: boolean) => setFgWithScaler(c)}
          />
        </PanelSectionRow>
        )}
        {scanning && (
          <PanelSectionRow>
            <MButton  variant="text" onClick={onCancelScan}>
              Cancel scan
            </MButton>
          </PanelSectionRow>
        )}
        {scanning && (
          <PanelSectionRow>
            <div style={{ width: "100%", maxWidth: "100%", boxSizing: "border-box" }}>
              <ProgressBar indeterminate={true} />
              <div style={{ fontSize: 12, marginTop: 4 }}>Walking Steam libraries — this can take a few minutes on large collections ...</div>
            </div>
          </PanelSectionRow>
        )}
        {migCandidates.length > 0 && activeVersion && !migrating && (
          <PanelSectionRow>
            <MButton
              
              disabled={busy !== "" || scanning}
              description="Reinstalls them into the new build, keeping routes"
              variant="filled" onClick={onMigrate}
            >
              {confirm === `migrate to ${activeVersion.tag}` ? "Confirm migrate?" : `Migrate ${migCandidates.length} game(s) to ${activeVersion.tag}`}
            </MButton>
          </PanelSectionRow>
        )}
        {migrating && (
          <PanelSectionRow>
            <div style={{ width: "100%", maxWidth: "100%", boxSizing: "border-box" }}>
              <ProgressBar nProgress={mig && mig.total > 0 ? Math.min(100, (100 * mig.current) / mig.total) : 0} nTransitionSec={M3.motion.bar} />
              <div style={{ ...M3.type.caption, color: M3.color.onSurfaceVariant, marginTop: M3.space.xs, fontVariantNumeric: M3.tabular }}>
                {mig && mig.total > 0 ? `Migrating ${mig.current}/${mig.total}${mig.current_name ? `: ${mig.current_name}` : ""} ...` : "Starting migration ..."}
              </div>
            </div>
          </PanelSectionRow>
        )}
        {installAllTargets.length > 0 && (
          <PanelSectionRow>
            <MButton  disabled={busy !== "" || scanning} variant="filled" onClick={onInstallAll}>
              {`Install all ${installAllTargets.length} game(s)`}
            </MButton>
          </PanelSectionRow>
        )}
        {visInstalled > 0 && (
          <PanelSectionRow>
            <MButton  disabled={busy !== "" || scanning} variant={confirm === "remove all games" ? "danger" : "danger-outline"} onClick={onRemoveAll}>
              {confirm === "remove all games" ? "Confirm remove all?" : `Remove from all ${visInstalled} game(s)`}
            </MButton>
          </PanelSectionRow>
        )}
        {installedCount > 0 && (
          <PanelSectionRow>
            <MButton  disabled={busy !== "" || scanning} variant="tonal" onClick={onVerify}>
              Verify installs
            </MButton>
          </PanelSectionRow>
        )}
        {verify && verify.some((v) => !v.ok) && (
          <PanelSectionRow>
            <MButton
              
              disabled={busy !== ""}
              variant="filled" onClick={() => onRepair(verify.filter((v) => !v.ok).map((v) => v.index))}
            >
              {`Repair all ${verify.filter((v) => !v.ok).length} broken game(s)`}
            </MButton>
          </PanelSectionRow>
        )}
        {verify && verify.filter((v) => !v.ok).map((v) => (
          <PanelSectionRow key={`verify-${v.index}`}>
            <MButton
              
              disabled={busy !== ""}
              description={v.problems.join("; ")}
              variant="filled" onClick={() => onRepair([v.index])}
            >
              {`Repair ${v.name}`}
            </MButton>
          </PanelSectionRow>
        ))}
        {games.length > 0 && (
          <PanelSectionRow>
            <TextField
              label="Filter games"
              value={filter}
              bShowClearAction
              onChange={(e) => setFilter((e.target as HTMLInputElement).value)}
            />
          </PanelSectionRow>
        )}
        <PanelSectionRow>
          <div style={{ display: "flex", flexWrap: "wrap", gap: M3.space.sm }}>
            {([
              ["all", "All"],
              ["installed", "Installed"],
              ["available", "Available"],
              ["repair", "Repair"],
            ] as const).map(([mode, label]) => (
              <MButton
                key={mode}
                variant={showMode === mode ? "filled" : "outlined"}
                onClick={() => setShowMode(mode)}
                style={{ flex: "1 1 40%", minWidth: 0 }}
              >
                {label}
              </MButton>
            ))}
          </div>
        </PanelSectionRow>
        {showMode === "repair" && !verify && (
          <PanelSectionRow>
            <div style={{ ...M3.type.caption, color: M3.color.onSurfaceVariant }}>
              Run Verify installs first to find broken games.
            </div>
          </PanelSectionRow>
        )}
        <PanelSectionRow>
          <div style={{ ...M3.type.caption, color: M3.color.onSurfaceVariant }}>
            {games.length > 0
              ? `Showing ${({ all: "all games", installed: "installed only", available: "not installed only", repair: "needs repair" } as Record<string, string>)[showMode] || showMode}${filter.trim() ? ` · filter "${filter.trim()}"` : ""} — ${visibleGames.length}/${games.length}`
              : "Scan to find games"}
          </div>
        </PanelSectionRow>
        {sortedGames.map((g, si) => (
          <Fragment key={g.index}>
            {(si === 0 || sortedGames[si - 1].installed !== g.installed) && (
              <PanelSectionRow>
                <MButton
                  variant="text"
                  onClick={() => setOpenGroups({
                    ...openGroups,
                    [g.installed ? "installed" : "not"]: !(g.installed ? openInstalled : openNot),
                  })}
                >
                  {g.installed
                    ? `Installed (${installedGroup.length}) — ${openInstalled ? "collapse" : "expand"}`
                    : `Not installed (${availGroup.length}) — ${openNot ? "collapse" : "expand"}`}
                </MButton>
              </PanelSectionRow>
            )}
            {(g.installed ? openInstalled : openNot) && (
          <Card variant="outlined" style={{ width: "100%", marginBottom: M3.space.sm }}>
            <PanelSectionRow>
              <div style={{ display: "flex", alignItems: "center", gap: M3.space.sm, flexWrap: "wrap", width: "100%", boxSizing: "border-box", minWidth: 0 }}>
                <span style={{ ...M3.type.title, flex: 1, minWidth: 0, overflowWrap: "anywhere" }}>{g.name}</span>
                {g.installed
                  ? <Chip tone="success">Installed</Chip>
                  : <Chip tone="secondary">{ROUTE_SHORT[g.route || ""] || g.route || "—"}</Chip>}
              </div>
            </PanelSectionRow>
            <PanelSectionRow>
              <MButton
                
                disabled={busy !== "" || scanning || (!g.installed && !(g.fsr || []).length && activeLayout === "modern" && !((g.available || []).includes("scaler")))}
                description={g.installed ? shortRoute(g.route) : (ROUTE_LABELS[pickedRoute(g)] || g.status)}
                variant={confirm === `install scaler ${g.index}` ? "danger" : (g.installed ? "outlined" : "filled")} onClick={() => (g.installed ? onUninstall(g) : onInstall(g))}
              >
                {confirm === `install scaler ${g.index}` && !g.installed
                  ? `Tap again: install Scaler in ${g.name}`
                  : `${g.installed ? "Remove from" : "Install in"} ${g.name}`}
              </MButton>
            </PanelSectionRow>
            {!g.installed && g.available.length > 1 && (
              <PanelSectionRow>
                <div style={{ width: "100%" }}>
                  <div style={{ ...M3.type.caption, color: M3.color.onSurfaceVariant, marginBottom: M3.space.xs }}>
                    Install via:
                  </div>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: M3.space.sm }}>
                    {g.available.map((a) => (
                      <MButton
                        key={a}
                        variant={(routes[g.index] || g.route) === a ? "filled" : "outlined"}
                        onClick={() => setRoutes({ ...routes, [g.index]: a })}
                        style={{ flex: "1 1 40%", minWidth: 0 }}
                      >
                        {ROUTE_LABELS[a] || a}
                      </MButton>
                    ))}
                  </div>
                </div>
              </PanelSectionRow>
            )}
            <PanelSectionRow>
              <MButton variant="text" onClick={() => setExpanded({ ...expanded, [g.index]: !expanded[g.index] })}>
                {expanded[g.index] ? "Hide details" : "Details"}
              </MButton>
            </PanelSectionRow>
            {expanded[g.index] && (
              <PanelSectionRow>
                <div style={{ ...M3.type.caption, color: M3.color.onSurfaceVariant, overflowWrap: "anywhere" }}>
                  <div>Folder: {g.folder}</div>
                  <div>Detected: {g.inputs.length ? g.inputs.join(" / ") : "none"}</div>
                  <div>FSR: {(g.fsr || []).length ? g.fsr.map(base).join(", ") : "none"}</div>
                  <div>Exe: {g.exe ? base(g.exe) : "not found"}</div>
                  <div>Installed builds: {(g.installed_versions || []).length ? (g.installed_versions || []).join(", ") : "—"}</div>
                </div>
              </PanelSectionRow>
            )}
            {g.installed && g.route !== "scaler" && iniFor !== g.index && (
              <PanelSectionRow>
                <MButton  disabled={busy !== "" || scanning} variant="text" onClick={() => onOpenIni(g)}>
                  {`Settings — ${g.name}`}
                </MButton>
              </PanelSectionRow>
            )}
            {g.installed && g.route === "scaler" && scIniFor !== g.index && (
              <PanelSectionRow>
                <MButton  disabled={busy !== "" || scanning} variant="text" onClick={() => onOpenScIni(g)}>
                  {`Scaler settings — ${g.name}`}
                </MButton>
              </PanelSectionRow>
            )}
            {g.installed && g.route === "scaler" && !g.launch_options ? (
              <PanelSectionRow>
                <Card tone="neutral" radius={M3.shape.md} style={{ background: M3.color.surfaceContainerLow, width: "100%" }}>
                  <div style={{ ...M3.type.caption, color: M3.color.onSurfaceVariant }}>
                    No Steam launch options needed (dxgi.dll loads by itself).
                  </div>
                </Card>
              </PanelSectionRow>
            ) : null}
            {g.installed && g.launch_options ? (
              <PanelSectionRow>
                <Card tone="neutral" radius={M3.shape.md} style={{ background: M3.color.surfaceContainerLow, width: "100%" }}>
                  <div style={{ ...M3.type.caption, color: M3.color.onSurfaceVariant, marginBottom: M3.space.xs }}>
                    Steam launch options — Steam → game Properties → General → Launch Options, paste:
                  </div>
                  <div style={{ fontFamily: M3.mono, fontSize: 12, overflowWrap: "anywhere" }}>{g.launch_options}</div>
                  <div style={{ ...M3.type.caption, color: M3.color.onSurfaceVariant, marginTop: M3.space.xs }}>
                    Required for the Insert menu. Take them back out if you uninstall.
                  </div>
                  <MButton variant="tonal" onClick={() => onCopyLaunchOptions(g.launch_options || "")}>
                    Copy launch options
                  </MButton>
                </Card>
              </PanelSectionRow>
            ) : null}
            {g.installed && logFor !== g.index && (
              <PanelSectionRow>
                <MButton  disabled={busy !== "" || scanning} variant="text" onClick={() => onOpenLog(g)}>
                  {`Log — ${g.name}`}
                </MButton>
              </PanelSectionRow>
            )}
            {g.installed && logFor === g.index && (
              <div key={`log-${g.index}`}>
                {gameLogs.map((l, i) => (
                  <PanelSectionRow key={i}>
                    <Card tone="neutral" radius={M3.shape.md} style={{ background: M3.color.surfaceContainerLow, width: "100%" }}>
                      <div style={{ ...M3.type.caption, color: M3.color.onSurfaceVariant, marginBottom: M3.space.xs, overflowWrap: "anywhere" }}>{l.path}</div>
                      <PagedText text={l.tail.slice(-4000)} pageSize={30} maxHeight={200} />
                    </Card>
                  </PanelSectionRow>
                ))}
                {logFixes.map((f) => (
                  <PanelSectionRow key={f.id}>
                    <MButton  disabled={busy !== ""} description={f.label} variant="filled" onClick={() => onApplyFix(f.id)}>
                      Apply fix
                    </MButton>
                  </PanelSectionRow>
                ))}
                <PanelSectionRow>
                  <MButton  variant="text" onClick={() => { setLogFor(null); setGameLogs([]); setLogFixes([]); }}>
                    Close log
                  </MButton>
                </PanelSectionRow>
              </div>
            )}
            {g.installed && iniFor === g.index && ini && (
              <div>
                <PanelSectionRow>
                  <div style={{ fontSize: 12 }}>{iniPath}</div>
                </PanelSectionRow>
                <PanelSectionRow>
                  <div style={{ width: "100%" }}>
                    <div style={{ ...M3.type.caption, color: M3.color.onSurfaceVariant, marginBottom: M3.space.xs }}>
                      Sharpener — NVIDIA's filter, AMD FSR's RCAS, or off (as DLSS)
                    </div>
                    <div style={{ display: "flex", flexWrap: "wrap", gap: M3.space.sm }}>
                      {([
                        ["off", "Off"],
                        ["nvidia", "NVIDIA"],
                        ["rcas", "RCAS (AMD)"],
                      ] as const).map(([v, label]) => (
                        <MButton
                          key={v}
                          variant={ini.Sharpener === v ? "filled" : "outlined"}
                          onClick={() => setIni({ ...ini, Sharpener: v })}
                          style={{ flex: "1 1 40%", minWidth: 0 }}
                        >
                          {label}
                        </MButton>
                      ))}
                    </div>
                  </div>
                </PanelSectionRow>
                <PanelSectionRow>
                  <SliderField
                    label="Sharpness"
                    description="Used when a sharpener is on (0.5 is NVIDIA's default)"
                    value={parseFloat(ini.Sharpness)}
                    min={0}
                    max={1}
                    step={0.05}
                    showValue
                    disabled={ini.Sharpener === "off"}
                    onChange={(v: number) => setIni({ ...ini, Sharpness: String(Math.round(v * 100) / 100) })}
                  />
                </PanelSectionRow>
                {(iniSchema === "legacy" || iniSchema === "g3") && (
                <PanelSectionRow>
                  <ToggleField
                    label="Use game slider"
                    description="Follow the game's sharpness slider, if it has one"
                    checked={ini.UseGameSlider === "true"}
                    onChange={(c: boolean) => setIni({ ...ini, UseGameSlider: c ? "true" : "false" })}
                  />
                </PanelSectionRow>
                )}
                <PanelSectionRow>
                  <div style={{ width: "100%" }}>
                    <div style={{ ...M3.type.caption, color: M3.color.onSurfaceVariant, marginBottom: M3.space.xs }}>
                      {iniSchema === "modern"
                        ? "WaveSize — 64 for RX 7000 / 9000 if the log says wave32"
                        : "WaveSize — 64 for RX 7000 / 9000 in OptiScaler games if the log says wave32"}
                    </div>
                    <div style={{ display: "flex", flexWrap: "wrap", gap: M3.space.sm }}>
                      {((iniSchema === "modern"
                        ? [["auto", "Auto"], ["64", "64"], ["32", "32"]]
                        : iniSchema === "g3"
                          ? [["auto", "Auto"], ["default", "Default"], ["64", "64"], ["32", "32"]]
                          : [["default", "Default"], ["64", "64"], ["32", "32"]]) as [string, string][]).map(([v, label]) => (
                        <MButton
                          key={v}
                          variant={ini.WaveSize === v ? "filled" : "outlined"}
                          onClick={() => setIni({ ...ini, WaveSize: v })}
                          style={{ flex: "1 1 40%", minWidth: 0 }}
                        >
                          {label}
                        </MButton>
                      ))}
                    </div>
                  </div>
                </PanelSectionRow>
                {iniSchema === "g3" && (
                <PanelSectionRow>
                  <div style={{ width: "100%" }}>
                    <div style={{ ...M3.type.caption, color: M3.color.onSurfaceVariant, marginBottom: M3.space.xs }}>
                      Int8 — faster int8 math (auto decides; off if the picture shimmers)
                    </div>
                    <div style={{ display: "flex", flexWrap: "wrap", gap: M3.space.sm }}>
                      {([["auto", "Auto"], ["on", "On"], ["off", "Off"]] as [string, string][]).map(([v, label]) => (
                        <MButton
                          key={v}
                          variant={(ini.Int8 || "auto") === v ? "filled" : "outlined"}
                          onClick={() => setIni({ ...ini, Int8: v })}
                          style={{ flex: "1 1 40%", minWidth: 0 }}
                        >
                          {label}
                        </MButton>
                      ))}
                    </div>
                  </div>
                </PanelSectionRow>
                )}
                {iniSchema === "g3" && (
                <PanelSectionRow>
                  <div style={{ width: "100%" }}>
                    <div style={{ ...M3.type.caption, color: M3.color.onSurfaceVariant, marginBottom: M3.space.xs }}>
                      NetworkResolution — Ultra Performance (fast, default) vs the sharpest image (full)
                    </div>
                    <div style={{ display: "flex", flexWrap: "wrap", gap: M3.space.sm }}>
                      {([["fast", "Fast"], ["full", "Full"]] as [string, string][]).map(([v, label]) => (
                        <MButton
                          key={v}
                          variant={(ini.NetworkResolution || "fast") === v ? "filled" : "outlined"}
                          onClick={() => setIni({ ...ini, NetworkResolution: v })}
                          style={{ flex: "1 1 40%", minWidth: 0 }}
                        >
                          {label}
                        </MButton>
                      ))}
                    </div>
                  </div>
                </PanelSectionRow>
                )}
                {iniSchema === "g3" && (
                <PanelSectionRow>
                  <div style={{ width: "100%" }}>
                    <div style={{ ...M3.type.caption, color: M3.color.onSurfaceVariant, marginBottom: M3.space.xs }}>
                      DLSS frame generation (needs an FG build)
                    </div>
                    <div style={{ display: "flex", flexWrap: "wrap", gap: M3.space.sm }}>
                      {([["c", "Light"], ["full", "Full"]] as [string, string][]).map(([v, label]) => (
                        <MButton
                          key={v}
                          variant={(ini.FgPreset || "c") === v ? "filled" : "outlined"}
                          disabled={ini.FgEnabled === "false"}
                          onClick={() => setIni({ ...ini, FgPreset: v })}
                          style={{ flex: "1 1 40%", minWidth: 0 }}
                        >
                          {label}
                        </MButton>
                      ))}
                      <MButton
                        variant={ini.FgMemory === "system" ? "filled" : "outlined"}
                        disabled={ini.FgEnabled === "false"}
                        onClick={() => setIni({ ...ini, FgMemory: ini.FgMemory === "system" ? "auto" : "system" })}
                        style={{ flex: "1 1 40%", minWidth: 0 }}
                      >
                        {ini.FgMemory === "system" ? "Memory: system" : "Memory: auto"}
                      </MButton>
                      <MButton
                        variant={ini.FgEnabled === "false" ? "outlined" : "filled"}
                        onClick={() => setIni({ ...ini, FgEnabled: ini.FgEnabled === "false" ? "true" : "false" })}
                        style={{ flex: "1 1 40%", minWidth: 0 }}
                      >
                        {ini.FgEnabled === "false" ? "FG off" : "FG on"}
                      </MButton>
                    </div>
                  </div>
                </PanelSectionRow>
                )}
                {allPresetNames.length === 0 && (
                  <PanelSectionRow>
                    <div style={{ ...M3.type.caption, color: M3.color.onSurfaceVariant }}>
                      No presets yet — tune above, name it, save.
                    </div>
                  </PanelSectionRow>
                )}
                {allPresetNames.length > 0 && (
                  <PanelSectionRow>
                    <div style={{ width: "100%" }}>
                      <div style={{ ...M3.type.caption, color: M3.color.onSurfaceVariant, marginBottom: M3.space.xs }}>
                        Preset — apply a saved set of values
                      </div>
                      <div style={{ display: "flex", flexWrap: "wrap", gap: M3.space.sm }}>
                        {allPresetNames.map((n) => (
                          <MButton
                            key={n}
                            variant={presetPick === n ? "filled" : "outlined"}
                            onClick={() => setPresetPick(n)}
                            style={{ flex: "1 1 40%", minWidth: 0 }}
                          >
                            {`${n}${presetMatch === n ? " ✓" : ""}`}
                          </MButton>
                        ))}
                      </div>
                    </div>
                  </PanelSectionRow>
                )}
                {pickedPresetValues && (
                  <PanelSectionRow>
                    <div style={{ width: "100%" }}>
                      <div style={{ ...M3.type.caption, color: M3.color.onSurfaceVariant, marginBottom: M3.space.xs }}>
                        {formatPresetSummary(pickedPresetValues)}
                        {presetPick === DEFAULT_PRESET_NAME && showBuiltinDefault
                          ? " — built-in HelixSR defaults"
                          : presetMatch === presetPick ? " — matches current settings" : ""}
                      </div>
                      <MButton disabled={busy !== ""} variant="filled" onClick={onApplyPreset}>
                        {`Apply "${presetPick}"`}
                      </MButton>
                    </div>
                  </PanelSectionRow>
                )}
                {presetPick && presets[presetPick] && (
                  <PanelSectionRow>
                    <MButton disabled={busy !== ""} variant={confirm === `delete preset ${presetPick}` ? "danger" : "text"} onClick={onDeletePreset}>
                      {confirm === `delete preset ${presetPick}` ? `Tap again to delete "${presetPick}"` : `Delete "${presetPick}"`}
                    </MButton>
                  </PanelSectionRow>
                )}
                <PanelSectionRow>
                  <TextField
                    label="Preset name"
                    description={`Save the current editor values as a preset (${trimmedPresetName.length}/40)`}
                    value={presetName}
                    onChange={(e) => setPresetName((e.target as HTMLInputElement).value.slice(0, 40))}
                  />
                </PanelSectionRow>
                {overwriteTarget && (
                  <PanelSectionRow>
                    <div style={{ ...M3.type.caption, color: M3.color.onSurfaceVariant }}>
                      {`Will overwrite "${overwriteTarget}".`}
                    </div>
                  </PanelSectionRow>
                )}
                {trimmedPresetName !== "" && (
                <PanelSectionRow>
                  <MButton disabled={busy !== ""} variant="tonal" onClick={onSavePreset}>
                    {overwriteTarget ? `Overwrite "${overwriteTarget}"` : "Save preset"}
                  </MButton>
                </PanelSectionRow>
                )}
                <PanelSectionRow>
                  <MButton  disabled={busy !== ""} variant="filled" onClick={onSaveIni}>
                    Save settings
                  </MButton>
                </PanelSectionRow>
                <PanelSectionRow>
                  <MButton  variant="text" onClick={() => { setIniFor(null); setIni(null); setIniSchema("legacy"); setPresetPick(""); setPresetName(""); }}>
                    Close settings
                  </MButton>
                </PanelSectionRow>
              </div>
            )}
            {g.installed && g.route === "scaler" && scIniFor === g.index && scIni && (
              <div>
                <PanelSectionRow>
                  <div style={{ fontSize: 12 }}>{scIniPath}</div>
                </PanelSectionRow>
                {(scSchema === "g3"
                  ? (["Int8", "WaveSize", "FGPreset", "QuickFlow"] as const)
                  : (["Int8", "WaveSize", "FrameGeneration", "QuickFlow"] as const)
                ).map((key) => (
                  <PanelSectionRow key={key}>
                    <div style={{ width: "100%" }}>
                      <div style={{ ...M3.type.caption, color: M3.color.onSurfaceVariant, marginBottom: M3.space.xs }}>
                        {key} — {(scSchema === "g3" ? SCALER_SETTING_LABELS_G3 : SCALER_SETTING_LABELS)[key]}
                      </div>
                      <div style={{ display: "flex", flexWrap: "wrap", gap: M3.space.sm }}>
                        {((scSchema === "g3" ? SCALER_SETTING_OPTIONS_G3 : SCALER_SETTING_OPTIONS)[key]).map(([v, label]) => (
                          <MButton
                            key={v}
                            variant={scIni[key] === v ? "filled" : "outlined"}
                            onClick={() => setScIni({ ...scIni, [key]: v } as ScalerSettings)}
                            style={{ flex: "1 1 40%", minWidth: 0 }}
                          >
                            {label}
                          </MButton>
                        ))}
                      </div>
                    </div>
                  </PanelSectionRow>
                ))}
                <PanelSectionRow>
                  <MButton  disabled={busy !== ""} variant="filled" onClick={onSaveScIni}>
                    Save scaler settings
                  </MButton>
                </PanelSectionRow>
                <PanelSectionRow>
                  <MButton  variant="text" onClick={() => { setScIniFor(null); setScIni(null); }}>
                    Close scaler settings
                  </MButton>
                </PanelSectionRow>
              </div>
            )}
          </Card>
            )}
          </Fragment>
        ))}
        <PanelSectionRow>
          <TextField
            label="Extra game folder"
            description="A game outside Steam libraries (also: drag its folder onto a manual setup run)"
            value={folderInput}
            onChange={(e) => setFolderInput((e.target as HTMLInputElement).value)}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <MButton  disabled={busy !== "" || !folderInput.trim()} variant="tonal" onClick={onAddFolder}>
            Add folder
          </MButton>
        </PanelSectionRow>
        {(state?.extra_folders || []).map((f) => (
          <PanelSectionRow key={f}>
            <MButton  disabled={busy !== ""} variant="text" description={f} onClick={() => onRemoveFolder(f)}>
              Remove folder
            </MButton>
          </PanelSectionRow>
        ))}
        </>
      </PanelSection>
      )}
      <PanelSection title="HelixSR Scaler">
        <>
        {activeVersion?.generation === 3 ? (
        <PanelSectionRow>
          <div style={{ ...M3.type.caption, color: M3.color.onSurfaceVariant }}>
            Scaler is bundled with HelixSR 2.0+ — installing a game brings it (and frame generation) automatically.
            The download below is only for 1.9.x versions.
          </div>
        </PanelSectionRow>
        ) : (
        <PanelSectionRow>
          <div style={{ ...M3.type.caption, color: M3.color.onSurfaceVariant }}>
            For games with DLSS/XeSS/FSR2 but no FSR 3.1: OptiScaler with HelixSR built in (Insert for the menu).
            Not for online games or games with anti-cheat. Needs a built HelixSR 1.9.0+ and manual Steam launch
            options per game. About 132 MB to download, plus copies in each game.
          </div>
        </PanelSectionRow>
        )}
        {activeVersion?.generation !== 3 && (
        <PanelSectionRow>
          <div style={{ ...M3.type.caption, color: M3.color.onSurfaceVariant }}>
            {scalerState?.tag ? `Downloaded: ${scalerState.tag} (${scalerState.size_mb} MB on disk)` : "Not downloaded yet"}
          </div>
        </PanelSectionRow>
        )}
        {scalerReleases.map((r) => (
          <PanelSectionRow key={r.tag}>
            <MButton
              variant={scalerPicked === r.tag ? "filled" : "outlined"}
              description={`${r.published_at} · ${fmtSize(r.size)} download`}
              onClick={() => setScalerPicked(r.tag)}
            >
              {`${r.tag}${scalerState?.tag === r.tag ? " ✓" : ""}`}
            </MButton>
          </PanelSectionRow>
        ))}
        {scalerPickedRelease && scalerPickedRelease.body && (
          <PanelSectionRow>
            <Card tone="neutral" radius={M3.shape.md}>
              <PagedText text={scalerPickedRelease.body.slice(0, 800)} pageSize={12} maxHeight={160} />
            </Card>
          </PanelSectionRow>
        )}
        <PanelSectionRow>
          <MButton  disabled={busy !== "" || scalerDownloading || !scalerPicked} variant="tonal" onClick={() => refreshScalerReleases()}>
            Reload scaler releases
          </MButton>
        </PanelSectionRow>
        <PanelSectionRow>
          <ToggleField
            label="Include prereleases"
            description="Show prerelease builds (may be unstable)"
            checked={showPrereleases}
            onChange={(c: boolean) => onTogglePrereleases(c)}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <MButton
            disabled={busy !== "" || scalerDownloading || !scalerPicked}
            variant="filled" onClick={onScalerDownload}
          >
            {scalerDownloading ? "Downloading Scaler ..." : `Download ${scalerPicked || "Scaler"}`}
          </MButton>
        </PanelSectionRow>
        {scalerDownloading && (
          <PanelSectionRow>
            <div style={{ width: "100%", maxWidth: "100%", boxSizing: "border-box" }}>
              <ProgressBar nProgress={scDl && scDl.total > 0 ? Math.min(100, (100 * scDl.downloaded) / scDl.total) : 0} nTransitionSec={M3.motion.bar} />
              <div style={{ ...M3.type.caption, color: M3.color.onSurfaceVariant, marginTop: M3.space.xs, fontVariantNumeric: M3.tabular }}>
                {scDl && scDl.total > 0
                  ? `${fmtSize(scDl.downloaded)} / ${fmtSize(scDl.total)} (${Math.floor((100 * scDl.downloaded) / scDl.total)}%)${scDlRate ? ` — ${scDlRate}` : ""}`
                  : scDl ? `${fmtSize(scDl.downloaded)} downloaded ...${scDlRate ? ` — ${scDlRate}` : ""}` : "Starting scaler download ..."}
              </div>
            </div>
          </PanelSectionRow>
        )}
        {scalerDownloading && (
          <PanelSectionRow>
            <MButton  variant="text" onClick={onCancelScalerDownload}>
              Cancel scaler download
            </MButton>
          </PanelSectionRow>
        )}
        </>
      </PanelSection>
      <PanelSection title="System">
        <>
        {hasVersions && (
        <>
        <PanelSectionRow>
          <div style={{ ...M3.type.title }}>Total {totalMB.toFixed(1)} MB in {downloadedVersions.length} version(s)</div>
        </PanelSectionRow>
        {downloadedVersions.map((v) => (
          <PanelSectionRow key={v.tag}>
            <div style={{ ...M3.type.body, color: M3.color.onSurfaceVariant }}>{v.tag} — {v.size_mb} MB on disk{v.tag === state?.active_tag ? " (active)" : ""}</div>
          </PanelSectionRow>
        ))}
        <PanelSectionRow>
          <MButton  disabled={busy !== "" || downloadedVersions.length < 2} variant={confirm === "remove old versions" ? "danger" : "danger-outline"} onClick={onRemoveOld}>
            {confirm === "remove old versions" ? "Confirm remove all?" : "Remove all except active"}
          </MButton>
        </PanelSectionRow>
        <PanelSectionRow>
          <MButton  disabled={busy !== ""} variant={confirm === "clear SDK cache" ? "danger" : "danger-outline"} onClick={onClearDlss}>
            {confirm === "clear SDK cache" ? "Confirm clear cache?" : "Clear cached SDK files"}
          </MButton>
        </PanelSectionRow>
        <PanelSectionRow>
          <MButton  disabled={busy !== ""} variant={confirm === "reset everything" ? "danger" : "danger-outline"} onClick={onResetAll}>
            {confirm === "reset everything" ? "Confirm reset all?" : "Reset everything"}
          </MButton>
        </PanelSectionRow>
        </>
        )}
        <PanelSectionRow>
          <MButton  disabled={busy !== ""} variant="tonal" onClick={onHealth}>
            Run health check
          </MButton>
        </PanelSectionRow>
        {(health || []).map((c) => (
          <PanelSectionRow key={c.name}>
            <div style={{ display: "flex", gap: M3.space.sm, alignItems: "baseline", ...M3.type.body }}>
              <Chip tone={c.ok ? "success" : "error"}>{c.ok ? "OK" : "FAIL"}</Chip>
              <span style={{ overflowWrap: "anywhere" }}>{c.name} — {c.detail}</span>
            </div>
          </PanelSectionRow>
        ))}
        </>
      </PanelSection>
      <PanelSection title="Plugin update">
        <>
        {puCheck?.update_available && !updating && !(pu?.done && pu?.ok) && (
          <PanelSectionRow>
            <div style={{ fontSize: 12 }}>v{puCheck.latest} available (you run v{puCheck.current}).</div>
          </PanelSectionRow>
        )}
        {puCheck?.update_available && puCheck?.body && !updating && !(pu?.done && pu?.ok) && (
          <PanelSectionRow>
            <PagedText text={puCheck.body.slice(0, 800)} pageSize={12} maxHeight={160} />
          </PanelSectionRow>
        )}
        {puCheck?.update_available && !updating && !(pu?.done && pu?.ok) && (
          <PanelSectionRow>
            <MButton  disabled={busy !== ""} variant="filled" onClick={onStartPluginUpdate}>
              {`Download & install v${puCheck.latest}`}
            </MButton>
          </PanelSectionRow>
        )}
        {puCheck?.update_available && puCheck.can_install === false && !updating && !(pu?.done && pu?.ok) && (
          <PanelSectionRow>
            <div style={{ ...M3.type.caption, color: M3.color.onSurfaceVariant }}>
              Decky runs this plugin without write access to its own folder, so the update
              is downloaded for Decky's manual Install-from-ZIP flow instead.
            </div>
          </PanelSectionRow>
        )}
        {puCheck && !puCheck.update_available && !updating && (
          <PanelSectionRow>
            <div style={{ ...M3.type.body, color: M3.color.onSurfaceVariant }}>
              {puCheck.ok
                ? `Plugin v${puCheck.current || PLUGIN_VERSION} — up to date.`
                : `Update check failed${puCheck.error ? `: ${puCheck.error}` : ""}.`}
            </div>
          </PanelSectionRow>
        )}
        {updating && (
          <PanelSectionRow>
            <div style={{ width: "100%", maxWidth: "100%", boxSizing: "border-box" }}>
              <ProgressBar nProgress={pu && pu.total > 0 ? Math.min(100, (100 * pu.downloaded) / pu.total) : 0} nTransitionSec={M3.motion.bar} />
              <div style={{ ...M3.type.caption, color: M3.color.onSurfaceVariant, marginTop: M3.space.xs, fontVariantNumeric: M3.tabular }}>
                {pu && pu.total > 0
                  ? `${fmtSize(pu.downloaded)} / ${fmtSize(pu.total)} (${Math.floor((100 * pu.downloaded) / pu.total)}%)${puRate ? ` — ${puRate}` : ""}`
                  : "Starting update ..."}
              </div>
            </div>
          </PanelSectionRow>
        )}
        {updating && pu?.running && (
          <PanelSectionRow>
            <MButton  variant="text" onClick={async () => {
              try {
                const r = await apiCancelPluginUpdate();
                if (!r.ok) setOutput(r.error || "update not cancelled");
              } catch (e) {
                console.error("[helixsr] cancel_plugin_update failed", e);
              }
            }}>
              Cancel update
            </MButton>
          </PanelSectionRow>
        )}
        {!updating && (
          <PanelSectionRow>
            <MButton  disabled={busy !== "" || checkingUpdate} variant="tonal" onClick={() => onCheckPluginUpdate(true)}>
              {checkingUpdate ? "Checking ..." : puCheck ? "Check again" : "Check for updates"}
            </MButton>
          </PanelSectionRow>
        )}
        </>
      </PanelSection>
      {state?.plugin_version && (
        <PanelSectionRow>
          <div style={{ ...M3.type.caption, color: M3.color.onSurfaceVariant }}>plugin v{state.plugin_version}</div>
        </PanelSectionRow>
      )}
    </div>
  );
};

export default definePlugin(() => {
  console.log("HelixSR initializing, this is called once on frontend startup")

  const listener = addEventListener<[ok: boolean, rc: number]>("helixsr_build_done", (ok, rc) => {
    console.log("HelixSR build done:", ok, rc)
    if (!panelOpen) {
      toaster.toast({ title: ok ? "HelixSR build done" : "HelixSR build failed", body: `rc=${rc}` });
    }
  });

  return {
    // The name shown in various decky menus
    name: "HelixSR Muse Plugin",
    // The element displayed at the top of your plugin's menu
    titleView: <div className={staticClasses.Title}>HelixSR</div>,
    // The content of your plugin's menu
    content: <Content />,
    // The icon displayed in the plugin list
    icon: <MdMemory />,
    // The function triggered when your plugin unloads
    onDismount() {
      console.log("Unloading HelixSR")
      removeEventListener("helixsr_build_done", listener);
    },
  };
});
