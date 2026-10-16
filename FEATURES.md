# HelixSR Muse Plugin — Feature Catalog

Every user-facing capability of the plugin (v0.4.1), grouped by panel section.
Backend: `main.py` + `py_modules/helixscan.py` + `py_modules/helixref.py`.
Frontend: `src/index.tsx` (+ api).

## Status hero

- One-line live summary: active version, network state, installed/found games,
  disk use.
- 4-step progress stepper (Download → Build → Scan → Install) with the current
  step highlighted and named.
- State chips: network built / NOT built, games count + scanned tag, total MB,
  update availability.
- **Live operation bar**: whatever is running (release download, network build,
  game scan, migration, plugin update) shows a progress bar + label in the
  hero, visible from every section.
- **Next-step button**: the hero offers the current step's action directly
  (Build network now / Scan games now), so the obvious move doesn't require
  scrolling to its section.
- **Restart needed** banner after a self-update (persists until Decky restarts).
- **Partial update** banner on UI/backend version skew, with in-panel
  **Reload plugin** (falls back to reinstall + restart advice).

## 1 · Version (HelixSR releases)

- Release list from the HelixSR GitHub API (newest first, zip assets only),
  compacted to downloaded versions plus the newest few behind an expander;
  failures surface the actual reason (network, rate limit, TLS) instead of an
  empty list. **Reload releases** retries without reopening the panel.
  **Include prereleases** toggle covers both the HelixSR and Scaler lists.
- Old releases (e.g. 1.4.x) download and work: missing scripts tolerated,
  layouts validated, OptiScaler withheld where the release doesn't ship it.
- **Update available** badge with numeric tag comparison; release notes preview
  before downloading.
- **Download** with live progress bar (MB/MB, %, speed, ETA), cancellable;
  incomplete downloads are detected and partials cleaned. **Switch to** swaps
  the active version; **Remove** deletes files (two-tap confirm).
- Switching/removing versions clears the stale games list.

## 2 · Network build

- **Check build readiness** (auto-runs before every build): Python + numpy,
  Proton/Wine shader compiler, disk space, DLSS status — fail fast with reasons.
- **Build / Rebuild network**: downloads NVIDIA's DLSS DLL itself (hash-pinned,
  works around system Pythons without SSL), verifies it, hands it to HelixSR's
  own `helixsr-setup.sh` via `--dlss`; restores lost executable bits first.
  Build log streams with a parsed % progress bar; the once-used DLL is deleted
  after success, kept for retry after failure. Hung builds time out after 4h.
  **Cancel build** stops the DLL pre-download or terminates the setup script.
- **Frame generation** (1.9.0–1.9.2 with the FG engine shipped): build the FG
  DLLs in as well (~400 MB extra, extra checks in readiness); newer versions
  report FG as withdrawn and steer Scaler/FG installs accordingly. On builds
  without the engine the toggle stays visible but disabled, with the reason
  inline, and the flag resets when switching versions.

## 3 · Games

- **Scan games**: walks every Steam library (plus Heroic/Lutris and hand-added
  folders) for DLSS / XeSS / FSR titles using HelixSR's own detection logic.
  Backgrounded with progress, cancellable, per-folder failure isolation, results
  persisted across panel opens (with age note), reused for installs for 5 min.
- Text filter plus one-tap segmented status filter (All / Installed /
  Available / Needs repair). Every selection control is a one-tap button
  (release, route, settings, presets) — no dropdowns anywhere.
- Per-game **install route choice**: FSR 3.1 drop-in, OptiScaler, or experimental
  DLSS loader (validated; route switching handled, OptiScaler preference kept).
- **Install / Remove** per game, **Install/Remove all** (visible subset; on
  1.9.0+ it only lists games the drop-in can install into, others get a skip
  note), **Migrate** every outdated game to the active build in one background
  pass with per-game results.
- Inline per-game **Settings** editor for `helixsr.ini` (Sharpener, Sharpness
  slider, game-slider toggle, WaveSize) with named **presets** (save/apply/delete).
- Inline per-game **Log** viewer (`helixsr.log` tails) with one-tap fixes for
  known patterns (e.g. wave32 → WaveSize 64, re-verified before writing).
- Per-game **Details**: folder, detected inputs, FSR DLLs, exe folder, installed
  builds. **Verify installs** detects Steam-update breakage (markers/backups,
  not just live files) with one-tap **Repair** (refreshes the game's backup).
- **No forced rescan**: install/uninstall results update the games list
  immediately from the persisted scan; only **Scan games** re-walks the
  libraries. Groups auto-open when they're the only non-empty one.
- **Folders**: add non-Steam game folders by path (validated, persisted).

## HelixSR Scaler

- For DLSS/XeSS/FSR2-only games (no FSR 3.1): OptiScaler with a built HelixSR
  inside. Its own releases feed (Linux tarballs) with prerelease support;
  per-version disk use shown. Download (~132 MB) with the same progress bar,
  speed/ETA, cancel and incomplete-detection as version downloads.
- Per-game install with a two-tap anti-cheat/online warning, loader choice
  handled (dxgi.dll needs no Steam launch options, other loaders get a copy
  button for `WINEDLLOVERRIDES`). Uses the release's own installer on 1.9.3+
  (loader choice, manifest + remover), the plugin's own flow on 1.9.0–1.9.2.
- Per-game **Scaler settings** editor (`OptiScaler.ini [HelixSR]`: Int8,
  WaveSize, FrameGeneration, QuickFlow) and its `OptiScaler.log` in the
  per-game Log viewer. Installs made by HelixSR itself are recognized,
  verified and uninstallable too.

## Storage

- Per-version disk usage (mtime-cached), total, remove-all-except-active
  (refuses versions games still use, naming them), clear cached SDK DLLs with
  freed-MB reporting, and full **factory reset** (uninstalls everywhere, wipes
  versions/caches/settings, keeps ini presets — all reported, two-tap confirm).

## Health

- One screen: GitHub reachability (TCP probe, no API quota), version health
  (DLL present + network built), game verification, disk space on both
  filesystems, plugin update state.

## Plugin update (self-update)

- Automatic check on panel open (6h cache) + manual **Check for updates**.
- One-tap update through Decky's own installer: native consent popup,
  privileged install (works for root-owned setups), hot-reload — no Developer
  settings trip, no restart. Falls back to in-place swap or a validated manual
  zip where Decky's installer is unreachable; the download phase is
  cancellable. Refuses while tasks run; legacy old-name installs block with
  guidance instead of duplicating.

## Log & navigation

- Single Log section under the hero; paged viewer (newest auto-scroll, **Show
  older** buttons) readable with gamepad only, no touch scrolling, no sideways
  overflow. Monospace, anywhere-wrapping.
- Destructive actions use two-tap confirm-arming (15s expiry). Disabled buttons
  explain why. Long ops toast on completion even with the panel closed, and
  resume polling when reopened. In-progress backend work is killed, never
  orphaned, on plugin unload/reload.
