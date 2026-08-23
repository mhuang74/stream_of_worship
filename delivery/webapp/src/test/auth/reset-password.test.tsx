import { renderWithLocale } from "@/test/render";
import { screen, fireEvent, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach } from "vitest";

const { mockPush, mockResetPassword } = vi.hoisted(() => ({
  mockPush: vi.fn(),
  mockResetPassword: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
  useSearchParams: () => new URLSearchParams(window.location.search),
}));

vi.mock("@/lib/auth-client", () => ({
  resetPassword: mockResetPassword,
  signIn: { email: vi.fn() },
  signOut: vi.fn(),
  useSession: vi.fn(() => ({ data: null, isPending: false })),
}));

import ResetPasswordPage from "@/app/reset-password/page";

function setSearch(search: string) {
  Object.defineProperty(window, "location", {
    value: { ...window.location, search },
    writable: true,
  });
}

describe("ResetPasswordPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setSearch("?token=validtoken123");
  });

  it("renders password fields and submit button", () => {
    renderWithLocale(<ResetPasswordPage />);
    expect(screen.getByLabelText("New password")).toBeInTheDocument();
    expect(screen.getByLabelText("Confirm new password")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /reset password/i })).toBeInTheDocument();
  });

  it("shows the invalid-token message when ?error=INVALID_TOKEN is present", () => {
    setSearch("?error=INVALID_TOKEN");
    renderWithLocale(<ResetPasswordPage />);
    expect(screen.getByText(/invalid or has expired/i)).toBeInTheDocument();
  });

  it("shows validation errors on empty submit", async () => {
    renderWithLocale(<ResetPasswordPage />);
    fireEvent.click(screen.getByRole("button", { name: /reset password/i }));
    await waitFor(() => {
      expect(screen.getByText("New password is required")).toBeInTheDocument();
    });
    expect(screen.getByText("Please confirm your password")).toBeInTheDocument();
    expect(mockResetPassword).not.toHaveBeenCalled();
  });

  it("shows validation error when password is too short", async () => {
    renderWithLocale(<ResetPasswordPage />);
    await userEvent.type(screen.getByLabelText("New password"), "short");
    await userEvent.type(screen.getByLabelText("Confirm new password"), "short");
    fireEvent.click(screen.getByRole("button", { name: /reset password/i }));
    await waitFor(() => {
      expect(screen.getByText("Password must be at least 8 characters")).toBeInTheDocument();
    });
  });

  it("shows error when passwords do not match", async () => {
    renderWithLocale(<ResetPasswordPage />);
    await userEvent.type(screen.getByLabelText("New password"), "password123");
    await userEvent.type(screen.getByLabelText("Confirm new password"), "different123");
    fireEvent.click(screen.getByRole("button", { name: /reset password/i }));
    await waitFor(() => {
      expect(screen.getByText("Passwords do not match")).toBeInTheDocument();
    });
  });

  it("calls resetPassword with token from query and redirects to /login on success", async () => {
    mockResetPassword.mockResolvedValue({ data: { status: true }, error: null });
    renderWithLocale(<ResetPasswordPage />);
    await userEvent.type(screen.getByLabelText("New password"), "newpassword123");
    await userEvent.type(screen.getByLabelText("Confirm new password"), "newpassword123");
    fireEvent.click(screen.getByRole("button", { name: /reset password/i }));
    await waitFor(() => {
      expect(mockResetPassword).toHaveBeenCalledWith({
        newPassword: "newpassword123",
        token: "validtoken123",
      });
    });
    await waitFor(() => {
      expect(mockPush).toHaveBeenCalledWith("/login");
    });
  });

  it("shows invalid-token error when the token is rejected", async () => {
    mockResetPassword.mockResolvedValue({
      data: null,
      error: { message: "Invalid token", code: "INVALID_TOKEN" },
    });
    renderWithLocale(<ResetPasswordPage />);
    await userEvent.type(screen.getByLabelText("New password"), "newpassword123");
    await userEvent.type(screen.getByLabelText("Confirm new password"), "newpassword123");
    fireEvent.click(screen.getByRole("button", { name: /reset password/i }));
    await waitFor(() => {
      expect(screen.getByText(/invalid or has expired/i)).toBeInTheDocument();
    });
    expect(mockPush).not.toHaveBeenCalled();
  });
});
