import { callable } from "@decky/api";

// Frontend<->backend RPC, typed to Plugin methods in main.py (HelixSR 2.0+ only).
// Games come from the release's own bridge (game_json + scan index) and are
// addressed by folder (stable across rescans).
export interface HelixRelease {
  tag: string;
  name: string;
  published_at: string;
  zip_name: string;
  zip_url: string;
  size: number;
  body: string;
}

export interface HelixVersion {
  tag: string;
  dir: string;
  size_mb: number;
  dll_version: string;
  network_built: boolean;
  fg_built: boolean;
  fg_reason: string;
  scaler_version: string | null;
}

export interface HelixState {
  active_tag: string;
  versions: HelixVersion[];
  root: string;
  plugin_version: string;
  update_pending: string;
  extra_folders: string[];
}

export interface HelixGame {
  index: number;
  id: string;
  name: string;
  folder: string;
  inputs: string[];
  inputs_found: boolean;
  ways: string[];
  recommended: string | null;
  anticheat: string[];
  installed: string | null;
  version: string | null;
  settings_path: string | null;
  log_path: string | null;
  supported: boolean;
  blocked: string | null;
  exe_folder: string | null;
  scaler_version: string | null;
  scaler_dll: string | null;
  scaler_layout: string | null;
  launch_options: string | null;
  frame_generation: boolean | null;
  fg_possible: boolean;
  fg_other: boolean;
  fg_installed: boolean;
  fg_unsupported: boolean;
  scaler_missing: string[];
  scaler_seen: string | null;
  previous: unknown | null;
  game_fg: string[];
}

export interface DownloadStatus {
  running: boolean;
  done: boolean;
  ok: boolean;
  cancelled: boolean;
  error: string;
  tag: string;
  downloaded: number;
  total: number;
  dir: string;
  network_built: boolean;
}

export interface ScanStatus {
  running: boolean;
  done: boolean;
  ok: boolean;
  cancelled: boolean;
  error: string;
  games: HelixGame[];
  tag: string;
  log: string;
}

export interface ScanCache {
  ok: boolean;
  games: HelixGame[];
  tag: string;
  skipped: number;
  scanned_at: number;
}

export interface InstallItem {
  folder: string;
  way: string;
  fg?: boolean;
  anticheat_ok?: boolean;
}

export interface InstallResult {
  folder: string;
  ok: boolean;
  detail: string;
  installed?: string | null;
  version?: string | null;
}

export interface VerifyResult {
  folder: string;
  name: string;
  route: string | null;
  installed: boolean;
  ok: boolean;
  problems: string[];
  version: string | null;
}

export interface SettingsResult {
  ok: boolean;
  path?: string;
  values?: Record<string, string>;
  schema?: string;
  other?: Record<string, string>;
  error?: string;
}

export interface PresetsResult {
  ok: boolean;
  presets: Record<string, Record<string, string | null>>;
  defaults_helixsr?: Record<string, string>;
  defaults_scaler?: Record<string, string>;
}

export interface LogResult {
  ok: boolean;
  files?: { path: string; lines: number; tail: string[] | string }[];
  important?: string[];
  report?: string;
  fixes?: { id: string; label: string }[];
  error?: string;
}

export interface ReadinessCheck {
  name: string;
  ok: boolean;
  detail: string;
}

export interface HealthCheck {
  name: string;
  ok: boolean;
  detail: string;
}

export interface PluginUpdateInfo {
  ok: boolean;
  current?: string;
  update_available?: boolean;
  latest?: string;
  url?: string;
  size?: number;
  body?: string;
  error?: string;
}

export interface PluginUpdateStatus {
  running: boolean;
  done: boolean;
  ok: boolean;
  error: string;
  version: string;
  url: string;
  downloaded: number;
  total: number;
  mode: string;
  path: string;
}

export const apiGetReleases = callable<[include_prereleases?: boolean], { releases: HelixRelease[]; error: string }>("get_releases");
export const apiGetState = callable<[], HelixState>("get_state");
export const apiSetActiveVersion = callable<[tag: string], HelixState>("set_active_version");
export const apiRemoveVersion = callable<[tag: string], { ok: boolean; error?: string; freed_mb?: number }>("remove_version");
export const apiStartDownload = callable<[tag: string, zip_url: string, size?: number], { ok: boolean; error?: string }>("start_download");
export const apiCancelDownload = callable<[], { ok: boolean; error?: string }>("cancel_download");
export const apiGetDownloadStatus = callable<[], DownloadStatus>("get_download_status");
export const apiStartScan = callable<[], { ok: boolean; error?: string }>("start_scan");
export const apiCancelScan = callable<[], { ok: boolean; error?: string }>("cancel_scan");
export const apiGetScanStatus = callable<[], ScanStatus>("get_scan_status");
export const apiGetLastScan = callable<[], ScanCache>("get_last_scan");
export const apiInstallGames = callable<[items: InstallItem[]], { ok: boolean; results: InstallResult[]; error?: string }>("install_games");
export const apiUninstallGames = callable<[folders: string[]], { ok: boolean; results: InstallResult[]; error?: string }>("uninstall_games");
export const apiRepairGames = callable<[folders: string[]], { ok: boolean; results: InstallResult[]; error?: string }>("repair_games");
export const apiVerifyInstalls = callable<[], { ok: boolean; games: VerifyResult[]; error?: string }>("verify_installs");
export const apiGetGameSettings = callable<[folder: string], SettingsResult>("get_game_settings");
export const apiSetGameSettings = callable<[folder: string, values: Record<string, string | null>], { ok: boolean; path?: string; output?: string; error?: string }>("set_game_settings");
export const apiResetGameSettings = callable<[folder: string], { ok: boolean; output?: string; error?: string }>("reset_game_settings");
export const apiGetIniPresets = callable<[], PresetsResult>("get_ini_presets");
export const apiSaveIniPreset = callable<[name: string, values: Record<string, string | null>], { ok: boolean; presets?: PresetsResult["presets"]; overwrote?: boolean; error?: string }>("save_ini_preset");
export const apiDeleteIniPreset = callable<[name: string], { ok: boolean; presets?: PresetsResult["presets"] }>("delete_ini_preset");
export const apiApplyIniPreset = callable<[folder: string, name: string], { ok: boolean; output?: string; error?: string }>("apply_ini_preset");
export const apiGetGameLog = callable<[folder: string], LogResult>("get_game_log");
export const apiApplyLogFix = callable<[folder: string, fix_id: string], { ok: boolean; output?: string }>("apply_log_fix");
export const apiSetScalerFg = callable<[folder: string, on: boolean], { ok: boolean; output?: string; error?: string }>("set_scaler_fg");
export const apiRollbackGame = callable<[folder: string], { ok: boolean; output?: string; error?: string }>("rollback_game");
export const apiStartBuild = callable<[rebuild: boolean, fg?: boolean], { ok: boolean; log?: string; error?: string }>("start_build");
export const apiCancelBuild = callable<[], { ok: boolean; error?: string }>("cancel_build");
export const apiGetBuildStatus = callable<[], { running: boolean; log: string; log_path: string; network_built: boolean; fg_built?: boolean }>("get_build_status");
export const apiCheckBuildReady = callable<[tag: string | null, fg?: boolean], { ok: boolean; error?: string; checks: ReadinessCheck[] }>("check_build_ready");
export const apiRemoveOldVersions = callable<[], { ok: boolean; removed: string[]; skipped: string[]; freed_mb: number }>("remove_old_versions");
export const apiFactoryReset = callable<[], { ok: boolean; games_uninstalled?: string[]; games_failed?: string[]; versions_removed?: string[]; error?: string }>("factory_reset");
export const apiAddScanFolder = callable<[path: string], { ok: boolean; folders?: string[]; error?: string }>("add_scan_folder");
export const apiRemoveScanFolder = callable<[path: string], { ok: boolean; folders?: string[] }>("remove_scan_folder");
export const apiCheckPluginUpdate = callable<[force: boolean], PluginUpdateInfo>("check_plugin_update");
export const apiStartPluginUpdate = callable<[], { ok: boolean; version?: string; error?: string }>("start_plugin_update");
export const apiGetPluginUpdateStatus = callable<[], PluginUpdateStatus>("get_plugin_update_status");
export const apiCancelPluginUpdate = callable<[], { ok: boolean; error?: string }>("cancel_plugin_update");
export const apiHealthCheck = callable<[], { ok: boolean; checks: HealthCheck[]; error?: string }>("health_check");
