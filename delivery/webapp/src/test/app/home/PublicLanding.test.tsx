import { describe, it, expect } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithLocale as render } from "@/test/render";
import { PublicLanding } from "@/app/page/PublicLanding";

describe("PublicLanding", () => {
  it("renders hero tagline, title, and description in en", () => {
    render(<PublicLanding locale="en" />);
    expect(
      screen.getByText((content, element) =>
        element?.textContent === "✦ Seamless worship music transitions"
      )
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", {
        name: "Lead worship without awkward pauses.",
      })
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Stream of Worship analyzes tempo, key, and structure/)
    ).toBeInTheDocument();
  });

  it("renders hero strings in Traditional Chinese for zh-Hant", () => {
    render(<PublicLanding locale="zh-Hant" />);
    expect(
      screen.getByRole("heading", { name: "帶領敬拜，無需尷尬停頓。" })
    ).toBeInTheDocument();
    expect(
      screen.getByText((content, element) =>
        element?.textContent === "✦ 無縫敬拜音樂轉場"
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
    expect(
      screen.getByRole("heading", { name: "Build songsets" })
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Render audio & video" })
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Share with your team" })
    ).toBeInTheDocument();
  });

  it("renders the four how-it-works steps", () => {
    render(<PublicLanding locale="en" />);
    expect(
      screen.getByRole("heading", { name: "How it works" })
    ).toBeInTheDocument();
    expect(screen.getByText("Pick your songs")).toBeInTheDocument();
    expect(screen.getByText("Tune transitions")).toBeInTheDocument();
    expect(screen.getByText("Render")).toBeInTheDocument();
    expect(screen.getByText("Lead & share")).toBeInTheDocument();
  });

  it("renders bottom CTA and footer", () => {
    render(<PublicLanding locale="en" />);
    expect(
      screen.getByRole("heading", { name: "Ready to lead worship seamlessly?" })
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Create your free account" })
    ).toHaveAttribute("href", "/register");
    expect(screen.getByText("Stream of Worship")).toBeInTheDocument();
  });
});
