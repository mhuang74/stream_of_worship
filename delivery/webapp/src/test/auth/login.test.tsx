import { screen, fireEvent, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderWithLocale } from "@/test/render";

const { mockPush, mockRefresh, mockSignIn, mockSendVerificationEmail } = vi.hoisted(() => ({
  mockPush: vi.fn(),
  mockRefresh: vi.fn(),
  mockSignIn: vi.fn(),
  mockSendVerificationEmail: vi.fn(),
}));

const mockFetch = vi.fn();
const originalFetch = global.fetch;

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush, refresh: mockRefresh }),
}));

vi.mock("@/lib/auth-client", () => ({
  signIn: { email: mockSignIn },
  signOut: vi.fn(),
  useSession: vi.fn(() => ({ data: null, isPending: false })),
  sendVerificationEmail: mockSendVerificationEmail,
}));

import LoginPage from "@/app/login/page";

describe("LoginPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    global.fetch = mockFetch;
    mockFetch.mockResolvedValue(new Response(null, { status: 200 }));
    // Ensure clean location.search for tests that don't set callbackUrl.
    Object.defineProperty(window, "location", {
      value: { ...window.location, search: "" },
      writable: true,
    });
  });

  afterEach(() => {
    global.fetch = originalFetch;
    // The language-switcher tests write sow_locale; clear it so later tests
    // start from a clean cookie state.
    document.cookie = "sow_locale=; path=/; max-age=0";
  });

  it("renders email and password fields", () => {
    renderWithLocale(<LoginPage />);
    expect(screen.getByLabelText("Email")).toBeInTheDocument();
    expect(screen.getByLabelText("Password")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /sign in/i })).toBeInTheDocument();
  });

  it("shows validation error when email is empty", async () => {
    renderWithLocale(<LoginPage />);
    fireEvent.click(screen.getByRole("button", { name: /sign in/i }));
    await waitFor(() => {
      expect(screen.getByText("Email is required")).toBeInTheDocument();
    });
    expect(mockSignIn).not.toHaveBeenCalled();
  });

  it("shows validation error for invalid email format", async () => {
    renderWithLocale(<LoginPage />);
    await userEvent.type(screen.getByLabelText("Email"), "notanemail");
    fireEvent.click(screen.getByRole("button", { name: /sign in/i }));
    await waitFor(() => {
      expect(screen.getByText("Enter a valid email address")).toBeInTheDocument();
    });
  });

  it("shows validation error when password is empty", async () => {
    renderWithLocale(<LoginPage />);
    await userEvent.type(screen.getByLabelText("Email"), "user@example.com");
    fireEvent.click(screen.getByRole("button", { name: /sign in/i }));
    await waitFor(() => {
      expect(screen.getByText("Password is required")).toBeInTheDocument();
    });
  });

  it("shows validation error when password is too short", async () => {
    renderWithLocale(<LoginPage />);
    await userEvent.type(screen.getByLabelText("Email"), "user@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "short");
    fireEvent.click(screen.getByRole("button", { name: /sign in/i }));
    await waitFor(() => {
      expect(screen.getByText("Password must be at least 8 characters")).toBeInTheDocument();
    });
  });

  it("calls signIn.email with credentials on valid submit", async () => {
    mockSignIn.mockResolvedValue({ data: { user: { id: "1" } }, error: null });
    renderWithLocale(<LoginPage />);
    await userEvent.type(screen.getByLabelText("Email"), "user@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "password123");
    fireEvent.click(screen.getByRole("button", { name: /sign in/i }));
    await waitFor(() => {
      expect(mockSignIn).toHaveBeenCalledWith({
        email: "user@example.com",
        password: "password123",
      });
    });
  });

  it("redirects to / on successful login", async () => {
    mockSignIn.mockResolvedValue({ data: { user: { id: "1" } }, error: null });
    renderWithLocale(<LoginPage />);
    await userEvent.type(screen.getByLabelText("Email"), "user@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "password123");
    fireEvent.click(screen.getByRole("button", { name: /sign in/i }));
    await waitFor(() => {
      expect(mockPush).toHaveBeenCalledWith("/");
    });
  });

  it("redirects to callbackUrl when present on successful login", async () => {
    mockSignIn.mockResolvedValue({ data: { user: { id: "1" } }, error: null });
    Object.defineProperty(window, "location", {
      value: { ...window.location, search: "?callbackUrl=/songsets/123" },
      writable: true,
    });
    renderWithLocale(<LoginPage />);
    await userEvent.type(screen.getByLabelText("Email"), "user@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "password123");
    fireEvent.click(screen.getByRole("button", { name: /sign in/i }));
    await waitFor(() => {
      expect(mockPush).toHaveBeenCalledWith("/songsets/123");
    });
  });

  it("ignores external callbackUrl and redirects to /", async () => {
    mockSignIn.mockResolvedValue({ data: { user: { id: "1" } }, error: null });
    Object.defineProperty(window, "location", {
      value: { ...window.location, search: "?callbackUrl=https://evil.com" },
      writable: true,
    });
    renderWithLocale(<LoginPage />);
    await userEvent.type(screen.getByLabelText("Email"), "user@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "password123");
    fireEvent.click(screen.getByRole("button", { name: /sign in/i }));
    await waitFor(() => {
      expect(mockPush).toHaveBeenCalledWith("/");
    });
  });

  it("persists zh-Hant locale via settings PUT on successful login", async () => {
    mockSignIn.mockResolvedValue({ data: { user: { id: "1" } }, error: null });
    renderWithLocale(<LoginPage />, "zh-Hant");
    await userEvent.type(screen.getByLabelText("電子郵件"), "user@example.com");
    await userEvent.type(screen.getByLabelText("密碼"), "password123");
    fireEvent.click(screen.getByRole("button", { name: /登入/i }));
    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(
        "/api/settings",
        expect.objectContaining({
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ locale: "zh-Hant" }),
        })
      );
    });
    await waitFor(() => {
      expect(mockPush).toHaveBeenCalledWith("/");
    });
  });

  it("persists en locale via settings PUT on successful login (overrides saved locale)", async () => {
    mockSignIn.mockResolvedValue({ data: { user: { id: "1" } }, error: null });
    renderWithLocale(<LoginPage />);
    await userEvent.type(screen.getByLabelText("Email"), "user@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "password123");
    fireEvent.click(screen.getByRole("button", { name: /sign in/i }));
    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(
        "/api/settings",
        expect.objectContaining({
          method: "PUT",
          body: JSON.stringify({ locale: "en" }),
        })
      );
    });
  });

  it("shows form error on invalid credentials", async () => {
    mockSignIn.mockResolvedValue({
      data: null,
      error: { message: "Invalid email or password" },
    });
    renderWithLocale(<LoginPage />);
    await userEvent.type(screen.getByLabelText("Email"), "user@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "wrongpassword");
    fireEvent.click(screen.getByRole("button", { name: /sign in/i }));
    await waitFor(() => {
      expect(screen.getByText("Invalid email or password")).toBeInTheDocument();
    });
  });

  it("renders a forgot-password link to /forgot-password", () => {
    renderWithLocale(<LoginPage />);
    expect(screen.getByRole("link", { name: "Forgot password?" })).toHaveAttribute(
      "href",
      "/forgot-password"
    );
  });

  it("shows a resend-verification action for an unverified email", async () => {
    mockSignIn.mockResolvedValue({
      data: null,
      error: { message: "Email not verified", code: "EMAIL_NOT_VERIFIED", status: 403 },
    });
    mockSendVerificationEmail.mockResolvedValue({ data: { status: true }, error: null });
    renderWithLocale(<LoginPage />);
    await userEvent.type(screen.getByLabelText("Email"), "unverified@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "password123");
    fireEvent.click(screen.getByRole("button", { name: /sign in/i }));
    await waitFor(() => {
      expect(screen.getByText(/hasn't been verified yet/i)).toBeInTheDocument();
    });
    expect(mockPush).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: /resend verification email/i }));
    await waitFor(() => {
      expect(mockSendVerificationEmail).toHaveBeenCalledWith({
        email: "unverified@example.com",
        callbackURL: "/",
      });
    });
    await waitFor(() => {
      expect(screen.getByText("Verification email sent")).toBeInTheDocument();
    });
  });

  it("keeps the form error for non-verification sign-in failures", async () => {
    mockSignIn.mockResolvedValue({
      data: null,
      error: { message: "Invalid email or password", code: "INVALID_EMAIL_OR_PASSWORD" },
    });
    renderWithLocale(<LoginPage />);
    await userEvent.type(screen.getByLabelText("Email"), "user@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "wrongpassword");
    fireEvent.click(screen.getByRole("button", { name: /sign in/i }));
    await waitFor(() => {
      expect(screen.getByText("Invalid email or password")).toBeInTheDocument();
    });
    expect(
      screen.queryByText(/hasn't been verified yet/i)
    ).not.toBeInTheDocument();
  });

  it("shows loading state during submission", async () => {
    let resolve: (v: unknown) => void;
    const pending = new Promise((r) => {
      resolve = r;
    });
    mockSignIn.mockReturnValue(pending);
    renderWithLocale(<LoginPage />);
    await userEvent.type(screen.getByLabelText("Email"), "user@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "password123");
    fireEvent.click(screen.getByRole("button", { name: /sign in/i }));
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /signing in/i })).toBeDisabled();
    });
    resolve!({ data: { user: { id: "1" } }, error: null });
  });

  it("switches to zh-Hant via the language switcher", async () => {
    renderWithLocale(<LoginPage />);
    await userEvent.click(screen.getByRole("button", { name: "繁體中文" }));
    expect(
      screen.getByText("登入", { selector: '[data-slot="card-title"]' })
    ).toBeInTheDocument();
    expect(screen.getByLabelText("密碼")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "繁體中文" })).toHaveAttribute(
      "aria-current",
      "true"
    );
    expect(document.cookie).toContain("sow_locale=zh-Hant");
  });

  it("switches back to English and rewrites the cookie", async () => {
    renderWithLocale(<LoginPage />);
    await userEvent.click(screen.getByRole("button", { name: "繁體中文" }));
    expect(document.cookie).toContain("sow_locale=zh-Hant");
    await userEvent.click(screen.getByRole("button", { name: "English" }));
    expect(screen.getByText("Sign in", { selector: '[data-slot="card-title"]' })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "English" })).toHaveAttribute(
      "aria-current",
      "true"
    );
    expect(document.cookie).toContain("sow_locale=en");
  });
});
