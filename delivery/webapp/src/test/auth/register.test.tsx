import { renderWithLocale } from "@/test/render";
import { screen, fireEvent, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

const { mockPush, mockRefresh, mockSignUp, mockRequestVerificationEmail } = vi.hoisted(() => ({
  mockPush: vi.fn(),
  mockRefresh: vi.fn(),
  mockSignUp: vi.fn(),
  mockRequestVerificationEmail: vi.fn(),
}));

const mockFetch = vi.fn();
const originalFetch = global.fetch;

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush, refresh: mockRefresh }),
}));

vi.mock("@/lib/auth-client", () => ({
  signIn: { email: vi.fn() },
  signOut: vi.fn(),
  useSession: vi.fn(() => ({ data: null, isPending: false })),
  signUp: { email: mockSignUp },
  requestVerificationEmail: mockRequestVerificationEmail,
}));

import RegisterPage from "@/app/register/page";

describe("RegisterPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    global.fetch = mockFetch;
    mockFetch.mockResolvedValue(new Response(null, { status: 200 }));
  });

  afterEach(() => {
    global.fetch = originalFetch;
  });

  it("renders all fields", () => {
    renderWithLocale(<RegisterPage />);
    expect(screen.getByLabelText("Name")).toBeInTheDocument();
    expect(screen.getByLabelText("Email")).toBeInTheDocument();
    expect(screen.getByLabelText("Password")).toBeInTheDocument();
    expect(screen.getByLabelText("Confirm password")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /create account/i })).toBeInTheDocument();
  });

  it("shows error when name is empty", async () => {
    renderWithLocale(<RegisterPage />);
    fireEvent.click(screen.getByRole("button", { name: /create account/i }));
    await waitFor(() => {
      expect(screen.getByText("Name is required")).toBeInTheDocument();
    });
    expect(mockSignUp).not.toHaveBeenCalled();
  });

  it("shows error when email is empty", async () => {
    renderWithLocale(<RegisterPage />);
    await userEvent.type(screen.getByLabelText("Name"), "Test User");
    fireEvent.click(screen.getByRole("button", { name: /create account/i }));
    await waitFor(() => {
      expect(screen.getByText("Email is required")).toBeInTheDocument();
    });
    expect(mockSignUp).not.toHaveBeenCalled();
  });

  it("shows error for invalid email format", async () => {
    renderWithLocale(<RegisterPage />);
    await userEvent.type(screen.getByLabelText("Name"), "Test User");
    await userEvent.type(screen.getByLabelText("Email"), "notanemail");
    fireEvent.click(screen.getByRole("button", { name: /create account/i }));
    await waitFor(() => {
      expect(screen.getByText("Enter a valid email address")).toBeInTheDocument();
    });
  });

  it("shows error when password is empty", async () => {
    renderWithLocale(<RegisterPage />);
    await userEvent.type(screen.getByLabelText("Name"), "Test User");
    await userEvent.type(screen.getByLabelText("Email"), "user@example.com");
    fireEvent.click(screen.getByRole("button", { name: /create account/i }));
    await waitFor(() => {
      expect(screen.getByText("Password is required")).toBeInTheDocument();
    });
  });

  it("shows error when password is too short", async () => {
    renderWithLocale(<RegisterPage />);
    await userEvent.type(screen.getByLabelText("Name"), "Test User");
    await userEvent.type(screen.getByLabelText("Email"), "user@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "short");
    fireEvent.click(screen.getByRole("button", { name: /create account/i }));
    await waitFor(() => {
      expect(screen.getByText("Password must be at least 8 characters")).toBeInTheDocument();
    });
  });

  it("shows error when passwords do not match", async () => {
    renderWithLocale(<RegisterPage />);
    await userEvent.type(screen.getByLabelText("Name"), "Test User");
    await userEvent.type(screen.getByLabelText("Email"), "user@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "password123");
    await userEvent.type(screen.getByLabelText("Confirm password"), "different123");
    fireEvent.click(screen.getByRole("button", { name: /create account/i }));
    await waitFor(() => {
      expect(screen.getByText("Passwords do not match")).toBeInTheDocument();
    });
  });

  it("calls signUp.email with correct args on valid submit", async () => {
    mockSignUp.mockResolvedValue({ data: { user: { id: "1" } }, error: null });
    renderWithLocale(<RegisterPage />);
    await userEvent.type(screen.getByLabelText("Name"), "Test User");
    await userEvent.type(screen.getByLabelText("Email"), "user@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "password123");
    await userEvent.type(screen.getByLabelText("Confirm password"), "password123");
    fireEvent.click(screen.getByRole("button", { name: /create account/i }));
    await waitFor(() => {
      expect(mockSignUp).toHaveBeenCalledWith({
        email: "user@example.com",
        password: "password123",
        name: "Test User",
      });
    });
  });

  it("shows the check-your-email confirmation on success (no redirect)", async () => {
    mockSignUp.mockResolvedValue({ data: { user: { id: "1" } }, error: null });
    renderWithLocale(<RegisterPage />);
    await userEvent.type(screen.getByLabelText("Name"), "Test User");
    await userEvent.type(screen.getByLabelText("Email"), "user@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "password123");
    await userEvent.type(screen.getByLabelText("Confirm password"), "password123");
    fireEvent.click(screen.getByRole("button", { name: /create account/i }));
    await waitFor(() => {
      expect(screen.getByText("Check your email")).toBeInTheDocument();
    });
    expect(screen.getByText(/user@example\.com/)).toBeInTheDocument();
    expect(mockPush).not.toHaveBeenCalled();
    expect(mockRefresh).not.toHaveBeenCalled();
  });

  it("resends the verification email from the confirmation screen", async () => {
    mockSignUp.mockResolvedValue({ data: { user: { id: "1" } }, error: null });
    mockRequestVerificationEmail.mockResolvedValue({ data: { status: true }, error: null });
    renderWithLocale(<RegisterPage />);
    await userEvent.type(screen.getByLabelText("Name"), "Test User");
    await userEvent.type(screen.getByLabelText("Email"), "user@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "password123");
    await userEvent.type(screen.getByLabelText("Confirm password"), "password123");
    fireEvent.click(screen.getByRole("button", { name: /create account/i }));
    await waitFor(() => {
      expect(screen.getByText("Check your email")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: /resend verification email/i }));
    await waitFor(() => {
      expect(mockRequestVerificationEmail).toHaveBeenCalledWith({
        email: "user@example.com",
        callbackURL: "/",
      });
    });
  });

  it("shows error on duplicate email", async () => {
    mockSignUp.mockResolvedValue({
      data: null,
      error: { message: "User already exists" },
    });
    renderWithLocale(<RegisterPage />);
    await userEvent.type(screen.getByLabelText("Name"), "Test User");
    await userEvent.type(screen.getByLabelText("Email"), "existing@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "password123");
    await userEvent.type(screen.getByLabelText("Confirm password"), "password123");
    fireEvent.click(screen.getByRole("button", { name: /create account/i }));
    await waitFor(() => {
      expect(screen.getByText("User already exists")).toBeInTheDocument();
    });
  });

  it("shows loading state during submission", async () => {
    let resolve!: (value: unknown) => void;
    const pending = new Promise<unknown>((res) => {
      resolve = res;
    });
    mockSignUp.mockReturnValue(pending);
    renderWithLocale(<RegisterPage />);
    await userEvent.type(screen.getByLabelText("Name"), "Test User");
    await userEvent.type(screen.getByLabelText("Email"), "user@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "password123");
    await userEvent.type(screen.getByLabelText("Confirm password"), "password123");
    fireEvent.click(screen.getByRole("button", { name: /create account/i }));
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /creating account/i })).toBeDisabled();
    });
    resolve({ data: { user: { id: "1" } }, error: null });
  });

  it("persists zh-Hant locale via settings PUT when locale is zh-Hant", async () => {
    mockSignUp.mockResolvedValue({ data: { user: { id: "1" } }, error: null });
    renderWithLocale(<RegisterPage />, "zh-Hant");
    await userEvent.type(screen.getByLabelText("姓名"), "Test User");
    await userEvent.type(screen.getByLabelText("電子郵件"), "user@example.com");
    await userEvent.type(screen.getByLabelText("密碼"), "password123");
    await userEvent.type(screen.getByLabelText("確認密碼"), "password123");
    fireEvent.click(screen.getByRole("button", { name: /建立帳號/i }));
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
      expect(screen.getByText("請檢查您的電子郵件")).toBeInTheDocument();
    });
  });

  it("persists en locale via settings PUT on successful registration (overrides saved locale)", async () => {
    mockSignUp.mockResolvedValue({ data: { user: { id: "1" } }, error: null });
    renderWithLocale(<RegisterPage />);
    await userEvent.type(screen.getByLabelText("Name"), "Test User");
    await userEvent.type(screen.getByLabelText("Email"), "user@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "password123");
    await userEvent.type(screen.getByLabelText("Confirm password"), "password123");
    fireEvent.click(screen.getByRole("button", { name: /create account/i }));
    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(
        "/api/settings",
        expect.objectContaining({
          method: "PUT",
          body: JSON.stringify({ locale: "en" }),
        })
      );
    });
    await waitFor(() => {
      expect(screen.getByText("Check your email")).toBeInTheDocument();
    });
  });
});
