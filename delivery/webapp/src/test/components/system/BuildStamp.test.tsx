import { describe, it, expect } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithLocale } from "@/test/render";
import { BUILD_COMMIT_DATE, BUILD_COMMIT_HASH } from "@/lib/build-info";
import { BuildStamp } from "@/components/system/BuildStamp";

describe("BuildStamp", () => {
  it("reveals commit hash and date on click, hides on second click", async () => {
    const user = userEvent.setup();
    renderWithLocale(<BuildStamp />);

    const button = screen.getByRole("button", {
      name: "and all the trees of the field shall clap their hands",
    });
    expect(button).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByTestId("build-stamp-details")).not.toBeInTheDocument();

    await user.click(button);
    expect(button).toHaveAttribute("aria-expanded", "true");
    const details = screen.getByTestId("build-stamp-details");
    expect(details.textContent).toContain(BUILD_COMMIT_HASH);
    expect(details.textContent).toContain(BUILD_COMMIT_DATE);

    await user.click(button);
    expect(screen.queryByTestId("build-stamp-details")).not.toBeInTheDocument();
  });

  it("shows zh-Hant label", () => {
    renderWithLocale(<BuildStamp />, "zh-Hant");
    expect(
      screen.getByRole("button", { name: "田野的樹木也都拍掌" })
    ).toBeInTheDocument();
  });
});
