// Pure UI helpers (no Decky/React imports): covered by tests_ui/ via node:test.
// Anything imported by index.tsx must stay side-effect free.

export function fmtSize(n: number) {
  return `${(n / 1e6).toFixed(1)} MB`;
}

export const base = (p: string) => p.split(/[/\\]/).pop() || p;

export function fmtAge(epochSec: number) {
  if (!epochSec) return "some time ago";
  const s = Math.max(0, Date.now() / 1000 - epochSec);
  if (s < 90) return "just now";
  const m = Math.floor(s / 60);
  if (m < 90) return `${m} min ago`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h} h ago`;
  return `${Math.floor(h / 24)} d ago`;
}

export const ROUTE_LABELS: Record<string, string> = {
  auto: "Recommended way",
  scaler: "HelixSR Scaler (Insert for menu)",
  fsr: "FSR drop-in (select AMD FSR)",
  blocked: "Anti-cheat — nothing installs here",
};

export const ROUTE_SHORT: Record<string, string> = {
  auto: "Recommended",
  scaler: "Scaler",
  fsr: "FSR drop-in",
  blocked: "Anti-cheat",
};

export function parseBuildProgress(log: string): { pct: number; label: string } | null {
  // Matches the reference setup's bar: [####----]  42%  3:12  shaders 15/40
  const re = /\[([#-]{20})\]\s*(\d+)%\s*(\d+:\d+)\s*(.*)/g;
  let m: RegExpExecArray | null = null;
  let last: RegExpExecArray | null = null;
  while ((m = re.exec(log)) !== null) last = m;
  if (!last) return null;
  return { pct: Math.min(100, parseInt(last[2], 10)), label: `${last[3]} — ${last[4]}` };
}

export function shortRoute(route: string): string {
  if (route === "auto") return "recommended way";
  if (route === "scaler") return "scaler: pick DLSS in-game, Insert for menu";
  if (route === "fsr") return "drop-in, pick AMD FSR in-game";
  if (route === "blocked") return "anti-cheat: nothing installs here";
  return route;
}

/** The step the panel is at, so the user always sees where they are:
 *  download a version, build the network, scan the games, install. */
export type PanelAction = "download" | "build" | "scan" | "none";
export function panelStep(versions: number, networkBuilt: boolean, games: number): { index: number; action: PanelAction } {
  if (!versions) return { index: 0, action: "download" };
  if (!networkBuilt) return { index: 1, action: "build" };
  if (!games) return { index: 2, action: "scan" };
  return { index: 3, action: "none" };
}

/** The game list's status filter. */
export type GameFilter = "all" | "installed" | "notinstalled" | "repair";

export interface FilterableGame {
  folder: string;
  name: string;
  installed?: string | null;
}

/** The games the list shows: the status filter and the text search (the
 *  name or the folder). `broken` holds the folders the last verify found
 *  damaged. */
export function filterGames<T extends FilterableGame>(
  games: T[],
  filter: GameFilter,
  query: string,
  broken: string[] = [],
): T[] {
  const bad = new Set(broken);
  const q = query.trim().toLowerCase();
  return games.filter((g) => {
    if (filter === "installed" && !g.installed) return false;
    if (filter === "notinstalled" && g.installed) return false;
    if (filter === "repair" && !bad.has(g.folder)) return false;
    if (q && !g.name.toLowerCase().includes(q) && !g.folder.toLowerCase().includes(q)) return false;
    return true;
  });
}

/** The ways a game can take. An anti-cheat game only gets the FSR drop-in:
 *  HelixSR Scaler is not used there (it can get the account banned). */
export function waysFor(game: { anticheat?: string[] | null; ways?: string[] | null }): string[] {
  const ways = game.ways || [];
  if ((game.anticheat || []).length) return ways.includes("fsr") ? ["fsr"] : [];
  return ways;
}

/** The way an install action uses: the way the user picked, or the way the
 * game already has when nothing is picked (an update keeps that way). */
export function installWayFor(picked?: string | null, installed?: string | null, allowed?: string[] | null): string {
  const p = picked || "auto";
  const ok = (w: string) => !allowed || allowed.length === 0 || allowed.includes(w);
  if (p !== "auto" && ok(p)) return p;
  if (installed && ok(installed)) return installed;
  return "auto";
}

/** Whether the picked way is another one than the installed way: the install
 * then switches the game (the bridge removes the other install first). */
export function isWaySwitch(picked?: string | null, installed?: string | null): boolean {
  return !!installed && !!picked && picked !== "auto" && picked !== installed;
}

export function cmpTags(a: string, b: string): number {
  // Numeric dotted comparison ignoring a leading "v": v1.9.0 > v1.8.1.
  const pa = a.replace(/^v/i, "").split(".").map((x) => parseInt(x, 10) || 0);
  const pb = b.replace(/^v/i, "").split(".").map((x) => parseInt(x, 10) || 0);
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
    if ((pa[i] || 0) !== (pb[i] || 0)) return (pa[i] || 0) < (pb[i] || 0) ? -1 : 1;
  }
  return 0;
}
