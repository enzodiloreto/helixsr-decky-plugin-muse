import {
  ButtonItem,
  DropdownItem,
  PanelSection,
  PanelSectionRow,
  ProgressBar,
  SliderField,
  ToggleField,
  staticClasses
} from "@decky/ui";
import {
  addEventListener,
  removeEventListener,
  definePlugin,
  toaster,
} from "@decky/api"
import { useEffect, useRef, useState } from "react";
import { FaPlug } from "react-icons/fa";
import {
  HelixGame,
  HelixRelease,
  HelixState,
  DownloadStatus,
  GameSettings,
  ScanCache,
  ScanStatus,
  apiCancelScan,
  apiGetBuildStatus,
  apiGetDownloadStatus,
  apiGetGameSettings,
  apiGetLastScan,
  apiGetReleases,
  apiGetScanStatus,
  apiGetState,
  apiInstallGames,
  apiRemoveVersion,
  apiSetActiveVersion,
  apiSetGameSettings,
  apiStartBuild,
  apiStartDownload,
  apiStartScan,
  apiUninstallGames,
} from "./services/api";
import { PLUGIN_VERSION } from "./version";

function fmtSize(n: number) {
  return `${(n / 1e6).toFixed(1)} MB`;
}

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

function parseBuildProgress(log: string): { pct: number; label: string } | null {
  // Matches the reference setup's bar: [####----]  42%  3:12  shaders 15/40
  const re = /\[([#-]{20})\]\s*(\d+)%\s*(\d+:\d+)\s*(.*)/g;
  let m: RegExpExecArray | null = null;
  let last: RegExpExecArray | null = null;
  while ((m = re.exec(log)) !== null) last = m;
  if (!last) return null;
  return { pct: Math.min(100, parseInt(last[2], 10)), label: `${last[3]} — ${last[4]}` };
}

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
          if (st.ok) {
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
            setIniFor(null);
            setIni(null);
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
  };

  const onBuild = async (rebuild: boolean) => {
    setBusy("build");
    try {
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

  const options = releases.map((r) => ({
    data: r.tag,
    label: `${r.tag} — ${r.published_at} — ${fmtSize(r.size)}${downloadedTags.has(r.tag) ? " ✓" : ""}`,
  }));
  const buildProg = building ? parseBuildProgress(output) : null;

  return (
    <div>
      {state && state.plugin_version !== PLUGIN_VERSION && (
        <PanelSection title="Warning">
          <PanelSectionRow>
            <div style={{ fontSize: 12 }}>
              UI v{PLUGIN_VERSION} but backend v{state.plugin_version || "unknown"} — a partial update.
              Reinstall the plugin zip and restart Decky.
            </div>
          </PanelSectionRow>
        </PanelSection>
      )}
      <PanelSection title="Version">
        <PanelSectionRow>
          <DropdownItem
            label="HelixSR release"
            description={activeVersion ? `Active: ${activeVersion.tag} (network ${activeVersion.network_built ? "built" : "NOT built"})` : "No version downloaded yet"}
            rgOptions={options}
            selectedOption={picked}
            onChange={(o) => setPicked(String(o.data))}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" disabled={busy !== "" || !releases.length} onClick={() => refreshReleases()}>
            Reload releases
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" disabled={busy !== "" || downloading || !picked} onClick={onDownloadOrUse}>
            {pickedDownloaded ? `Use ${picked}` : downloading ? "Downloading ..." : `Download ${picked}`}
          </ButtonItem>
        </PanelSectionRow>
        {downloading && (
          <PanelSectionRow>
            <div style={{ width: "100%" }}>
              <ProgressBar nProgress={dl && dl.total > 0 ? Math.min(100, (100 * dl.downloaded) / dl.total) : 0} />
              <div style={{ fontSize: 12, marginTop: 4 }}>
                {dl && dl.total > 0
                  ? `${fmtSize(dl.downloaded)} / ${fmtSize(dl.total)} (${Math.floor((100 * dl.downloaded) / dl.total)}%)${dlRate ? ` — ${dlRate}` : ""}`
                  : dl ? `${fmtSize(dl.downloaded)} downloaded ...${dlRate ? ` — ${dlRate}` : ""}` : "Starting download ..."}
              </div>
            </div>
          </PanelSectionRow>
        )}
        {pickedDownloaded && (
          <PanelSectionRow>
            <ButtonItem layout="below" disabled={busy !== ""} onClick={onRemove}>
              {`Remove ${picked} files`}
            </ButtonItem>
          </PanelSectionRow>
        )}
        <PanelSectionRow>
          <ButtonItem layout="below" disabled={busy !== "" || building || !activeVersion} onClick={() => onBuild(Boolean(activeVersion?.network_built))}>
            {building ? "Building network ..." : activeVersion?.network_built ? "Rebuild network (setup.sh)" : "Build network (setup.sh)"}
          </ButtonItem>
        </PanelSectionRow>
        {building && buildProg && (
          <PanelSectionRow>
            <div style={{ width: "100%" }}>
              <ProgressBar nProgress={buildProg.pct} />
              <div style={{ fontSize: 12, marginTop: 4 }}>{buildProg.pct}% — {buildProg.label}</div>
            </div>
          </PanelSectionRow>
        )}
      </PanelSection>
      <PanelSection title="Games">
        <PanelSectionRow>
          <ButtonItem layout="below" disabled={scanning || busy !== "" || !activeVersion} onClick={onScan}>
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
            <div style={{ width: "100%" }}>
              <ProgressBar indeterminate={true} />
              <div style={{ fontSize: 12, marginTop: 4 }}>Walking Steam libraries — this can take a few minutes on large collections ...</div>
            </div>
          </PanelSectionRow>
        )}
        {games.map((g) => (
          <div key={g.index}>
            <PanelSectionRow>
              <ButtonItem
                layout="below"
                disabled={busy !== "" || scanning}
                description={`${g.name} — ${g.status}`}
                onClick={() => (g.installed ? onUninstall(g) : onInstall(g))}
              >
                {`${g.installed ? "Remove from" : "Install in"} ${g.name}`}
              </ButtonItem>
            </PanelSectionRow>
            {!g.installed && g.available.length > 1 && (
              <PanelSectionRow>
                <DropdownItem
                  label={`Way — ${g.name}`}
                  rgOptions={g.available.map((a) => ({ data: a, label: ROUTE_LABELS[a] || a }))}
                  selectedOption={routes[g.index] || g.route}
                  onChange={(o) => setRoutes({ ...routes, [g.index]: String(o.data) })}
                />
              </PanelSectionRow>
            )}
            {g.installed && iniFor !== g.index && (
              <PanelSectionRow>
                <ButtonItem layout="below" disabled={busy !== "" || scanning} onClick={() => onOpenIni(g)}>
                  {`Settings — ${g.name}`}
                </ButtonItem>
              </PanelSectionRow>
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
                <PanelSectionRow>
                  <ButtonItem layout="below" disabled={busy !== ""} onClick={onSaveIni}>
                    Save settings
                  </ButtonItem>
                </PanelSectionRow>
                <PanelSectionRow>
                  <ButtonItem layout="below" onClick={() => { setIniFor(null); setIni(null); }}>
                    Close settings
                  </ButtonItem>
                </PanelSectionRow>
              </div>
            )}
          </div>
        ))}
      </PanelSection>
      {output && (
        <PanelSection title="Log">
          <PanelSectionRow>
            <div style={{ whiteSpace: "pre-wrap", fontSize: 12, maxHeight: 240, overflowY: "auto" }}>{output.slice(-4000)}</div>
          </PanelSectionRow>
        </PanelSection>
      )}
      {state?.plugin_version && (
        <PanelSectionRow>
          <div style={{ fontSize: 11, opacity: 0.6 }}>plugin v{state.plugin_version}</div>
        </PanelSectionRow>
      )}
    </div>
  );
};

export default definePlugin(() => {
  console.log("HelixSR initializing, this is called once on frontend startup")

  const listener = addEventListener<[ok: boolean, rc: number]>("helixsr_build_done", (ok, rc) => {
    console.log("HelixSR build done:", ok, rc)
  });

  return {
    // The name shown in various decky menus
    name: "HelixSR-Decky-Plugin-Muse",
    // The element displayed at the top of your plugin's menu
    titleView: <div className={staticClasses.Title}>HelixSR</div>,
    // The content of your plugin's menu
    content: <Content />,
    // The icon displayed in the plugin list
    icon: <FaPlug />,
    // The function triggered when your plugin unloads
    onDismount() {
      console.log("Unloading HelixSR")
      removeEventListener("helixsr_build_done", listener);
    },
  };
});
