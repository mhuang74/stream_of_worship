import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach } from "vitest";

const { mockPush, mockRefresh, mockSignUp } = vi.hoisted(() => ({
  mockPush: vi.fn(),
  mockRefresh: vi.fn(),
  mockSignUp: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush, refresh: mockRefresh }),
}));

vi.mock("@/lib/auth-client", () => ({
  signIn: { email: vi.fn() },
  signOut: vi.fn(),
  useSession: vi.fn(() => ({ data: null, isPending: false })),
  signUp: { email: mockSignUp },
}));

import RegisterPage from "@/app/register/page";

describe("RegisterPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders all fields", () => {
    render(<RegisterPage />);
    expect(screen.getByLabelText("Name")).toBeInTheDocument();
    expect(screen.getByLabelText("Email")).toBeInTheDocument();
    expect(screen.getByLabelText("Password")).toBeInTheDocument();
    expect(screen.getByLabelText("Confirm password")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /create account/i })).toBeInTheDocument();
  });

  it("shows error when name is empty", async () => {
    render(<RegisterPage />);
    fireEvent.click(screen.getByRole("button", { name: /create account/i }));
    await waitFor(() => {
      expect(screen.getByText("Name is required")).toBeInTheDocument();
    });
    expect(mockSignUp).not.toHaveBeenCalled();
  });

  it("shows error when email is empty", async () => {
    render(<RegisterPage />);
    await userEvent.type(screen.getByLabelText("Name"), "Test User");
    fireEvent.click(screen.getByRole("button", { name: /create account/i }));
    await waitFor(() => {
      expect(screen.getByText("Email is required")).toBeInTheDocument();
    });
    expect(mockSignUp).not.toHaveBeenCalled();
  });

  it("shows error for invalid email format", async () => {
    render(<RegisterPage />);
    await userEvent.type(screen.getByLabelText("Name"), "Test User");
    await userEvent.type(screen.getByLabelText("Email"), "notanemail");
    fireEvent.click(screen.getByRole("button", { name: /create account/i }));
    await waitFor(() => {
      expect(screen.getByText("Enter a valid email address")).toBeInTheDocument();
    });
  });

  it("shows error when password is empty", async () => {
    render(<RegisterPage />);
    await userEvent.type(screen.getByLabelText("Name"), "Test User");
    await userEvent.type(screen.getByLabelText("Email"), "user@example.com");
    fireEvent.click(screen.getByRole("button", { name: /create account/i }));
    await waitFor(() => {
      expect(screen.getByText("Password is required")).toBeInTheDocument();
    });
  });

  it("shows error when password is too short", async () => {
    render(<RegisterPage />);
    await userEvent.type(screen.getByLabelText("Name"), "Test User");
    await userEvent.type(screen.getByLabelText("Email"), "user@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "short");
    fireEvent.click(screen.getByRole("button", { name: /create account/i }));
    await waitFor(() => {
      expect(screen.getByText("Password must be at least 8 characters")).toBeInTheDocument();
    });
  });

  it("shows error when passwords do not match", async () => {
    render(<RegisterPage />);
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
    render(<RegisterPage />);
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

  it("redirects to /songsets and calls refresh on success", async () => {
    mockSignUp.mockResolvedValue({ data: { user: { id: "1" } }, error: null });
    render(<RegisterPage />);
    await userEvent.type(screen.getByLabelText("Name"), "Test User");
    await userEvent.type(screen.getByLabelText("Email"), "user@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "password123");
    await userEvent.type(screen.getByLabelText("Confirm password"), "password123");
    fireEvent.click(screen.getByRole("button", { name: /create account/i }));
    await waitFor(() => {
      expect(mockPush).toHaveBeenCalledWith("/songsets");
      expect(mockRefresh).toHaveBeenCalled();
    });
  });

  it("shows error on duplicate email", async () => {
    mockSignUp.mockResolvedValue({
      data: null,
      error: { message: "User already exists" },
    });
    render(<RegisterPage />);
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
    let resolve: (v: unknown) => void;
    const pending = new Promise((r) => {
      resolve = r;
    });
    mockSignUp.mockReturnValue(pending);
    render(<RegisterPage />);
    await userEvent.type(screen.getByLabelText("Name"), "Test User");
    await userEvent.type(screen.getByLabelText("Email"), "user@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "password123");
    await userEvent.type(screen.getByLabelText("Confirm password"), "password123");
    fireEvent.click(screen.getByRole("button", { name: /create account/i }));
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /creating account/i })).toBeDisabled();
    });
    resolve!({ data: { user: { id: "1" } }, error: null });
  });
});
