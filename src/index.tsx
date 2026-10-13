import {
  ButtonItem,
  DropdownItem,
  PanelSection,
  PanelSectionRow,
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
  apiDownloadVersion,
  apiGetBuildStatus,
  apiGetReleases,
  apiGetState,
  apiInstallGames,
  apiListGames,
  apiRemoveVersion,
  apiSetActiveVersion,
  apiStartBuild,
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
        const [rels, s] = await Promise.all([apiGetReleases(), apiGetState()]);
        setReleases(rels);
        setState(s);
        setPicked(s.active_tag || s.versions[0]?.tag || rels[0]?.tag || "");
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
    setBusy("version");
    try {
      if (!pickedDownloaded) {
        if (!pickedRelease) return;
        setOutput(`Downloading ${pickedRelease.zip_name} (${fmtSize(pickedRelease.size)}) ...`);
        const r = await apiDownloadVersion(picked, pickedRelease.zip_url);
        if (!r.ok) {
          toaster.toast({ title: "Download failed", body: r.error || "" });
          setOutput(r.error || "download failed");
          return;
        }
        setOutput(`Downloaded ${r.dir}. Network built: ${r.network_built ? "yes" : "no — run Build"}.`);
      }
      setState(await apiSetActiveVersion(picked));
      setPicked(picked);
    } finally {
      setBusy("");
      refreshState();
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
      setOutput("Build started (~1 min). Polling log ...");
    } finally {
      setBusy("");
    }
  };

  const onListGames = async () => {
    setBusy("games");
    try {
      const r = await apiListGames();
      if (!r.ok) {
        setOutput(r.error || "game scan failed");
        setGames([]);
        return;
      }
      setGames(r.games);
      setGamesTag(r.tag || "");
      setOutput(`Found ${r.games.length} compatible game(s) with ${r.tag}.`);
    } finally {
      setBusy("");
    }
  };

  const onInstall = async (g: HelixGame) => {
    setBusy(`game-${g.index}`);
    try {
      const r = await apiInstallGames([g.index]);
      setOutput(r.output);
      toaster.toast({ title: r.ok ? `Installed in ${g.name}` : "Install failed", body: r.ok ? "" : r.output.slice(-200) });
      onListGames();
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
      onListGames();
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
          <ButtonItem layout="below" disabled={busy !== "" || !picked} onClick={onDownloadOrUse}>
            {pickedDownloaded ? `Use ${picked}` : `Download ${picked}`}
          </ButtonItem>
        </PanelSectionRow>
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
          <ButtonItem layout="below" disabled={busy === "games" || !activeVersion} onClick={onListGames}>
            {`Scan games${gamesTag ? ` (${gamesTag})` : ""}`}
          </ButtonItem>
        </PanelSectionRow>
        {games.map((g) => (
          <PanelSectionRow key={g.index}>
            <ButtonItem
              layout="below"
              disabled={busy !== ""}
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
