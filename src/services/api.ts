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
}

export interface HelixGame {
  index: number;
  name: string;
  status: string;
  installed: boolean;
}

export const apiGetReleases = callable<[], HelixRelease[]>("get_releases");
export const apiGetState = callable<[], HelixState>("get_state");
export const apiSetActiveVersion = callable<[tag: string], HelixState>("set_active_version");
export const apiDownloadVersion = callable<[tag: string, zip_url: string], { ok: boolean; dir?: string; network_built?: boolean; error?: string }>("download_version");
export const apiRemoveVersion = callable<[tag: string], { ok: boolean; error?: string }>("remove_version");
export const apiListGames = callable<[], { ok: boolean; games: HelixGame[]; tag?: string; error?: string }>("list_games");
export const apiInstallGames = callable<[indices: number[]], { ok: boolean; output: string; rc?: number }>("install_games");
export const apiUninstallGames = callable<[indices: number[]], { ok: boolean; output: string; rc?: number }>("uninstall_games");
export const apiStartBuild = callable<[], { ok: boolean; log?: string; error?: string }>("start_build");
export const apiGetBuildStatus = callable<[], { running: boolean; log: string; network_built: boolean }>("get_build_status");
