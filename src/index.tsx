import {
  ButtonItem,
  DropdownItem,
  PanelSection,
  PanelSectionRow,
  ProgressBar,
  staticClasses
} from "@decky/ui";
import {
  addEventListener,
  removeEventListener,
  definePlugin,
  toaster,
} from "@decky/api"
import { useEffect, useState } from "react";
import { FaPlug } from "react-icons/fa";
import {
  HelixGame,
  HelixRelease,
  HelixState,
  DownloadStatus,
  ScanStatus,
  apiGetBuildStatus,
  apiGetDownloadStatus,
  apiGetReleases,
  apiGetScanStatus,
  apiGetState,
  apiInstallGames,
  apiRemoveVersion,
  apiSetActiveVersion,
  apiStartBuild,
  apiStartDownload,
  apiStartScan,
  apiUninstallGames,
} from "./services/api";

function fmtSize(n: number) {
  return `${(n / 1e6).toFixed(1)} MB`;
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
  const [scanning, setScanning] = useState<boolean>(false);

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

  useEffect(() => {
    (async () => {
      try {
        const [relsRes, s] = await Promise.all([apiGetReleases(), apiGetState()]);
        setReleases(relsRes.releases);
        setState(s);
        setPicked(s.active_tag || s.versions[0]?.tag || relsRes.releases[0]?.tag || "");
        if (relsRes.error) setOutput(`Failed to load HelixSR releases: ${relsRes.error}`);
      } catch (e) {
        console.error("[helixsr] init failed", e);
      }
      const listener = addEventListener<[ok: boolean, rc: number]>("helixsr_build_done", (ok, rc) => {
        setBuilding(false);
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
        if (st.done) {
          setDownloading(false);
          if (st.ok) {
            setOutput(`Downloaded ${st.dir}. Network built: ${st.network_built ? "yes" : "no — run Build"}.`);
            setState(await apiSetActiveVersion(st.tag));
            setPicked(st.tag);
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
            setOutput(`Found ${st.games.length} compatible game(s) with ${st.tag}.${st.skipped ? ` ${st.skipped} folder(s) skipped (unreadable).` : ""}`);
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
      } finally {
        setBusy("");
        refreshState();
      }
      return;
    }
    if (!pickedRelease) return;
    setDl(null);
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
    } finally {
      setBusy("");
      refreshState();
    }
  };

  const onBuild = async () => {
    setBusy("build");
    try {
      const r = await apiStartBuild();
      if (!r.ok) {
        toaster.toast({ title: "Build not started", body: r.error || "" });
        setOutput(r.error || "build not started");
        return;
      }
      setBuilding(true);
      setOutput("Build started (compiling shaders — can take several minutes). Polling log ...");
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

  const onInstall = async (g: HelixGame) => {
    setBusy(`game-${g.index}`);
    try {
      const r = await apiInstallGames([g.index]);
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

  return (
    <div>
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
                  ? `${fmtSize(dl.downloaded)} / ${fmtSize(dl.total)} (${Math.floor((100 * dl.downloaded) / dl.total)}%)`
                  : dl ? `${fmtSize(dl.downloaded)} downloaded ...` : "Starting download ..."}
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
          <ButtonItem layout="below" disabled={busy !== "" || building || !activeVersion} onClick={onBuild}>
            {building ? "Building network ..." : "Build network (setup.sh)"}
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>
      <PanelSection title="Games">
        <PanelSectionRow>
          <ButtonItem layout="below" disabled={scanning || busy !== "" || !activeVersion} onClick={onScan}>
            {scanning ? "Scanning ..." : `Scan games${gamesTag ? ` (${gamesTag})` : ""}`}
          </ButtonItem>
        </PanelSectionRow>
        {scanning && (
          <PanelSectionRow>
            <div style={{ width: "100%" }}>
              <ProgressBar indeterminate={true} />
              <div style={{ fontSize: 12, marginTop: 4 }}>Walking Steam libraries — this can take a few minutes on large collections ...</div>
            </div>
          </PanelSectionRow>
        )}
        {games.map((g) => (
          <PanelSectionRow key={g.index}>
            <ButtonItem
              layout="below"
              disabled={busy !== "" || scanning}
              description={`${g.name} — ${g.status}`}
              onClick={() => (g.installed ? onUninstall(g) : onInstall(g))}
            >
              {`${g.installed ? "Remove from" : "Install in"} ${g.name}`}
            </ButtonItem>
          </PanelSectionRow>
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
