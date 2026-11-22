import { describe, expect, it } from "vitest";
import path from "node:path";
import { readFileSync } from "node:fs";
import vm from "node:vm";

// Registers the service worker's routes against a stubbed workbox and returns
// them in registration order, so a matcher can be exercised the way workbox
// does: first match wins.
const SW_SCRIPT_PATH = path.resolve(__dirname, "../../../../public/sw.js");

interface RegisteredRoute {
  matcher: (input: { url: URL; request?: { mode?: string } }) => boolean;
  strategy: string;
  cacheName: string | undefined;
}

interface BuiltStrategy {
  __strategy: string;
  __cacheName: string | undefined;
}

function isBuiltStrategy(value: unknown): value is BuiltStrategy {
  return (
    typeof value === "object" &&
    value !== null &&
    "__strategy" in value &&
    typeof value.__strategy === "string"
  );
}

function loadRoutes(): RegisteredRoute[] {
  const routes: RegisteredRoute[] = [];
  const script = readFileSync(SW_SCRIPT_PATH, "utf8");

  const strategyCtor = (name: string) =>
    new Proxy(function strategy() {} as unknown as object, {
      construct(_target, args) {
        const options = (args[0] ?? {}) as { cacheName?: string };
        const built: BuiltStrategy = { __strategy: name, __cacheName: options.cacheName };
        return built;
      },
    });

  const workbox = {
    setConfig: () => {},
    core: { skipWaiting: () => {}, clientsClaim: () => {} },
    routing: {
      setCatchHandler: () => {},
      registerRoute: (matcher: RegisteredRoute["matcher"], strategy: unknown) => {
        if (!isBuiltStrategy(strategy)) throw new Error("route registered without a strategy");
        routes.push({ matcher, strategy: strategy.__strategy, cacheName: strategy.__cacheName });
      },
    },
    strategies: {
      NetworkOnly: strategyCtor("NetworkOnly"),
      NetworkFirst: strategyCtor("NetworkFirst"),
      StaleWhileRevalidate: strategyCtor("StaleWhileRevalidate"),
      CacheFirst: strategyCtor("CacheFirst"),
    },
    expiration: { ExpirationPlugin: class {} },
    cacheableResponse: { CacheableResponsePlugin: class {} },
  };

  vm.runInNewContext(
    script,
    {
      workbox,
      importScripts: () => {},
      self: {},
      clients: { claim: async () => {} },
      console,
      fetch: async () => new Response(""),
      URL,
      Response,
    },
    { filename: SW_SCRIPT_PATH }
  );
  return routes;
}

function firstMatch(routes: RegisteredRoute[], pathname: string) {
  return routes.find((route) =>
    route.matcher({ url: new URL(`https://app.test${pathname}`), request: {} })
  );
}

describe("service worker API route matching", () => {
  // `/api/songsets…` starts with `/api/songs`, and workbox takes the first
  // matching route. The catalog route uses stale-while-revalidate, so letting
  // it claim songset reads served every editor reload one mutation stale: a
  // take cleared by a PATCH still read as selected on the next GET.
  it("routes songset reads to the network-first songset route", () => {
    const routes = loadRoutes();
    const matched = firstMatch(routes, "/api/songsets/2ZV5SHjAzw7ceJE-nREs2");
    expect(matched?.strategy).toBe("NetworkFirst");
    expect(matched?.cacheName).toBe("sow-api-songsets");
  });

  it("keeps the catalog route for catalog reads", () => {
    const routes = loadRoutes();
    const matched = firstMatch(routes, "/api/songs");
    expect(matched?.strategy).toBe("StaleWhileRevalidate");
    expect(matched?.cacheName).toBe("sow-api-songs");
  });

  it("still routes nested catalog reads to the catalog route", () => {
    const routes = loadRoutes();
    expect(firstMatch(routes, "/api/songs/abc")?.cacheName).toBe("sow-api-songs");
  });
});
