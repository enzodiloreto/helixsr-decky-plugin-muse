# HelixSR Muse Plugin

![HelixSR Muse Plugin banner](assets/helixsr_muse_banner_main.png)

A [Decky Loader](https://github.com/SteamDeckHomebrew/decky-loader) plugin that manages
[HelixSR](https://github.com/lonewolf0622/HelixSR) **2.0 and newer** (upscaling on AMD
GPUs, built from NVIDIA's DLSS model): pick a release, download it with a progress bar,
build its network (optionally with frame generation), and install it into compatible
games — FSR 3.1 titles get the FSR drop-in, DLSS/XeSS/FSR2-only titles get HelixSR
Scaler — all from the Quick Access menu. The Scaler ships **inside** the 2.0 release
zip, version-paired with the release it belongs to, so there is no separate Scaler
download.

**HelixSR 2.0+ only.** The plugin talks to the 2.0 release's own
`setup/helixsr_bridge.py` for every game operation (scan, install, remove, repair,
verify, settings, logs). The bridge runs as a subprocess and the plugin never
imports upstream files, so upstream code cannot take the plugin backend down
(HelixSR's Freeware license also forbids vendoring it).

Tested on Steam Deck LCD running CachyOS and Steam Machine Running SteamOS.

## Install

On your Deck (or desktop Steam with Decky), Decky > Settings > Developer >
**Install Plugin from ZIP File**, using the zip attached to a
[release](https://github.com/enzodiloreto/helixsr-decky-plugin-muse/releases)
(or URL-install it from the release download link). Afterwards the plugin updates
itself from its panel: confirm Decky's install popup and it downloads, installs
(with proper permissions even for root-owned installs) and hot-reloads — no
Developer settings trip, no restart. (If Decky's installer is unreachable, it
falls back to the in-place or manual-zip flows.)

Upgrading from ≤ v0.0.14 (named *HelixSR-Decky-Plugin-Muse*)? Install the new zip —
it appears as *HelixSR Muse Plugin* and carries your settings and downloads over
automatically on first run. Then uninstall the old entry.

## Use

The panel opens with a header that says where you are — **Step 2 of 4 — Build the
network** — with the one button that moves you forward, and the **Last action** card
right below it with the result of what you just did. Then the sections: Version,
Build, Games, Storage & updates. Every long action runs in the background while the
panel stays responsive.

### Version

The HelixSR release feed (newest first; prereleases stay hidden unless you show
them). Pick a release and **Download** it: the progress bar shows MB/MB, %, speed
and ETA, and the release notes are shown before the download starts. Downloads are
disk-gated — the plugin refuses when the free space cannot hold the zip plus its
extraction — and a half-extracted version is cleaned up when a download fails.
Downloaded versions are listed with their DLL version, disk use, network state, FG
state and bundled Scaler version; **Use** switches to one, **Remove** deletes it
(two-tap confirm). **Reload releases** retries a failed fetch.

### Build

The network (the shader network HelixSR installs into games) is built by the
release's own setup script, run directly by the plugin — never through the
release's `--yes` wrapper, which would also install the game behind your back.

- **Check readiness** — toolchain, Python + numpy, disk space and the memory the
  shader compile may need (the compile fans out one worker per CPU thread).
- **Build** / **Rebuild** — the log streams with a parsed % bar, and **Cancel
  build** stops the whole process group (no orphaned workers left behind).
- **Frame generation** toggle — adds the FG build (NVIDIA's DLSS Frame Generation
  DLLs and the NGX core are downloaded during the build; close the game first). A
  build whose shader compile does not fit in the free memory is refused up front,
  with the reason.

### Games

**Scan games** walks your Steam libraries (plus any folder you add by hand) with the
release's own scanner, in the background: it can be cancelled, and the last scan is
remembered and reused. Only the games HelixSR can actually use are listed — a game
with FSR 3.1 (the drop-in) or a detected upscaler (DLSS/XeSS/FSR 2, the Scaler).
Games with no upscaler to take over are left out (the count is shown next to the
list); a game that already has HelixSR in it always stays listed, so it can be
updated or removed. Each game card shows its state, the inputs it was detected
with, anti-cheat markers and the Steam launch options, and offers:

- the **way** picker — **Automatic** (HelixSR picks the game's best way),
  **HelixSR Scaler** (pick DLSS in the game's graphics menu; Insert opens its
  menu) or **FSR drop-in** (pick AMD FSR in the game). Your pick is remembered
  between sessions (it survives a re-scan and a panel close), and installing
  with a different way switches the game (the other install is removed first) —
  the button then reads *Switch to …*.
- **Install**, **Update**, **Switch to …** and a **More** button that opens the
  rest: **Remove**, **Repair** (a clean re-install of the same way), **Roll back**
  (restore the previous install), **FG on/off** (Scaler games on an FG build),
  **Settings** and **Log**.
- **Anti-cheat games get the FSR drop-in only.** The Scaler is not used there (it
  can get the account banned in online play), so an anti-cheat game without FSR 3.1
  is not listed at all.
- **Settings** edits the game's own ini inline (the editor follows the kind of
  settings the installed way uses) with named **presets** (save, apply, delete) and
  a **Defaults** reset. **Log** shows the game's log files with one-tap fixes for
  the known patterns. Both open inside the game's own card, right under its
  buttons.
- **Verify installs** checks the install markers and files, so a Steam update that
  replaced the drop-ins is detected; affected games get a one-tap **Repair**.
- **Settings** edits the game's own ini inline (the editor follows the kind of
  settings the installed way uses) with named **presets** (save, apply, delete) and
  a **Defaults** reset. **Log** shows the game's log files with one-tap fixes for
  the known patterns.

### Storage & updates

Per-version disk use, **Remove old versions** (keeps the active version),
**Factory reset** (uninstalls HelixSR everywhere and wipes the downloaded versions
and caches, keeping your presets), and **Health check**: GitHub reachable, versions
healthy, games verified, disk space and update state. The plugin's own update lives
here too — **Check for update**, then **Update to vX.Y.Z** (Decky's installer with
its consent popup, then a hot reload; the download can be cancelled). The panel
shows the running plugin version.

### Output

Every failure surfaces its actual reason here instead of failing silently, and
destructive actions (remove, repair, reset, install into an anti-cheat game) ask
for a second tap to confirm.

Requirements for building: any Proton installed in Steam (for the shader compiler)
and ~1 GB free (~1.5 GB with frame generation). Game installs only touch files
inside each game's folder (the originals are kept as backups).

## Troubleshooting

- **Empty release list**: the Output section shows why (no network, GitHub rate
  limit, TLS error). **Reload releases** retries without reopening the panel.
- **"UI vX but backend vY" warning**: a partial update — tap **Reload plugin**
  in the banner first (it heals it without reinstalling in most cases); if it
  persists, reinstall the zip and restart Decky.
- **Build fails**: the log names the step. Handled automatically so far: a Python
  without CA certificates (the plugin hands the release's setup the CA bundle it
  ships, so the setup's own downloads verify), a system Python without SSL
  support (the plugin downloads NVIDIA's DLSS DLL itself and passes it via
  `--dlss`), missing executable bits on extracted files (repaired).
- **"certificate verify failed: unable to get local issuer certificate"**: the
  Python that ran the setup had no CA certificates. The plugin now passes its own
  CA bundle to the setup (`SSL_CERT_FILE`); on an older plugin, install the new
  zip to get it.
- **Frame generation build refused**: the shader compile needs more memory than
  the readiness check found. Close the game and retry, or build without FG.
- **Download refused**: not enough free disk for the zip plus its extraction (on a
  full Deck the Steam client itself crashes).
- **Scan takes minutes**: normal on large libraries; backgrounded, cancellable,
  and the last scan is reused. Re-scan after adding games or switching versions.
- **Steam update broke an install**: the drop-ins are replaced silently. Run
  **Verify installs** and **Repair** the affected games.
- **Scaler: no menu on Insert**: the Steam launch options are missing (they are
  shown on the game's card). On Deck, map a back button to Insert in the game's
  controller settings. Never use Scaler in online or anti-cheat games.

## Develop

```bash
pnpm i                 # pnpm v9
pnpm run build         # frontend -> dist/ (prebuild stamps src/version.ts)
pnpm test              # backend unittest (stdlib only) + frontend UI tests
```

Release zips (`out/helixsr-muse-plugin-vX.Y.Z.zip`) contain exactly:
`LICENSE`, `README.md`, `dist/index.js`, `main.py`, `package.json`, `plugin.json`,
`py_modules/` (`.keep`, `bridge.py`, `cacert.pem`, `settings.py` — no sourcemaps,
no tests). Bump `package.json` before every release.
