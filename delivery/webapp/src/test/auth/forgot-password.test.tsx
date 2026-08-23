import { renderWithLocale } from "@/test/render";
import { screen, fireEvent, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach } from "vitest";

const { mockRequestPasswordReset } = vi.hoisted(() => ({
  mockRequestPasswordReset: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

vi.mock("@/lib/auth-client", () => ({
  requestPasswordReset: mockRequestPasswordReset,
  signIn: { email: vi.fn() },
  signOut: vi.fn(),
  useSession: vi.fn(() => ({ data: null, isPending: false })),
}));

import ForgotPasswordPage from "@/app/forgot-password/page";

describe("ForgotPasswordPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders the email field and submit button", () => {
    renderWithLocale(<ForgotPasswordPage />);
    expect(screen.getByLabelText("Email")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /send reset link/i })).toBeInTheDocument();
  });

  it("shows error when email is empty", async () => {
    renderWithLocale(<ForgotPasswordPage />);
    fireEvent.click(screen.getByRole("button", { name: /send reset link/i }));
    await waitFor(() => {
      expect(screen.getByText("Email is required")).toBeInTheDocument();
    });
    expect(mockRequestPasswordReset).not.toHaveBeenCalled();
  });

  it("shows error for invalid email format", async () => {
    renderWithLocale(<ForgotPasswordPage />);
    await userEvent.type(screen.getByLabelText("Email"), "notanemail");
    fireEvent.click(screen.getByRole("button", { name: /send reset link/i }));
    await waitFor(() => {
      expect(screen.getByText("Enter a valid email address")).toBeInTheDocument();
    });
    expect(mockRequestPasswordReset).not.toHaveBeenCalled();
  });

  it("calls requestPasswordReset with email and redirectTo on valid submit", async () => {
    mockRequestPasswordReset.mockResolvedValue({ data: { status: true }, error: null });
    renderWithLocale(<ForgotPasswordPage />);
    await userEvent.type(screen.getByLabelText("Email"), "user@example.com");
    fireEvent.click(screen.getByRole("button", { name: /send reset link/i }));
    await waitFor(() => {
      expect(mockRequestPasswordReset).toHaveBeenCalledWith({
        email: "user@example.com",
        redirectTo: "/reset-password",
      });
    });
  });

  it("shows the generic confirmation (no account enumeration)", async () => {
    mockRequestPasswordReset.mockResolvedValue({ data: { status: true }, error: null });
    renderWithLocale(<ForgotPasswordPage />);
    await userEvent.type(screen.getByLabelText("Email"), "user@example.com");
    fireEvent.click(screen.getByRole("button", { name: /send reset link/i }));
    await waitFor(() => {
      expect(
        screen.getByText(/If an account exists for that email/)
      ).toBeInTheDocument();
    });
  });

  it("shows the same confirmation even when the account does not exist", async () => {
    // Better Auth returns a generic 200 for unknown emails; the UI must not
    // distinguish the two cases.
    mockRequestPasswordReset.mockResolvedValue({ data: { status: true }, error: null });
    renderWithLocale(<ForgotPasswordPage />);
    await userEvent.type(screen.getByLabelText("Email"), "nobody@example.com");
    fireEvent.click(screen.getByRole("button", { name: /send reset link/i }));
    await waitFor(() => {
      expect(
        screen.getByText(/If an account exists for that email/)
      ).toBeInTheDocument();
    });
    expect(screen.queryByText(/doesn't exist/i)).not.toBeInTheDocument();
  });

  it("shows a generic error when the request fails", async () => {
    mockRequestPasswordReset.mockResolvedValue({ data: null, error: { message: "boom" } });
    renderWithLocale(<ForgotPasswordPage />);
    await userEvent.type(screen.getByLabelText("Email"), "user@example.com");
    fireEvent.click(screen.getByRole("button", { name: /send reset link/i }));
    await waitFor(() => {
      expect(
        screen.getByText("An unexpected error occurred. Please try again.")
      ).toBeInTheDocument();
    });
  });
});
