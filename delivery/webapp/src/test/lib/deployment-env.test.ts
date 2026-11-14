import { afterEach, describe, expect, it, vi } from "vitest";
import { getDeploymentEnv } from "@/lib/deployment-env";

const ENV_VAR = "NEXT_PUBLIC_VERCEL_ENV";

describe("getDeploymentEnv", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it.each([
    ["production", "production"],
    ["preview", "preview"],
    ["development", "development"],
    ["", "development"],
    ["staging", "development"],
    ["PRODUCTION", "development"],
  ])("maps NEXT_PUBLIC_VERCEL_ENV=%o to %o", (raw, expected) => {
    vi.stubEnv(ENV_VAR, raw);
    expect(getDeploymentEnv()).toBe(expected);
  });

  it("treats an absent variable as development", () => {
    delete process.env[ENV_VAR];
    expect(getDeploymentEnv()).toBe("development");
  });
});
