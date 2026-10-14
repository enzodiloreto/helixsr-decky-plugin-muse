# HelixSR Muse Plugin — Feature Catalog

Every user-facing capability of the plugin (v0.0.30), grouped by panel section.
Backend: `main.py` + `py_modules/helixscan.py`. Frontend: `src/index.tsx` (+ api).

## Status hero

- One-line live summary: active version, network state, installed/found games,
  disk use.
- 4-step progress stepper (Download → Build → Scan → Install) with the current
  step highlighted and named.
- State chips: network built / NOT built, games count + scanned tag, total MB,
  update availability.
- **Restart needed** banner after a self-update (persists until Decky restarts).
- **Partial update** banner on UI/backend version skew, with in-panel
  **Reload plugin** (falls back to reinstall + restart advice).

## 1 · Version (HelixSR releases)

- Release list from the HelixSR GitHub API (newest first, zip assets only);
  failures surface the actual reason (network, rate limit, TLS) instead of an
  empty list. **Reload releases** retries without reopening the panel.
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

## 3 · Games

- **Scan games**: walks every Steam library (plus Heroic/Lutris and hand-added
  folders) for DLSS / XeSS / FSR titles using HelixSR's own detection logic.
  Backgrounded with progress, cancellable, per-folder failure isolation, results
  persisted across panel opens (with age note), reused for installs for 5 min.
- Collapsible Installed / Not-installed groups (both collapsed by default);
  text filter; status filter (All / Installed / Not installed / Needs repair).
- Per-game **install route choice**: FSR 3.1 drop-in, OptiScaler, or experimental
  DLSS loader (validated; route switching handled, OptiScaler preference kept).
- **Install / Remove** per game, **Install/Remove all** (visible subset),
  **Migrate** every outdated game to the active build in one background pass
  with per-game results.
- Inline per-game **Settings** editor for `helixsr.ini` (Sharpener, Sharpness
  slider, game-slider toggle, WaveSize) with named **presets** (save/apply/delete).
- Inline per-game **Log** viewer (`helixsr.log` tails) with one-tap fixes for
  known patterns (e.g. wave32 → WaveSize 64, re-verified before writing).
- Per-game **Details**: folder, detected inputs, FSR DLLs, exe folder, installed
  builds. **Verify installs** detects Steam-update breakage (markers/backups,
  not just live files) with one-tap **Repair** (refreshes the game's backup).
- **Folders**: add non-Steam game folders by path (validated, persisted).

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
  zip where Decky's installer is unreachable. Refuses while tasks run; legacy
  old-name installs block with guidance instead of duplicating.

## Log & navigation

- Single Log section under the hero; paged viewer (newest auto-scroll, **Show
  older** buttons) readable with gamepad only, no touch scrolling, no sideways
  overflow. Monospace, anywhere-wrapping.
- Destructive actions use two-tap confirm-arming (15s expiry). Disabled buttons
  explain why. Long ops toast on completion even with the panel closed, and
  resume polling when reopened. In-progress backend work is killed, never
  orphaned, on plugin unload/reload.
