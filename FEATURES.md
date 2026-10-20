# HelixSR Muse Plugin — Feature Catalog

Every user-facing capability of the plugin (v0.6.2), grouped by panel section.
Backend: `main.py` + `py_modules/bridge.py` (client for the release's own
`setup/helixsr_bridge.py`).
Frontend: `src/index.tsx` (+ `src/services/api.ts`).

HelixSR **2.0+ only**: 1.x support, the separate Scaler feed and the migration
flow are gone. Every game operation runs the downloaded release's own bridge as a
subprocess; upstream files are never imported.

## Version (HelixSR releases)

- Release feed from the HelixSR GitHub API (newest first, zip assets only), the
  three newest visible with a **Show all N releases** expander. Prereleases stay
  hidden until **Show prereleases** is toggled. Failures surface the actual
  reason (network, rate limit, TLS) instead of an empty list; **Reload releases**
  retries without reopening the panel.
- **Download** with a live progress bar (MB/MB, %, speed, ETA), cancellable
  mid-extraction. The download is **disk-gated**: it is refused when the free
  space cannot hold the zip plus its extraction, and a half-extracted version is
  removed when the download fails (a previous good install of the same tag is
  kept).
- The release notes are shown before a download starts.
- Downloaded versions are listed with their DLL stamp, disk use, network state, FG
  state and bundled Scaler version. **Use** switches to one, **Remove** deletes it
  (two-tap confirm, heals the active version when it is removed).
- The release's `--status` probe (hashes NVIDIA's DLLs, reads the FG archives) runs
  **once per release and is cached for 5 minutes**; the cache is cleared after
  builds, downloads and removals.

## Build (the shader network)

- **Check readiness** runs automatically before every build and reports:
  *Python 3 + numpy* (a note, not a gate — the plugin downloads a portable Python
  during the build), *Proton/Wine (shader compiler)*, *Disk space*, *Network /
  frame generation*. With FG enabled it adds *Shader compile memory*, *DLSS Frame
  Generation DLLs* (two NVIDIA Streamline archives) and *NGX core prerequisites*
  (`zstd` or Python 3.14).
- **Build** / **Rebuild** (two-tap) run the release's own
  `setup/helixsr_setup.py` directly — never the release's `--yes` wrapper, which
  would also install the game behind the user's back. Exit code 3 (upscaling built,
  FG failed) counts as success.
- The build provisions what the release's own setup script would: a portable
  Python with numpy, the pinned Windows DXC and a `dxc-wine.sh` shim.
- The build log streams with a parsed % bar and a memory line (free memory and
  swap) at the start, after every toolchain download and every 30 s — the trail
  used to diagnose the OOM that killed Steam's UI.
- **Cancel build** kills the whole process group; a plugin reload reaps an
  orphaned build left behind by a previous session.
- **Frame generation** toggle adds the FG build (extra downloads; close the game
  first). The FG shader compile fans out one worker per CPU thread, so a build
  whose compile does not fit in the free memory is refused up front.
- Download and build are mutually exclusive (both have memory-heavy phases).

## Games

- **Scan games** walks the Steam libraries (plus hand-added folders) with the
  release's own scanner. Backgrounded, cancellable, per-folder failure isolation.
  The last scan is cached for 5 minutes and reused across panel opens, with the
  scan age and the scanned version shown.
- **Only the games HelixSR can use are listed**: the release's scan returns every
  folder with a game .exe (the Scaler can take over an upscaler built into the
  exe, which the scan cannot see), the plugin keeps the ones with something to
  take over — FSR 3.1 (the drop-in) or a detected upscaler (DLSS/XeSS/FSR 2, the
  Scaler). A game with an install always stays listed (it must stay manageable).
- **An anti-cheat game gets the FSR drop-in only**: the Scaler is not used there
  (it can get the account banned in online play), so an anti-cheat game without
  FSR 3.1 is not listed, and the way picker of an anti-cheat game offers only the
  FSR drop-in. The backend refuses a Scaler install in a game the scan flags as
  anti-cheat.
- Each game card shows: name, state chip (installed way + version, the
  recommended way, or the pending switch), folder, detected inputs, anti-cheat
  markers, FG state (Scaler games), Steam launch options and whether a previous
  install is kept for a rollback.
- **Way picker** per game: **Recommended** (HelixSR picks the game's best way),
  **HelixSR Scaler** (pick DLSS in the game's graphics menu; Insert opens its
  menu) or **FSR drop-in** (pick AMD FSR in the game). The pick survives
  re-scans and is persisted in the plugin's settings, so it is still there after
  the panel is closed and opened again; the install action uses the picked way,
  and switching ways removes the other install first (the button reads
  *Switch to …*). An anti-cheat game only offers the FSR drop-in.
- **Install**, **Update**, **Switch to …**, **Remove**, **Repair** (remove +
  reinstall of the same way), **Roll back** (restore the previous install),
  **FG on/off** (Scaler games on an FG build), **Settings**, **Log**. An
  anti-cheat game has no Scaler: it gets the FSR drop-in (or is not listed at
  all when it has no FSR 3.1).
- **Verify installs** checks the install markers *and* the files (loader, HelixSR
  DLL, scaler ini for Scaler; marker + log/backup for the drop-in), so a Steam
  update that replaced the drop-ins is detected and can be repaired.
- **Settings** editor: the game's own ini, inline. The editor follows the kind of
  settings the installed way uses (`helixsr` — Sharpening/Compatibility — or
  `scaler` — `HelixSR.*`), with labelled options, a Sharpness slider, free-text
  keys and a list of the other keys in the file. Named **presets** (save, apply,
  delete) plus a built-in **Default**; the bridge validates the values. The
  editor and the log open **inside the game's own card**, under its buttons.
- **Log** viewer: the game's log files with line counts, paged tails, the
  important lines and one-tap fixes for known patterns (the pattern is
  re-verified before anything is written). It opens inside the game card too.
- Extra non-Steam folders can be added and removed by path.

## Storage & updates

- Per-version disk use and total.
- **Remove old versions** keeps the active one and reports the freed space.
- **Factory reset** uninstalls HelixSR from every installed game (via the
  bridge) and wipes the downloaded versions and caches; the ini presets are kept.
  Two-tap confirm, every step reported.
- **Health check**: GitHub reachable (TCP probe), versions healthy (DLL present +
  network built), games verified, free disk space and the plugin update state.
- **Plugin update**: automatic check on panel open (6 h cache) + **Check for
  update**. **Update to vX.Y.Z** downloads the release zip and installs it through
  Decky's own installer (native consent popup, privileged install, hot reload); it
  falls back to an in-place swap or a manual-zip flow when Decky's installer is
  unreachable. The download is cancellable, and the panel shows the running plugin
  version and a restart banner when an update still needs a Decky restart.

## Output

- Every failure surfaces its actual reason here (network, TLS, rate limit, bridge
  error, missing file) instead of failing silently.
- Destructive actions (remove, repair, factory reset) use two-tap confirm-arming
  with a 15 s expiry.

## Panel behaviour

- Long work runs in daemon threads; the panel polls the backend and never blocks.
  A build that finishes while the panel is closed raises a toast.
- The panel keeps native Steam controls (dropdowns, sliders, toggles, text fields)
  for gamepad editing; the custom chrome (cards, chips, buttons) follows Material
  You.
- Disabled buttons explain why (not supported, busy, no version downloaded).
- A UI/backend version skew shows a banner with an in-panel **Reload plugin**.
