import { describe, it, expect, vi } from "vitest";
import { renderWithLocale } from "@/test/render";
import { BuildStamp } from "@/components/system/BuildStamp";

vi.mock("@/lib/build-info", () => ({
  BUILD_COMMIT_HASH: "",
  BUILD_COMMIT_DATE: "",
}));

describe("BuildStamp (non-git fallback)", () => {
  it("renders nothing when build info is empty", () => {
    const { container } = renderWithLocale(<BuildStamp />);
    expect(container).toBeEmptyDOMElement();
  });
});
