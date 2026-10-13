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
}

export interface HelixVersion {
  tag: string;
  dir: string;
  network_built: boolean;
  dll_version: string;
}

export interface HelixState {
  active_tag: string;
  versions: HelixVersion[];
  root: string;
  plugin_version: string;
}

export interface HelixGame {
  index: number;
  name: string;
  folder: string;
  route: string;
  inputs: string[];
  installed: boolean;
  status: string;
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

export const apiGetReleases = callable<[], { releases: HelixRelease[]; error: string }>("get_releases");
export const apiGetState = callable<[], HelixState>("get_state");
export const apiSetActiveVersion = callable<[tag: string], HelixState>("set_active_version");
export const apiRemoveVersion = callable<[tag: string], { ok: boolean; error?: string }>("remove_version");
export const apiStartDownload = callable<[tag: string, zip_url: string], { ok: boolean; error?: string }>("start_download");
export const apiGetDownloadStatus = callable<[], DownloadStatus>("get_download_status");
export const apiStartScan = callable<[], { ok: boolean; error?: string }>("start_scan");
export const apiCancelScan = callable<[], { ok: boolean; error?: string }>("cancel_scan");
export const apiGetScanStatus = callable<[], ScanStatus>("get_scan_status");
export const apiInstallGames = callable<[indices: number[]], { ok: boolean; output: string; rc?: number }>("install_games");
export const apiUninstallGames = callable<[indices: number[]], { ok: boolean; output: string; rc?: number }>("uninstall_games");
export const apiStartBuild = callable<[rebuild: boolean], { ok: boolean; log?: string; error?: string }>("start_build");
export const apiGetBuildStatus = callable<[], { running: boolean; log: string; log_path: string; network_built: boolean }>("get_build_status");
