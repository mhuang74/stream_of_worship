import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SongsetSignupForm } from "@/components/SongsetSignupForm";
import { APP_URL } from "@/lib/urls";

describe("SongsetSignupForm", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(new Response(null, { status: 200 })))
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    cleanup();
  });

  it("POSTs email, source, and locale — never variant — and shows success", async () => {
    render(<SongsetSignupForm locale="en" />);
    await userEvent.type(screen.getByLabelText("Email address"), "hello@example.com");
    await userEvent.click(screen.getByRole("button"));

    await waitFor(() => expect(screen.getByRole("status")).toBeInTheDocument());
    expect(fetch).toHaveBeenCalledExactlyOnceWith(`${APP_URL}/api/capture-email`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        email: "hello@example.com",
        source: "landing-page",
        locale: "en",
      }),
    });
  });

  it("shows an inline error and skips the POST for an invalid email", async () => {
    render(<SongsetSignupForm locale="en" />);
    await userEvent.type(screen.getByLabelText("Email address"), "not-an-email");
    await userEvent.click(screen.getByRole("button"));

    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(fetch).not.toHaveBeenCalled();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("reports success on a honeypot hit without any POST", async () => {
    render(<SongsetSignupForm locale="en" />);
    // The honeypot input is visually hidden but present in the DOM.
    const honeypot = document.querySelector('input[name="website"]');
    expect(honeypot).not.toBeNull();
    await userEvent.type(screen.getByLabelText("Email address"), "hello@example.com");
    await userEvent.type(honeypot!, "spam.example.com");
    await userEvent.click(screen.getByRole("button"));

    await waitFor(() => expect(screen.getByRole("status")).toBeInTheDocument());
    expect(fetch).not.toHaveBeenCalled();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows the error state on a non-OK response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(new Response(null, { status: 429 })))
    );
    render(<SongsetSignupForm locale="en" />);
    await userEvent.type(screen.getByLabelText("Email address"), "hello@example.com");
    await userEvent.click(screen.getByRole("button"));

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("shows the error state when fetch throws", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new Error("offline"))));
    render(<SongsetSignupForm locale="en" />);
    await userEvent.type(screen.getByLabelText("Email address"), "hello@example.com");
    await userEvent.click(screen.getByRole("button"));

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
  });

  it("disables the button while submitting", async () => {
    let resolveFetch: (value: Response) => void = () => {};
    vi.stubGlobal(
      "fetch",
      vi.fn(
        () =>
          new Promise<Response>((resolve) => {
            resolveFetch = resolve;
          })
      )
    );
    render(<SongsetSignupForm locale="en" />);
    await userEvent.type(screen.getByLabelText("Email address"), "hello@example.com");
    await userEvent.click(screen.getByRole("button"));

    expect(screen.getByRole("button")).toBeDisabled();

    resolveFetch(new Response(null, { status: 200 }));
    await waitFor(() => expect(screen.getByRole("button")).not.toBeDisabled());
  });

  it("renders Traditional Chinese copy and reports the zh-Hant locale", async () => {
    render(<SongsetSignupForm locale="zh-Hant" />);
    const input = screen.getByLabelText("電子郵件地址");
    expect(screen.getByRole("button")).toHaveTextContent("訂閱免費歌單");

    await userEvent.type(input, "hello@example.com");
    await userEvent.click(screen.getByRole("button"));

    await waitFor(() => expect(screen.getByRole("status")).toBeInTheDocument());
    expect(fetch).toHaveBeenCalledExactlyOnceWith(`${APP_URL}/api/capture-email`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        email: "hello@example.com",
        source: "landing-page",
        locale: "zh-Hant",
      }),
    });
  });
});
