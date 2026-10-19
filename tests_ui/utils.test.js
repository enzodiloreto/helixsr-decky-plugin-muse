/** Frontend pure-function tests (node:test, no DOM).
 *
 *  Run:  pnpm run test:ui   (compiles src/utils.ts, then node --test)
 */
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import {
  base,
  cmpTags,
  fmtAge,
  fmtSize,
  parseBuildProgress,
  ROUTE_LABELS,
  ROUTE_SHORT,
  shortRoute,
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
    for (const r of ["direct", "optiscaler", "dlss", "unsupported", "scaler", "blocked"]) {
      assert.ok(ROUTE_LABELS[r].length > 0);
      assert.ok(ROUTE_SHORT[r].length > 0);
      assert.ok(shortRoute(r).length > 0);
    }
    assert.equal(shortRoute("mystery"), "mystery");
  });
});
