# HelixSR Decky Plugin

A [Decky Loader](https://github.com/SteamDeckHomebrew/decky-loader) plugin that manages
[HelixSR](https://github.com/lonewolf0622/HelixSR) (NVIDIA DLSS Model E upscaling on AMD GPUs):
pick a release, download it with a progress bar, build its network, and install it into
compatible Steam games — all from the Quick Access menu.

Game detection and install/uninstall use HelixSR's own `setup/helixsr_install.py`
logic (vendored in `py_modules/helixscan.py` under its Apache-2.0 license); the network
build runs HelixSR's own `helixsr-setup.sh` unmodified.

## Install

On your Deck (or desktop Steam with Decky), Decky > Settings > Developer >
**Install Plugin from ZIP File**, using the zip attached to a
[release](https://github.com/enzodiloreto/helixsr-decky-plugin-muse/releases)
(or URL-install it from the release download link).

## Use

1. **Version**: pick a HelixSR release, **Download** it (progress bar shows MB/MB and %),
   then **Build network**. The build downloads NVIDIA's DLSS DLL once (~56 MB, used to
   generate the network, then deleted), then compiles shaders through Proton/Wine's DXC —
   expect several minutes; the Log section streams progress.
2. **Games**: **Scan games** walks your Steam libraries for DLSS / XeSS / FSR titles
   (the last scan is remembered, incl. install states).
   Pick the install way per game (FSR 3.1 drop-in, OptiScaler, or the experimental
   DLSS loader) instead of the default where it matters.
   **Install in / Remove from** each game, or **Install/Remove all** at once.
   Extra non-Steam folders can be added by path.
   When a newer built version exists, **migrate** games to it in one go.
   Per-game **Settings** edit `helixsr.ini` inline: sharpening (off / NVIDIA /
   RCAS + strength), game-slider following, WaveSize (64 for RX 7000 / 9000 in
   OptiScaler games if the log says wave32).
3. **Storage**: per-version disk use, remove-all-except-active, clear cached SDK files.
4. **Log**: every failure surfaces its actual reason here instead of failing silently.

Requirements for building: any Proton installed in Steam (for the shader compiler) and
~200 MB free for downloads. Game installs only touch files inside each game's folder
(originals are kept as `*.original.dll` / `HelixSR/backup`).

## Troubleshooting

- **Empty release list**: the Log shows why (no network, GitHub rate limit, TLS error).
  **Reload releases** retries without reopening the panel.
- **"UI vX but backend vY" warning**: a partial update — reinstall the zip and restart Decky.
- **Build fails**: the Log names the step. Common causes seen so far: a system Python
  without SSL support (the plugin downloads NVIDIA's DLL itself to work around it),
  missing executable bits on extracted files (repaired automatically).
- **Scan takes minutes**: normal on large libraries; it runs in the background and can
  be cancelled. Install/Remove reuse a fresh scan instead of re-walking.

## Develop

```bash
pnpm i                 # pnpm v9
pnpm run build         # frontend -> dist/
python3 -m unittest discover -s tests -v   # backend tests (stdlib only)
```

Release zips (`out/helixsr-decky-plugin-muse-vX.Y.Z.zip`) contain exactly:
`LICENSE`, `README.md`, `dist/index.js`, `main.py`, `package.json`, `plugin.json`,
`py_modules/` (no sourcemaps, no tests). Bump `package.json` before every release.
