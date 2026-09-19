// @vitest-environment node
import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, it, expect } from "vitest";

const SW_SCRIPT_PATH = path.resolve(__dirname, "../../../../public/sw.js");

/**
 * The static-assets route must never cache /_next/* chunks into
 * sow-static-assets. In dev those URLs are stable across builds while their
 * contents change, and the dev server stamps them
 * `Cache-Control: public, max-age=31536000, immutable` — so
 * StaleWhileRevalidate's background revalidate fetch resolves from the
 * browser HTTP cache with the SAME stale body and cache.put re-stores it,
 * permanently poisoning the cache (issue: /offline endless request loop).
 *
 * sw.js is a plain script with no exports, so (matching the file-text
 * assertions in artifact-cache-sw-parity.test.ts) this pins the matcher's
 * text.
 */
describe("service worker static-assets route", () => {
  it("excludes /_next/ chunks from the sow-static-assets route", () => {
    const script = readFileSync(SW_SCRIPT_PATH, "utf8");

    // Isolate the static-assets route's registration: from its marker
    // comment to the next route comment (the /api/songs route).
    const start = script.indexOf(
      "// Cache static assets (JS, CSS, fonts, images)"
    );
    expect(start, "sw.js must keep the static-assets route").toBeGreaterThan(-1);

    const block = script.slice(start, script.indexOf("// Runtime caching for song catalog API"));

    expect(
      block.includes('!url.pathname.startsWith("/_next/")'),
      "the static-assets route matcher must exclude /_next/ URLs (stable-URL mutable dev chunks + Cache-Control: immutable poison sow-static-assets)"
    ).toBe(true);

    // The matcher must still narrow by destination — the exclusion must not
    // have broadened the route to all requests.
    expect(block).toContain('request.destination === "script"');
    expect(block).toContain('!url.pathname.startsWith("/_next/")');
  });

  it("registers sow-static-assets exactly once", () => {
    const script = readFileSync(SW_SCRIPT_PATH, "utf8");

    const occurrences = script.split('cacheName: "sow-static-assets"').length - 1;
    expect(
      occurrences,
      "exactly one route may write to sow-static-assets"
    ).toBe(1);
  });
});
