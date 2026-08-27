import { describe, it, expect } from "vitest";
import { BUILD_COMMIT_DATE, BUILD_COMMIT_HASH } from "@/lib/build-info";
import { screen, fireEvent, within } from "@testing-library/react";
import { renderWithLocale as render } from "@/test/render";
import { PublicLanding } from "@/app/page/PublicLanding";

describe("PublicLanding", () => {
  it("renders hero tagline, title, and description in en", () => {
    render(<PublicLanding locale="en" />);
    expect(
      screen.getByText((content, element) =>
        element?.textContent === "✦ Lyrics video for small group worship"
      )
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", {
        name: "Lead your small group in worship with no awkward interruptions.",
      })
    ).toBeInTheDocument();
    expect(
      screen.getByText(/strings your songs into one seamless set/)
    ).toBeInTheDocument();
  });

  it("renders hero strings in Traditional Chinese for zh-Hant", () => {
    render(<PublicLanding locale="zh-Hant" />);
    expect(
      screen.getByRole("heading", { name: "帶領小組敬拜，不再尷尬中斷。" })
    ).toBeInTheDocument();
    expect(
      screen.getByText((content, element) =>
        element?.textContent === "✦ 為小組敬拜而設的歌詞影片"
      )
    ).toBeInTheDocument();
  });

  it("renders the primary CTA to /register and secondary to /login", () => {
    render(<PublicLanding locale="en" />);
    expect(
      screen.getByRole("link", { name: "Get started free" })
    ).toHaveAttribute("href", "/register");
    const signInLinks = screen.getAllByRole("link", { name: "Sign in" });
    expect(signInLinks.length).toBeGreaterThan(0);
    expect(signInLinks[0]).toHaveAttribute("href", "/login");
  });

  it("renders all three feature cards", () => {
    render(<PublicLanding locale="en" />);
    const features = screen
      .getByRole("heading", {
        name: "Everything your small group needs to worship without interruption",
      })
      .closest("section")!;
    expect(
      within(features).getByRole("heading", { name: "Build your set" })
    ).toBeInTheDocument();
    expect(
      within(features).getByRole("heading", { name: "Render lyrics video" })
    ).toBeInTheDocument();
    expect(
      within(features).getByRole("heading", { name: "Cast to the TV" })
    ).toBeInTheDocument();
  });

  it("renders the four how-it-works steps", () => {
    render(<PublicLanding locale="en" />);
    const steps = screen
      .getByRole("heading", { name: "How it works" })
      .closest("section")!;
    expect(
      screen.getByRole("heading", { name: "How it works" })
    ).toBeInTheDocument();
    expect(within(steps).getByText("Pick your songs")).toBeInTheDocument();
    expect(within(steps).getByText("Render")).toBeInTheDocument();
    expect(within(steps).getByText("Cast to the TV")).toBeInTheDocument();
    expect(within(steps).getByText("Worship without interruption")).toBeInTheDocument();
  });

  it("renders bottom CTA and footer", () => {
    render(<PublicLanding locale="en" />);
    expect(
      screen.getByRole("heading", { name: "Ready to lead small group worship without interruption?" })
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Create your free account" })
    ).toHaveAttribute("href", "/register");
    expect(screen.getByText("Stream of Worship")).toBeInTheDocument();
  });
  it("renders a hidden build stamp with the build commit hash and date", () => {
    render(<PublicLanding locale="en" />);
    const stamp = screen.getByTestId("build-stamp");
    expect(stamp).toHaveClass("sr-only");
    expect(stamp.textContent).toContain(BUILD_COMMIT_HASH);
    expect(stamp.textContent).toContain(BUILD_COMMIT_DATE);
  });

  it("reveals the build info when the copyright year is clicked", () => {
    render(<PublicLanding locale="en" />);
    const toggle = screen.getByRole("button", { name: "Toggle build info" });
    expect(screen.queryByTestId("build-stamp-visible")).not.toBeInTheDocument();

    fireEvent.click(toggle);
    const visible = screen.getByTestId("build-stamp-visible");
    expect(visible.textContent).toContain(BUILD_COMMIT_HASH);
    expect(visible.textContent).toContain(BUILD_COMMIT_DATE);

    fireEvent.click(toggle);
    expect(screen.queryByTestId("build-stamp-visible")).not.toBeInTheDocument();
  });
});
