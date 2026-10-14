import {
  ButtonItem,
  DropdownItem,
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
import { Card, Chip, Stepper } from "./components";
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
  ScanCache,
  ScanStatus,
  VerifyResult,
  apiAddScanFolder,
  apiApplyIniPreset,
  apiApplyLogFix,
  apiCancelDownload,
  apiCancelScan,
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
  apiStartBuild,
  apiStartDownload,
  apiStartMigration,
  apiStartPluginUpdate,
  apiStartScan,
  apiUninstallGames,
  apiVerifyInstalls,
} from "./services/api";
import { PLUGIN_VERSION } from "./version";

function fmtSize(n: number) {
  return `${(n / 1e6).toFixed(1)} MB`;
}

const base = (p: string) => p.split(/[/\\]/).pop() || p;

function fmtAge(epochSec: number) {
  if (!epochSec) return "some time ago";
  const s = Math.max(0, Date.now() / 1000 - epochSec);
  if (s < 90) return "just now";
  const m = Math.floor(s / 60);
  if (m < 90) return `${m} min ago`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h} h ago`;
  return `${Math.floor(h / 24)} d ago`;
}

const ROUTE_LABELS: Record<string, string> = {
  direct: "FSR drop-in (select AMD FSR)",
  optiscaler: "OptiScaler (select DLSS)",
  dlss: "DLSS loader (experimental!)",
};

const ROUTE_SHORT: Record<string, string> = {
  direct: "FSR drop-in",
  optiscaler: "OptiScaler",
  dlss: "DLSS loader",
};

function parseBuildProgress(log: string): { pct: number; label: string } | null {
  // Matches the reference setup's bar: [####----]  42%  3:12  shaders 15/40
  const re = /\[([#-]{20})\]\s*(\d+)%\s*(\d+:\d+)\s*(.*)/g;
  let m: RegExpExecArray | null = null;
  let last: RegExpExecArray | null = null;
  while ((m = re.exec(log)) !== null) last = m;
  if (!last) return null;
  return { pct: Math.min(100, parseInt(last[2], 10)), label: `${last[3]} — ${last[4]}` };
}

function shortRoute(route: string): string {
  if (route === "direct") return "drop-in, pick AMD FSR in-game";
  if (route === "optiscaler") return "OptiScaler, pick DLSS in-game";
  if (route === "dlss") return "DLSS loader, pick DLSS in-game";
  return route;
}

function cmpTags(a: string, b: string): number {
  // Numeric dotted comparison ignoring a leading "v": v1.9.0 > v1.8.1.
  const pa = a.replace(/^v/i, "").split(".").map((x) => parseInt(x, 10) || 0);
  const pb = b.replace(/^v/i, "").split(".").map((x) => parseInt(x, 10) || 0);
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
    if ((pa[i] || 0) !== (pb[i] || 0)) return (pa[i] || 0) < (pb[i] || 0) ? -1 : 1;
  }
  return 0;
}

// True while the panel is open; the plugin-scope build listener below only
// toasts when it isn't, so completions are announced exactly once.
let panelOpen = false;

function Content() {
  const [releases, setReleases] = useState<HelixRelease[]>([]);
  const [state, setState] = useState<HelixState | null>(null);
  const [picked, setPicked] = useState<string>("");
  const [games, setGames] = useState<HelixGame[]>([]);
  const [gamesTag, setGamesTag] = useState<string>("");
  const [busy, setBusy] = useState<string>("");
  const [output, setOutput] = useState<string>("");
  const [building, setBuilding] = useState<boolean>(false);
  const [downloading, setDownloading] = useState<boolean>(false);
  const [dl, setDl] = useState<DownloadStatus | null>(null);
  const [dlRate, setDlRate] = useState<string>("");
  const dlPrev = useRef<{ t: number; bytes: number } | null>(null);
  const [scanning, setScanning] = useState<boolean>(false);
  const [routes, setRoutes] = useState<Record<number, string>>({});
  const [iniFor, setIniFor] = useState<number | null>(null);
  const [ini, setIni] = useState<GameSettings | null>(null);
  const [iniPath, setIniPath] = useState<string>("");
  const [presets, setPresets] = useState<Record<string, GameSettings>>({});
  const [presetPick, setPresetPick] = useState<string>("");
  const [presetName, setPresetName] = useState<string>("");
  const [logFor, setLogFor] = useState<number | null>(null);
  const [gameLogs, setGameLogs] = useState<GameLog[]>([]);
  const [logFixes, setLogFixes] = useState<LogFix[]>([]);
  const [verify, setVerify] = useState<VerifyResult[] | null>(null);
  const [filter, setFilter] = useState<string>("");
  const [expanded, setExpanded] = useState<Record<number, boolean>>({});
  const [showMode, setShowMode] = useState<string>("all");
  const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({ installed: true, not: false });
  const [health, setHealth] = useState<HealthCheck[] | null>(null);
  const [migrating, setMigrating] = useState<boolean>(false);
  const [mig, setMig] = useState<MigrationStatus | null>(null);
  const [folderInput, setFolderInput] = useState<string>("");
  const [puCheck, setPuCheck] = useState<PluginUpdateInfo | null>(null);
  const [checkingUpdate, setCheckingUpdate] = useState<boolean>(false);
  const [updating, setUpdating] = useState<boolean>(false);
  const [pu, setPu] = useState<PluginUpdateStatus | null>(null);
  const [confirm, setConfirm] = useState<string>("");
  const confirmTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const logRef = useRef<HTMLDivElement | null>(null);

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

  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [output]);

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
      const relsRes = await apiGetReleases();
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
          const [dl0, sc0, mg0, pu0] = await Promise.all([
            apiGetDownloadStatus(), apiGetScanStatus(), apiGetMigrationStatus(),
            apiGetPluginUpdateStatus()]);
          if (dl0.running) { setDl(dl0); setDownloading(true); }
          if (sc0.running) setScanning(true);
          if (mg0.running) { setMig(mg0); setMigrating(true); }
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
        if (st.done) {
          setUpdating(false);
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
    if (!picked) return;
    if (pickedDownloaded) {
      setBusy("version");
      try {
        setState(await apiSetActiveVersion(picked));
        setPicked(picked);
        setGames([]);
        setGamesTag("");
      } finally {
        setBusy("");
        refreshState();
      }
      return;
    }
    if (!pickedRelease) return;
    setDl(null);
    setDlRate("");
    dlPrev.current = null;
    try {
      const r = await apiStartDownload(picked, pickedRelease.zip_url);
      if (!r.ok) {
        toaster.toast({ title: "Download not started", body: r.error || "" });
        setOutput(r.error || "download not started");
        return;
      }
      setDownloading(true);
      setOutput(`Downloading ${pickedRelease.zip_name} (${fmtSize(pickedRelease.size)}) ...`);
    } catch (e) {
      console.error("[helixsr] start_download failed", e);
    }
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
    const c = await apiCheckBuildReady(activeVersion?.tag || null);
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
      const r = await apiStartBuild(rebuild);
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

  const onOpenIni = async (g: HelixGame) => {
    setBusy(`ini-${g.index}`);
    try {
      const r = await apiGetGameSettings(g.index);
      if (!r.ok || !r.values) {
        setOutput(r.error || "cannot read settings");
        return;
      }
      setIni(r.values);
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
      if (r.ok) setPresets(r.presets || {});
    } catch (e) {
      console.error("[helixsr] get_ini_presets failed", e);
    }
  };

  const onSavePreset = async () => {
    if (!presetName.trim()) return;
    setBusy("preset");
    try {
      const r = await apiSaveIniPreset(presetName.trim(), {
        Sharpener: "off", Sharpness: "0.5", UseGameSlider: "false", WaveSize: "default",
        ...(ini || {}),
      });
      if (!r.ok) {
        setOutput(r.error || "cannot save preset");
        return;
      }
      setPresets(r.presets || {});
      setPresetName("");
      setOutput(`Preset "${presetName.trim()}" saved.`);
    } finally {
      setBusy("");
    }
  };

  const onDeletePreset = async () => {
    if (!presetPick) return;
    setBusy("preset");
    try {
      const r = await apiDeleteIniPreset(presetPick);
      if (r.ok) setPresets(r.presets || {});
      setPresetPick("");
    } finally {
      setBusy("");
    }
  };

  const onApplyPreset = async () => {
    if (iniFor === null || !presetPick) return;
    setBusy("preset");
    try {
      const r = await apiApplyIniPreset(iniFor, presetPick);
      setOutput(r.output || "");
      toaster.toast({ title: r.ok ? `Preset "${presetPick}" applied` : "Apply failed", body: r.ok ? "" : (r.output || "").slice(-200) });
      if (r.ok) {
        const g = games.find((x) => x.index === iniFor);
        if (g) onOpenIni(g);
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
    const targets = visibleGames.filter((g) => !g.installed);
    if (!targets.length) return;
    setBusy("games-all");
    try {
      const r = await apiInstallGames(targets.map((g) => g.index), "");
      setOutput(r.output);
      toaster.toast({ title: r.ok ? `Installed in ${targets.length} game(s)` : "Install failed", body: r.ok ? "" : r.output.slice(-200) });
      if (r.ok) {
        setGames((gs) => gs.map((x) => (x.installed ? x : { ...x, installed: true, status: "HelixSR installed" })));
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
          if (r.ok) onScan();
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

  const onStartPluginUpdate = async () => {
    try {
      const r = await apiStartPluginUpdate();
      if (!r.ok) {
        setOutput(r.error || "update not started");
        toaster.toast({ title: "Update not started", body: r.error || "" });
        return;
      }
      setUpdating(true);
      setPu(null);
      setOutput(`Downloading plugin update v${r.version} ...`);
    } catch (e) {
      console.error("[helixsr] start_plugin_update failed", e);
    }
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
    setBusy(`game-${g.index}`);
    try {
      const route = routes[g.index] || g.route;
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
      if (r.ok) onScan();
    } finally {
      setBusy("");
    }
  };

  const options = releases.map((r) => {
    const dl = (state?.versions || []).find((v) => v.tag === r.tag);
    return {
      data: r.tag,
      label: dl ? `${r.tag} ✓ (${dl.size_mb} MB on disk)` : `${r.tag} — ${r.published_at} — ${fmtSize(r.size)} download`,
    };
  });
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
  const installedCount = games.filter((g) => g.installed).length;
  const visNotInstalled = visibleGames.filter((g) => !g.installed).length;
  const visInstalled = visibleGames.filter((g) => g.installed).length;
  const downloadedVersions = (state?.versions || []).filter((v) => downloadedTags.has(v.tag));
  const totalMB = downloadedVersions.reduce((a, v) => a + (v.size_mb || 0), 0);
  const buildProg = building ? parseBuildProgress(output) : null;
  const newestRelease = releases.length ? releases[0] : null;
  const updateAvailable = newestRelease && ![...downloadedTags].some((t) => cmpTags(t, newestRelease.tag) >= 0);
  const step = !state || !state.versions.length ? 1 : !activeVersion?.network_built ? 2 : !games.length ? 3 : 4;
  const STEP_TEXT = ["", "Download a HelixSR release", "Build the network", "Scan your games", "Install into games"];
  const migCandidates = activeVersion && activeVersion.dll_version && activeVersion.dll_version !== "?"
    ? games.filter((g) => g.installed && !(g.installed_versions || []).includes(activeVersion.dll_version))
    : [];

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
      </Card>
      {state && state.update_pending ? (
        <Card tone="primary" radius={M3.shape.md} style={{ marginBottom: M3.space.md }}>
          <div style={{ ...M3.type.title, marginBottom: 2 }}>Restart needed</div>
          <div style={M3.type.body}>v{state.update_pending} downloaded — restart Decky to apply it.</div>
        </Card>
      ) : state && state.plugin_version !== PLUGIN_VERSION ? (
        <Card tone="error" radius={M3.shape.md} style={{ marginBottom: M3.space.md }}>
          <div style={{ ...M3.type.title, marginBottom: 2 }}>Partial update</div>
          <div style={M3.type.body}>
            UI v{PLUGIN_VERSION} but backend v{state.plugin_version || "unknown"}.
            Reinstall the plugin zip and restart Decky.
          </div>
        </Card>
      ) : null}
      <PanelSection title="1 · Version">
        {updateAvailable && newestRelease && (
          <PanelSectionRow>
            <Card tone="tertiary" radius={M3.shape.md}>
              <div style={M3.type.label}>Update available: {newestRelease.tag} — select it below to download.</div>
            </Card>
          </PanelSectionRow>
        )}
        <PanelSectionRow>
          <DropdownItem
            label="HelixSR release"
            description={activeVersion ? `Active: ${activeVersion.tag} (network ${activeVersion.network_built ? "built" : "NOT built"})` : "No version downloaded yet"}
            rgOptions={options}
            selectedOption={picked}
            onChange={(o) => setPicked(String(o.data))}
          />
        </PanelSectionRow>
        {pickedRelease && !pickedDownloaded && pickedRelease.body && (
          <PanelSectionRow>
            <Card tone="neutral" radius={M3.shape.md}>
              <div style={{ ...M3.type.body, whiteSpace: "pre-wrap", maxHeight: 160, overflowY: "auto" }}>{pickedRelease.body.slice(0, 800)}</div>
            </Card>
          </PanelSectionRow>
        )}
        <PanelSectionRow>
          <ButtonItem layout="below" disabled={busy !== "" || !releases.length} onClick={() => refreshReleases()}>
            Reload releases
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            disabled={busy !== "" || downloading || !picked}
            description={!releases.length ? "No releases loaded — Reload above" : undefined}
            onClick={onDownloadOrUse}
          >
            {pickedDownloaded ? `Switch to ${picked}` : downloading ? "Downloading ..." : `Download ${picked}`}
          </ButtonItem>
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
            <ButtonItem layout="below" onClick={onCancelDownload}>
              Cancel download
            </ButtonItem>
          </PanelSectionRow>
        )}
        {pickedDownloaded && (
          <PanelSectionRow>
            <ButtonItem layout="below" disabled={busy !== ""} onClick={onRemove}>
              {confirm === `remove ${picked}` ? `Confirm remove ${picked}?` : `Remove ${picked} files`}
            </ButtonItem>
          </PanelSectionRow>
        )}
      </PanelSection>
      {hasVersions && (
      <PanelSection title="2 · Network build">
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            disabled={busy !== "" || building || !activeVersion}
            description={!activeVersion ? "Download a version first" : undefined}
            onClick={() => onBuild(Boolean(activeVersion?.network_built))}
          >
            {building ? "Building network ..." : activeVersion?.network_built ? confirm === "rebuild network" ? "Confirm rebuild?" : "Rebuild network" : "Build network"}
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" disabled={busy !== "" || building || !activeVersion} onClick={onCheckBuild}>
            Check build readiness
          </ButtonItem>
        </PanelSectionRow>
        {building && buildProg && (
          <PanelSectionRow>
            <div style={{ width: "100%", maxWidth: "100%", boxSizing: "border-box" }}>
              <ProgressBar nProgress={buildProg.pct} nTransitionSec={M3.motion.bar} />
              <div style={{ ...M3.type.caption, color: M3.color.onSurfaceVariant, marginTop: M3.space.xs, fontVariantNumeric: M3.tabular }}>{buildProg.pct}% — {buildProg.label}</div>
            </div>
          </PanelSectionRow>
        )}
      </PanelSection>
      )}
      {hasVersions && (
      <PanelSection title="3 · Games">
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            disabled={scanning || busy !== "" || !activeVersion}
            description={!activeVersion ? "Download and build a version first" : busy !== "" ? "Busy — wait for the current task" : undefined}
            onClick={onScan}
          >
            {scanning ? "Scanning ..." : `Scan games${gamesTag ? ` (${gamesTag})` : ""}`}
          </ButtonItem>
        </PanelSectionRow>
        {scanning && (
          <PanelSectionRow>
            <ButtonItem layout="below" onClick={onCancelScan}>
              Cancel scan
            </ButtonItem>
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
            <ButtonItem
              layout="below"
              disabled={busy !== "" || scanning}
              description="Reinstalls them into the new build, keeping routes"
              onClick={onMigrate}
            >
              {confirm === `migrate to ${activeVersion.tag}` ? "Confirm migrate?" : `Migrate ${migCandidates.length} game(s) to ${activeVersion.tag}`}
            </ButtonItem>
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
        {visNotInstalled > 0 && (
          <PanelSectionRow>
            <ButtonItem layout="below" disabled={busy !== "" || scanning} onClick={onInstallAll}>
              {`Install all ${visNotInstalled} game(s)`}
            </ButtonItem>
          </PanelSectionRow>
        )}
        {visInstalled > 0 && (
          <PanelSectionRow>
            <ButtonItem layout="below" disabled={busy !== "" || scanning} onClick={onRemoveAll}>
              {confirm === "remove all games" ? "Confirm remove all?" : `Remove from all ${visInstalled} game(s)`}
            </ButtonItem>
          </PanelSectionRow>
        )}
        {installedCount > 0 && (
          <PanelSectionRow>
            <ButtonItem layout="below" disabled={busy !== "" || scanning} onClick={onVerify}>
              Verify installs
            </ButtonItem>
          </PanelSectionRow>
        )}
        {verify && verify.some((v) => !v.ok) && (
          <PanelSectionRow>
            <ButtonItem
              layout="below"
              disabled={busy !== ""}
              onClick={() => onRepair(verify.filter((v) => !v.ok).map((v) => v.index))}
            >
              {`Repair all ${verify.filter((v) => !v.ok).length} broken game(s)`}
            </ButtonItem>
          </PanelSectionRow>
        )}
        {verify && verify.filter((v) => !v.ok).map((v) => (
          <PanelSectionRow key={`verify-${v.index}`}>
            <ButtonItem
              layout="below"
              disabled={busy !== ""}
              description={v.problems.join("; ")}
              onClick={() => onRepair([v.index])}
            >
              {`Repair ${v.name}`}
            </ButtonItem>
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
        {filter.trim() && (
          <PanelSectionRow>
            <div style={{ ...M3.type.caption, color: M3.color.onSurfaceVariant }}>
              {visibleGames.length}/{games.length} shown
            </div>
          </PanelSectionRow>
        )}
        <PanelSectionRow>
          <DropdownItem
            label="Show"
            rgOptions={[
              { data: "all", label: "All games" },
              { data: "installed", label: "Installed only" },
              { data: "available", label: "Not installed only" },
              { data: "repair", label: "Needs repair" },
            ]}
            selectedOption={showMode}
            onChange={(o) => setShowMode(String(o.data))}
          />
        </PanelSectionRow>
        {showMode === "repair" && !verify && (
          <PanelSectionRow>
            <div style={{ ...M3.type.caption, color: M3.color.onSurfaceVariant }}>
              Run Verify installs first to find broken games.
            </div>
          </PanelSectionRow>
        )}
        {sortedGames.map((g, si) => (
          <Fragment key={g.index}>
            {(si === 0 || sortedGames[si - 1].installed !== g.installed) && (
              <PanelSectionRow>
                <ButtonItem
                  layout="below"
                  onClick={() => setOpenGroups({
                    ...openGroups,
                    [g.installed ? "installed" : "not"]: !(g.installed ? openGroups.installed : openGroups.not),
                  })}
                >
                  {g.installed
                    ? `Installed (${installedGroup.length}) — ${openGroups.installed ? "collapse" : "expand"}`
                    : `Not installed (${availGroup.length}) — ${openGroups.not ? "collapse" : "expand"}`}
                </ButtonItem>
              </PanelSectionRow>
            )}
            {(g.installed ? openGroups.installed : openGroups.not) && (
          <Card variant="outlined" style={{ width: "100%", marginBottom: M3.space.sm }}>
            <PanelSectionRow>
              <div style={{ display: "flex", alignItems: "center", gap: M3.space.sm, flexWrap: "wrap", width: "100%", boxSizing: "border-box", minWidth: 0 }}>
                <span style={{ ...M3.type.title, flex: 1, minWidth: 0, overflowWrap: "anywhere" }}>{g.name}</span>
                {g.installed
                  ? <Chip tone="success">Installed</Chip>
                  : <Chip tone="secondary">{ROUTE_SHORT[g.route] || g.route}</Chip>}
              </div>
            </PanelSectionRow>
            <PanelSectionRow>
              <ButtonItem
                layout="below"
                disabled={busy !== "" || scanning}
                description={g.installed ? shortRoute(g.route) : (ROUTE_LABELS[routes[g.index] || g.route] || g.status)}
                onClick={() => (g.installed ? onUninstall(g) : onInstall(g))}
              >
                {`${g.installed ? "Remove from" : "Install in"} ${g.name}`}
              </ButtonItem>
            </PanelSectionRow>
            {!g.installed && g.available.length > 1 && (
              <PanelSectionRow>
                <DropdownItem
                  label={`Install ${g.name} via:`}
                  rgOptions={g.available.map((a) => ({ data: a, label: ROUTE_LABELS[a] || a }))}
                  selectedOption={routes[g.index] || g.route}
                  onChange={(o) => setRoutes({ ...routes, [g.index]: String(o.data) })}
                />
              </PanelSectionRow>
            )}
            <PanelSectionRow>
              <ButtonItem layout="below" disabled={busy !== ""} onClick={() => setExpanded({ ...expanded, [g.index]: !expanded[g.index] })}>
                {expanded[g.index] ? "Hide details" : "Details"}
              </ButtonItem>
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
            {g.installed && iniFor !== g.index && (
              <PanelSectionRow>
                <ButtonItem layout="below" disabled={busy !== "" || scanning} onClick={() => onOpenIni(g)}>
                  {`Settings — ${g.name}`}
                </ButtonItem>
              </PanelSectionRow>
            )}
            {g.installed && logFor !== g.index && (
              <PanelSectionRow>
                <ButtonItem layout="below" disabled={busy !== "" || scanning} onClick={() => onOpenLog(g)}>
                  {`Log — ${g.name}`}
                </ButtonItem>
              </PanelSectionRow>
            )}
            {g.installed && logFor === g.index && (
              <div>
                {gameLogs.map((l, i) => (
                  <PanelSectionRow key={i}>
                    <Card tone="neutral" radius={M3.shape.md} style={{ background: M3.color.surfaceContainerLow, width: "100%" }}>
                      <div style={{ ...M3.type.caption, color: M3.color.onSurfaceVariant, marginBottom: M3.space.xs, overflowWrap: "anywhere" }}>{l.path}</div>
                      <div style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", fontSize: 12, maxHeight: 200, overflowY: "auto", overflowX: "hidden", fontFamily: M3.mono }}>{l.tail.slice(-4000)}</div>
                    </Card>
                  </PanelSectionRow>
                ))}
                {logFixes.map((f) => (
                  <PanelSectionRow key={f.id}>
                    <ButtonItem layout="below" disabled={busy !== ""} description={f.label} onClick={() => onApplyFix(f.id)}>
                      Apply fix
                    </ButtonItem>
                  </PanelSectionRow>
                ))}
                <PanelSectionRow>
                  <ButtonItem layout="below" onClick={() => { setLogFor(null); setGameLogs([]); setLogFixes([]); }}>
                    Close log
                  </ButtonItem>
                </PanelSectionRow>
              </div>
            )}
            {g.installed && iniFor === g.index && ini && (
              <div>
                <PanelSectionRow>
                  <div style={{ fontSize: 12 }}>{iniPath}</div>
                </PanelSectionRow>
                <PanelSectionRow>
                  <DropdownItem
                    label="Sharpener"
                    description="NVIDIA's filter, AMD FSR's RCAS, or off (as DLSS)"
                    rgOptions={[
                      { data: "off", label: "Off" },
                      { data: "nvidia", label: "NVIDIA" },
                      { data: "rcas", label: "RCAS (AMD)" },
                    ]}
                    selectedOption={ini.Sharpener}
                    onChange={(o) => setIni({ ...ini, Sharpener: String(o.data) })}
                  />
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
                <PanelSectionRow>
                  <ToggleField
                    label="Use game slider"
                    description="Follow the game's sharpness slider, if it has one"
                    checked={ini.UseGameSlider === "true"}
                    onChange={(c: boolean) => setIni({ ...ini, UseGameSlider: c ? "true" : "false" })}
                  />
                </PanelSectionRow>
                <PanelSectionRow>
                  <DropdownItem
                    label="WaveSize"
                    description="64 for RX 7000 / 9000 in OptiScaler games if the log says wave32"
                    rgOptions={[
                      { data: "default", label: "Default" },
                      { data: "64", label: "64" },
                      { data: "32", label: "32" },
                    ]}
                    selectedOption={ini.WaveSize}
                    onChange={(o) => setIni({ ...ini, WaveSize: String(o.data) })}
                  />
                </PanelSectionRow>
                {Object.keys(presets).length > 0 && (
                  <PanelSectionRow>
                    <DropdownItem
                      label="Preset"
                      description="Apply a saved set of values"
                      rgOptions={Object.keys(presets).map((n) => ({ data: n, label: n }))}
                      selectedOption={presetPick}
                      onChange={(o) => setPresetPick(String(o.data))}
                    />
                  </PanelSectionRow>
                )}
                {presetPick && presets[presetPick] && (
                  <PanelSectionRow>
                    <ButtonItem layout="below" disabled={busy !== ""} onClick={onApplyPreset}>
                      {`Apply "${presetPick}"`}
                    </ButtonItem>
                  </PanelSectionRow>
                )}
                {presetPick && (
                  <PanelSectionRow>
                    <ButtonItem layout="below" disabled={busy !== ""} onClick={onDeletePreset}>
                      {`Delete "${presetPick}"`}
                    </ButtonItem>
                  </PanelSectionRow>
                )}
                <PanelSectionRow>
                  <TextField
                    label="Preset name"
                    description="Save the current editor values as a preset"
                    value={presetName}
                    onChange={(e) => setPresetName((e.target as HTMLInputElement).value)}
                  />
                </PanelSectionRow>
                <PanelSectionRow>
                  <ButtonItem layout="below" disabled={busy !== "" || !presetName.trim()} onClick={onSavePreset}>
                    Save preset
                  </ButtonItem>
                </PanelSectionRow>
                <PanelSectionRow>
                  <ButtonItem layout="below" disabled={busy !== ""} onClick={onSaveIni}>
                    Save settings
                  </ButtonItem>
                </PanelSectionRow>
                <PanelSectionRow>
                  <ButtonItem layout="below" onClick={() => { setIniFor(null); setIni(null); setPresetPick(""); setPresetName(""); }}>
                    Close settings
                  </ButtonItem>
                </PanelSectionRow>
              </div>
            )}
          </Card>
            )}
          </Fragment>
        ))}
      </PanelSection>
      )}
      {hasVersions && (
      <PanelSection title="Folders">
        <PanelSectionRow>
          <TextField
            label="Extra game folder"
            description="A game outside Steam libraries (also: drag its folder onto a manual setup run)"
            value={folderInput}
            onChange={(e) => setFolderInput((e.target as HTMLInputElement).value)}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" disabled={busy !== "" || !folderInput.trim()} onClick={onAddFolder}>
            Add folder
          </ButtonItem>
        </PanelSectionRow>
        {(state?.extra_folders || []).map((f) => (
          <PanelSectionRow key={f}>
            <ButtonItem layout="below" disabled={busy !== ""} description={f} onClick={() => onRemoveFolder(f)}>
              Remove folder
            </ButtonItem>
          </PanelSectionRow>
        ))}
      </PanelSection>
      )}
      {hasVersions && (
      <PanelSection title="Storage">
        <PanelSectionRow>
          <div style={{ ...M3.type.title }}>Total {totalMB.toFixed(1)} MB in {downloadedVersions.length} version(s)</div>
        </PanelSectionRow>
        {downloadedVersions.map((v) => (
          <PanelSectionRow key={v.tag}>
            <div style={{ ...M3.type.body, color: M3.color.onSurfaceVariant }}>{v.tag} — {v.size_mb} MB on disk{v.tag === state?.active_tag ? " (active)" : ""}</div>
          </PanelSectionRow>
        ))}
        <PanelSectionRow>
          <ButtonItem layout="below" disabled={busy !== "" || downloadedVersions.length < 2} onClick={onRemoveOld}>
            {confirm === "remove old versions" ? "Confirm remove all?" : "Remove all except active"}
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" disabled={busy !== ""} onClick={onClearDlss}>
            {confirm === "clear SDK cache" ? "Confirm clear cache?" : "Clear cached SDK files"}
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" disabled={busy !== ""} onClick={onResetAll}>
            {confirm === "reset everything" ? "Confirm reset all?" : "Reset everything"}
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>
      )}
      <PanelSection title="Health">
        <PanelSectionRow>
          <ButtonItem layout="below" disabled={busy !== ""} onClick={onHealth}>
            Run health check
          </ButtonItem>
        </PanelSectionRow>
        {(health || []).map((c) => (
          <PanelSectionRow key={c.name}>
            <div style={{ display: "flex", gap: M3.space.sm, alignItems: "baseline", ...M3.type.body }}>
              <Chip tone={c.ok ? "success" : "error"}>{c.ok ? "OK" : "FAIL"}</Chip>
              <span style={{ overflowWrap: "anywhere" }}>{c.name} — {c.detail}</span>
            </div>
          </PanelSectionRow>
        ))}
      </PanelSection>
      <PanelSection title="Plugin update">
        {puCheck?.update_available && !updating && !(pu?.done && pu?.ok) && (
          <PanelSectionRow>
            <div style={{ fontSize: 12 }}>v{puCheck.latest} available (you run v{puCheck.current}).</div>
          </PanelSectionRow>
        )}
        {puCheck?.update_available && puCheck?.body && !updating && !(pu?.done && pu?.ok) && (
          <PanelSectionRow>
            <div style={{ whiteSpace: "pre-wrap", fontSize: 12, maxHeight: 160, overflowY: "auto" }}>{puCheck.body.slice(0, 800)}</div>
          </PanelSectionRow>
        )}
        {puCheck?.update_available && !updating && !(pu?.done && pu?.ok) && (
          <PanelSectionRow>
            <ButtonItem layout="below" disabled={busy !== ""} onClick={onStartPluginUpdate}>
              {puCheck.can_install === false
                ? `Download v${puCheck.latest} for manual install`
                : `Download & install v${puCheck.latest}`}
            </ButtonItem>
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
                  ? `${fmtSize(pu.downloaded)} / ${fmtSize(pu.total)} (${Math.floor((100 * pu.downloaded) / pu.total)}%)`
                  : "Starting update ..."}
              </div>
            </div>
          </PanelSectionRow>
        )}
        {!updating && (
          <PanelSectionRow>
            <ButtonItem layout="below" disabled={busy !== "" || checkingUpdate} onClick={() => onCheckPluginUpdate(true)}>
              {checkingUpdate ? "Checking ..." : puCheck ? "Check again" : "Check for updates"}
            </ButtonItem>
          </PanelSectionRow>
        )}
      </PanelSection>
      {output && (
        <PanelSection title="Log">
          <PanelSectionRow>
            <Card tone="neutral" radius={M3.shape.md} style={{ background: M3.color.surfaceContainerLow, width: "100%" }}>
              <div ref={logRef} style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", fontSize: 12, maxHeight: 240, overflowY: "auto", overflowX: "hidden", fontFamily: M3.mono }}>{output.slice(-4000)}</div>
            </Card>
          </PanelSectionRow>
        </PanelSection>
      )}
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
