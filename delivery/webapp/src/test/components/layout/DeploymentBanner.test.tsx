import { screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DeploymentBanner } from "@/components/layout/DeploymentBanner";
import { BUILD_COMMIT_HASH } from "@/lib/build-info";
import { renderWithLocale } from "@/test/render";

const mockPathname = vi.hoisted(() => vi.fn(() => "/songsets"));

vi.mock("next/navigation", () => ({
  usePathname: mockPathname,
}));

const ENV_VAR = "NEXT_PUBLIC_VERCEL_ENV";

describe("DeploymentBanner", () => {
  beforeEach(() => {
    mockPathname.mockReturnValue("/songsets");
  });

  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("renders nothing in production", () => {
    vi.stubEnv(ENV_VAR, "production");
    renderWithLocale(<DeploymentBanner />);

    expect(screen.queryByTestId("deployment-banner")).toBeNull();
  });

  it("shows the preview label with the build commit", () => {
    vi.stubEnv(ENV_VAR, "preview");
    renderWithLocale(<DeploymentBanner />);

    const banner = screen.getByTestId("deployment-banner");
    expect(banner).toHaveAttribute("data-deployment", "preview");
    expect(banner).toHaveTextContent("Preview — non-production");
    if (BUILD_COMMIT_HASH) {
      expect(banner).toHaveTextContent(BUILD_COMMIT_HASH);
    }
  });

  it.each(["/songsets/abc/play/projection", "/share/tok/play/projection"])(
    "hides on the projection route %s",
    (pathname) => {
      vi.stubEnv(ENV_VAR, "preview");
      mockPathname.mockReturnValue(pathname);
      renderWithLocale(<DeploymentBanner />);

      expect(screen.queryByTestId("deployment-banner")).toBeNull();
    }
  );

  it("shows the development label when the variable is unset", () => {
    vi.stubEnv(ENV_VAR, "");
    renderWithLocale(<DeploymentBanner />);

    const banner = screen.getByTestId("deployment-banner");
    expect(banner).toHaveAttribute("data-deployment", "development");
    expect(banner).toHaveTextContent("Development — non-production");
  });

  it("localizes the label to zh-Hant", () => {
    vi.stubEnv(ENV_VAR, "preview");
    renderWithLocale(<DeploymentBanner />, "zh-Hant");

    expect(screen.getByTestId("deployment-banner")).toHaveTextContent("預覽版本 — 非正式環境");
  });
});
