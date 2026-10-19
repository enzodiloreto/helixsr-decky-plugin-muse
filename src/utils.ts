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

/** The way an install action uses: the way the user picked, or the way the
 * game already has when nothing is picked (an update keeps that way). */
export function installWayFor(picked?: string | null, installed?: string | null): string {
  const p = picked || "auto";
  if (p !== "auto") return p;
  return installed || "auto";
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
