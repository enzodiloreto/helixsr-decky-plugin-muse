import { callable } from "@decky/api";

// All frontend<->backend RPC typed to Python methods on Plugin in main.py.
// Keep names and signatures in sync with main.py.
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
  network_built: boolean;
  dll_version: string;
  size_mb: number;
}

export interface HelixState {
  active_tag: string;
  versions: HelixVersion[];
  root: string;
  plugin_version: string;
  update_pending: string;
  extra_folders: string[];
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
}

export interface HelixGame {
  index: number;
  name: string;
  folder: string;
  route: string;
  available: string[];
  inputs: string[];
  installed: boolean;
  status: string;
  installed_versions: string[];
}

export interface GameSettings {
  Sharpener: string;
  Sharpness: string;
  UseGameSlider: string;
  WaveSize: string;
}

export interface DownloadStatus {
  running: boolean;
  done: boolean;
  ok: boolean;
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
  skipped: number;
}

export interface ScanCache {
  ok: boolean;
  games: HelixGame[];
  tag: string;
  skipped: number;
  scanned_at: number;
}

export interface ReadinessCheck {
  name: string;
  ok: boolean;
  detail: string;
}

export interface MigrationGameResult {
  name: string;
  ok: boolean;
  detail: string;
}

export interface MigrationStatus {
  running: boolean;
  done: boolean;
  ok: boolean;
  error: string;
  tag: string;
  total: number;
  current: number;
  current_name: string;
  results: MigrationGameResult[];
}

export const apiGetReleases = callable<[], { releases: HelixRelease[]; error: string }>("get_releases");
export const apiGetState = callable<[], HelixState>("get_state");
export const apiSetActiveVersion = callable<[tag: string], HelixState>("set_active_version");
export const apiRemoveVersion = callable<[tag: string], { ok: boolean; error?: string }>("remove_version");
export const apiStartDownload = callable<[tag: string, zip_url: string], { ok: boolean; error?: string }>("start_download");
export const apiGetDownloadStatus = callable<[], DownloadStatus>("get_download_status");
export const apiStartScan = callable<[], { ok: boolean; error?: string }>("start_scan");
export const apiCancelScan = callable<[], { ok: boolean; error?: string }>("cancel_scan");
export const apiGetScanStatus = callable<[], ScanStatus>("get_scan_status");
export const apiGetLastScan = callable<[], ScanCache>("get_last_scan");
export const apiInstallGames = callable<[indices: number[], route: string], { ok: boolean; output: string; rc?: number }>("install_games");
export const apiGetGameSettings = callable<[index: number], { ok: boolean; path?: string; values?: GameSettings; note?: string; error?: string }>("get_game_settings");
export const apiSetGameSettings = callable<[index: number, values: GameSettings], { ok: boolean; output?: string; path?: string }>("set_game_settings");
export const apiUninstallGames = callable<[indices: number[]], { ok: boolean; output: string; rc?: number }>("uninstall_games");
export const apiStartBuild = callable<[rebuild: boolean], { ok: boolean; log?: string; error?: string }>("start_build");
export const apiCheckBuildReady = callable<[tag: string | null], { ok: boolean; error?: string; checks: ReadinessCheck[] }>("check_build_ready");
export const apiStartMigration = callable<[tag: string], { ok: boolean; error?: string }>("start_migration");
export const apiGetMigrationStatus = callable<[], MigrationStatus>("get_migration_status");
export const apiRemoveOldVersions = callable<[], { ok: boolean; removed: string[]; skipped: string[]; freed_mb: number }>("remove_old_versions");
export const apiClearDlssCache = callable<[], { ok: boolean; cleared: string[]; freed_mb: number }>("clear_dlss_cache");
export const apiAddScanFolder = callable<[path: string], { ok: boolean; folders?: string[]; error?: string }>("add_scan_folder");
export const apiRemoveScanFolder = callable<[path: string], { ok: boolean; folders?: string[] }>("remove_scan_folder");
export const apiCheckPluginUpdate = callable<[force: boolean], PluginUpdateInfo>("check_plugin_update");
export const apiStartPluginUpdate = callable<[], { ok: boolean; version?: string; error?: string }>("start_plugin_update");
export const apiGetPluginUpdateStatus = callable<[], PluginUpdateStatus>("get_plugin_update_status");
export const apiGetBuildStatus = callable<[], { running: boolean; log: string; log_path: string; network_built: boolean }>("get_build_status");
