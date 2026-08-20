import { screen, fireEvent, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderWithLocale } from "@/test/render";

const { mockPush, mockRefresh, mockSignIn } = vi.hoisted(() => ({
  mockPush: vi.fn(),
  mockRefresh: vi.fn(),
  mockSignIn: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush, refresh: mockRefresh }),
}));

vi.mock("@/lib/auth-client", () => ({
  signIn: { email: mockSignIn },
  signOut: vi.fn(),
  useSession: vi.fn(() => ({ data: null, isPending: false })),
}));

import LoginPage from "@/app/login/page";

describe("LoginPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
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

  it("redirects to /songsets on successful login", async () => {
    mockSignIn.mockResolvedValue({ data: { user: { id: "1" } }, error: null });
    renderWithLocale(<LoginPage />);
    await userEvent.type(screen.getByLabelText("Email"), "user@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "password123");
    fireEvent.click(screen.getByRole("button", { name: /sign in/i }));
    await waitFor(() => {
      expect(mockPush).toHaveBeenCalledWith("/songsets");
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
