/** Frontend pure-function tests (node:test, no DOM).
 *
 *  Run:  pnpm run test:ui   (compiles src/utils.ts, then node --test)
 */
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import {
  base,
  cmpTags,
  filterGames,
  fmtAge,
  fmtSize,
  installWayFor,
  isWaySwitch,
  panelStep,
  parseBuildProgress,
  ROUTE_LABELS,
  ROUTE_SHORT,
  shortRoute,
  waysFor,
} from "../.uitest/utils.js";

describe("fmtSize", () => {
  it("formats megabytes with one decimal", () => {
    assert.equal(fmtSize(55766339), "55.8 MB");
    assert.equal(fmtSize(0), "0.0 MB");
    assert.equal(fmtSize(1500), "0.0 MB");
  });
});

describe("base", () => {
  it("takes posix and windows basenames", () => {
    assert.equal(base("/a/b/c.dll"), "c.dll");
    assert.equal(base("C:\\a\\b.exe"), "b.exe");
    assert.equal(base("plain"), "plain");
    assert.equal(base(""), "");
  });
});

describe("fmtAge", () => {
  it("buckets recency", () => {
    const now = Date.now() / 1000;
    assert.equal(fmtAge(0), "some time ago");
    assert.equal(fmtAge(now - 10), "just now");
    assert.equal(fmtAge(now - 89), "just now");
    assert.equal(fmtAge(now - 90), "1 min ago");
    assert.equal(fmtAge(now - 45 * 60), "45 min ago");
    assert.equal(fmtAge(now - 90 * 60), "1 h ago");
    assert.equal(fmtAge(now - 30 * 3600), "30 h ago");
    assert.equal(fmtAge(now - 48 * 3600), "2 d ago");
  });
});

describe("cmpTags", () => {
  it("compares numerically, ignoring leading v", () => {
    assert.equal(cmpTags("v1.9.0", "v1.8.1"), 1);
    assert.equal(cmpTags("v1.8.1", "v1.9.0"), -1);
    assert.equal(cmpTags("v0.0.10", "v0.0.10"), 0);
    assert.equal(cmpTags("v0.0.9", "v0.0.10"), -1); // numeric, not lexicographic
    assert.equal(cmpTags("1.2", "1.2.0"), 0);
    assert.equal(cmpTags("v2.0", "v10.0"), -1);
    assert.equal(cmpTags("v1.8.1", "1.8.1"), 0);
  });
});

describe("parseBuildProgress", () => {
  it("parses the reference bar format", () => {
    const line = "[helixsr-setup] [##########----------]   50%  3:12  shaders 15/40";
    assert.deepEqual(parseBuildProgress(line), { pct: 50, label: "3:12 — shaders 15/40" });
  });
  it("takes the last bar and clamps", () => {
    const log = "[helixsr-setup] [--------------------]    0%  0:00  weights\n"
      + "[helixsr-setup] [####################]  142%  9:59  done\n";
    assert.deepEqual(parseBuildProgress(log), { pct: 100, label: "9:59 — done" });
  });
  it("returns null without a bar", () => {
    assert.equal(parseBuildProgress("downloading 12 MB ...\nno bars here\n"), null);
    assert.equal(parseBuildProgress(""), null);
  });
});

describe("routes", () => {
  it("labels every known route short and long", () => {
    for (const r of ["auto", "scaler", "fsr", "blocked"]) {
      assert.ok(ROUTE_LABELS[r].length > 0);
      assert.ok(ROUTE_SHORT[r].length > 0);
      assert.ok(shortRoute(r).length > 0);
    }
    assert.equal(shortRoute("mystery"), "mystery");
  });
});

describe("installWayFor", () => {
  it("uses the way the user picked", () => {
    assert.equal(installWayFor("scaler", null), "scaler");
    assert.equal(installWayFor("fsr", "scaler"), "fsr");
    assert.equal(installWayFor("fsr", "fsr"), "fsr");
  });
  it("keeps the installed way when nothing is picked", () => {
    assert.equal(installWayFor("auto", "scaler"), "scaler");
    assert.equal(installWayFor(null, "fsr"), "fsr");
    assert.equal(installWayFor("auto", null), "auto");
  });
  it("never picks a way the game does not allow", () => {
    // an anti-cheat game: only the FSR drop-in
    assert.equal(installWayFor("scaler", null, ["fsr"]), "auto");
    assert.equal(installWayFor("auto", "scaler", ["fsr"]), "auto");
    assert.equal(installWayFor("fsr", "scaler", ["fsr"]), "fsr");
  });
});

describe("panelStep", () => {
  it("walks download, build, scan, install", () => {
    assert.deepEqual(panelStep(0, false, 0), { index: 0, action: "download" });
    assert.deepEqual(panelStep(1, false, 0), { index: 1, action: "build" });
    assert.deepEqual(panelStep(1, true, 0), { index: 2, action: "scan" });
    assert.deepEqual(panelStep(1, true, 3), { index: 3, action: "none" });
  });
  it("does not go back a step when a scan finds games", () => {
    // a version that is downloaded but whose network is not built yet
    assert.deepEqual(panelStep(1, false, 5), { index: 1, action: "build" });
  });
});

describe("filterGames", () => {
  const games = [
    { folder: "/g/Alpha", name: "Alpha", installed: "scaler" },
    { folder: "/g/Beta", name: "Beta", installed: null },
    { folder: "/g/Gamma", name: "Gamma", installed: "fsr" },
  ];
  it("keeps everything for 'all'", () => {
    assert.equal(filterGames(games, "all", "").length, 3);
  });
  it("filters by the install state", () => {
    assert.deepEqual(filterGames(games, "installed", "").map((g) => g.name), ["Alpha", "Gamma"]);
    assert.deepEqual(filterGames(games, "notinstalled", "").map((g) => g.name), ["Beta"]);
  });
  it("filters by the broken folders", () => {
    assert.deepEqual(filterGames(games, "repair", "", ["/g/Beta"]).map((g) => g.name), ["Beta"]);
    assert.equal(filterGames(games, "repair", "").length, 0);
  });
  it("searches the name and the folder", () => {
    assert.deepEqual(filterGames(games, "all", "gam").map((g) => g.name), ["Gamma"]);
    assert.deepEqual(filterGames(games, "all", "/g/beta").map((g) => g.name), ["Beta"]);
    assert.equal(filterGames(games, "all", "nothing").length, 0);
  });
  it("combines the filter and the search", () => {
    assert.deepEqual(filterGames(games, "installed", "a").map((g) => g.name), ["Alpha", "Gamma"]);
  });
});

describe("waysFor", () => {
  it("offers both ways for a normal game", () => {
    assert.deepEqual(waysFor({ anticheat: [], ways: ["scaler", "fsr"] }), ["scaler", "fsr"]);
    assert.deepEqual(waysFor({ anticheat: [], ways: ["scaler"] }), ["scaler"]);
  });
  it("offers only the FSR drop-in in an anti-cheat game", () => {
    assert.deepEqual(waysFor({ anticheat: ["EasyAntiCheat"], ways: ["scaler", "fsr"] }), ["fsr"]);
  });
  it("offers nothing in an anti-cheat game without FSR 3.1", () => {
    assert.deepEqual(waysFor({ anticheat: ["BattlEye"], ways: ["scaler"] }), []);
  });
});

describe("isWaySwitch", () => {
  it("is true when the picked way is not the installed one", () => {
    assert.equal(isWaySwitch("fsr", "scaler"), true);
    assert.equal(isWaySwitch("scaler", "fsr"), true);
  });
  it("is false for the same way, for auto and for a game with no install", () => {
    assert.equal(isWaySwitch("scaler", "scaler"), false);
    assert.equal(isWaySwitch("auto", "scaler"), false);
    assert.equal(isWaySwitch("fsr", null), false);
    assert.equal(isWaySwitch(null, "scaler"), false);
  });
});
