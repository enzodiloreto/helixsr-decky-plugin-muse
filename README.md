# HelixSR Muse Plugin

A [Decky Loader](https://github.com/SteamDeckHomebrew/decky-loader) plugin that manages
[HelixSR](https://github.com/lonewolf0622/HelixSR) (NVIDIA DLSS Model E upscaling on AMD
GPUs): pick a release, download it with a progress bar, build its network, and install it
into compatible Steam games — all from the Quick Access menu.

Game detection and install/uninstall run HelixSR's own `setup/helixsr_install.py` logic
(vendored in `py_modules/helixscan.py` under its Apache-2.0 license); the network build
runs HelixSR's own `helixsr-setup.sh` unmodified.

## Install

On your Deck (or desktop Steam with Decky), Decky > Settings > Developer >
**Install Plugin from ZIP File**, using the zip attached to a
[release](https://github.com/enzodiloreto/helixsr-decky-plugin-muse/releases)
(or URL-install it from the release download link).

Upgrading from ≤ v0.0.14 (named *HelixSR-Decky-Plugin-Muse*)? Install the new zip —
it appears as *HelixSR Muse Plugin* and carries your settings and downloads over
automatically on first run. Then uninstall the old entry.

## Use

The panel follows four steps, with a status line and current step shown on top:

1. **Version** — pick a HelixSR release (update badge + release notes included),
   **Download** it (progress bar with MB/MB, %, speed and ETA), then **Switch to** /
   **Remove** it. **Reload releases** retries failed fetches.
2. **Network build** — **Check build readiness** first (Proton/Wine, Python + numpy,
   disk space; runs automatically before every build), then **Build network** or
   **Rebuild network**. The build downloads NVIDIA's DLSS DLL once (~56 MB, used to
   generate the network, then deleted), then compiles shaders through Proton/Wine's
   DXC — expect several minutes; the Log streams progress with a % bar.
3. **Games** — **Scan games** walks your Steam libraries for DLSS / XeSS / FSR titles
   (the last scan is remembered, incl. install states; scans run in the background
   and can be cancelled). Pick the install way per game (FSR 3.1 drop-in, OptiScaler,
   or the experimental DLSS loader). **Install in / Remove from** each game,
   **Install/Remove all** at once, or **Migrate** every outdated game to the active
   build in one go. Per-game **Settings** edit `helixsr.ini` inline (sharpening,
   sharpness, game slider, WaveSize). Extra non-Steam folders can be added by path.
4. **Storage** — per-version disk use, remove-all-except-active (refuses versions
   games still use), clear cached SDK files.
5. **Log** — every failure surfaces its actual reason here instead of failing
   silently; destructive actions ask for a second tap to confirm.

Requirements for building: any Proton installed in Steam (for the shader compiler)
and ~1 GB free. Game installs only touch files inside each game's folder (originals
are kept as `*.original.dll` / `HelixSR/backup`).

## Troubleshooting

- **Empty release list**: the Log shows why (no network, GitHub rate limit, TLS
  error). **Reload releases** retries without reopening the panel.
- **"UI vX but backend vY" warning**: a partial update — reinstall the zip and
  restart Decky.
- **Build fails**: the Log names the step. Handled automatically so far: a system
  Python without SSL support (the plugin downloads NVIDIA's DLL itself and passes
  it via `--dlss`), missing executable bits on extracted files (repaired).
- **Scan takes minutes**: normal on large libraries; backgrounded, cancellable,
  and reused for installs for 5 minutes. Rescan after adding games, switching
  versions, or changing anything outside the plugin.

## Develop

```bash
pnpm i                 # pnpm v9
pnpm run build         # frontend -> dist/ (prebuild stamps src/version.ts)
pnpm test              # python3 -m unittest discover -s tests -v (stdlib only)
```

Release zips (`out/helixsr-muse-plugin-vX.Y.Z.zip`) contain exactly:
`LICENSE`, `README.md`, `dist/index.js`, `main.py`, `package.json`, `plugin.json`,
`py_modules/` (no sourcemaps, no tests). Bump `package.json` before every release.
