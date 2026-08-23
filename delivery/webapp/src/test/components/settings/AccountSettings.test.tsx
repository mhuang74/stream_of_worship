import { renderWithLocale } from "@/test/render";
import { screen, fireEvent, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { useSyncExternalStore } from "react";

const { mockUpdateUser, mockChangePassword, mockSignOut, mockToast } = vi.hoisted(() => ({
  mockUpdateUser: vi.fn(),
  mockChangePassword: vi.fn(),
  mockSignOut: vi.fn(),
  mockToast: { success: vi.fn(), error: vi.fn() },
}));

// Reactive session store mirroring the real better-auth useSession hook:
// getSnapshot returns the current value; tests mutate it and notify
// subscribers inside act() to simulate the async session load.
let sessionSnapshot: unknown = null;
const sessionSubscribers = new Set<() => void>();

vi.mock("@/lib/auth-client", () => ({
  updateUser: mockUpdateUser,
  changePassword: mockChangePassword,
  signOut: mockSignOut,
  useSession: () => {
    const data = useSyncExternalStore(
      (cb) => {
        sessionSubscribers.add(cb);
        return () => sessionSubscribers.delete(cb);
      },
      () => sessionSnapshot
    );
    return { data, isPending: data === null };
  },
}));

function setSession(data: unknown) {
  sessionSnapshot = data;
  for (const cb of sessionSubscribers) cb();
}

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }),
}));

vi.mock("sonner", () => ({
  toast: mockToast,
}));

async function renderAccount() {
  renderWithLocale(<AccountSettings />);
  await act(async () => {
    setSession({ user: { id: "1", name: "Test User", email: "user@example.com" } });
  });
}

import { AccountSettings } from "@/components/settings/AccountSettings";

describe("AccountSettings", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSignOut.mockResolvedValue(undefined);
    sessionSnapshot = null;
  });

  it("renders name and password forms", async () => {
    await renderAccount();
    expect(screen.getByLabelText("Name")).toHaveValue("Test User");
    expect(screen.getByLabelText("Current password")).toBeInTheDocument();
    expect(screen.getByLabelText("New password")).toBeInTheDocument();
    expect(screen.getByLabelText("Confirm new password")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Update name" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Change password" })).toBeInTheDocument();
  });

  it("renders the sign-out button", async () => {
    await renderAccount();
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
  });

  it("syncs the name field once the session loads asynchronously", async () => {
    renderWithLocale(<AccountSettings />);
    // Session not loaded yet (as during first render).
    expect(screen.getByLabelText("Name")).toHaveValue("");
    // The session resolves after mount; the field must populate without an edit.
    setSession({ user: { id: "1", name: "Test User", email: "user@example.com" } });
    await waitFor(() => {
      expect(screen.getByLabelText("Name")).toHaveValue("Test User");
    });
  });

  it("calls updateUser with the new name and shows success", async () => {
    mockUpdateUser.mockResolvedValue({ data: { user: { name: "New Name" } }, error: null });
    await renderAccount();
    const nameInput = screen.getByLabelText("Name");
    fireEvent.change(nameInput, { target: { value: "New Name" } });
    fireEvent.click(screen.getByRole("button", { name: "Update name" }));
    await waitFor(() => {
      expect(mockUpdateUser).toHaveBeenCalledWith({ name: "New Name" });
    });
    await waitFor(() => {
      expect(screen.getByText("Name updated")).toBeInTheDocument();
    });
  });

  it("shows error when name is empty", async () => {
    await renderAccount();
    const nameInput = screen.getByLabelText("Name");
    fireEvent.change(nameInput, { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Update name" }));
    await waitFor(() => {
      expect(screen.getByText("Name is required")).toBeInTheDocument();
    });
    expect(mockUpdateUser).not.toHaveBeenCalled();
  });

  it("calls changePassword with current and new password and clears the form", async () => {
    mockChangePassword.mockResolvedValue({ data: { status: true }, error: null });
    await renderAccount();
    await userEvent.type(screen.getByLabelText("Current password"), "oldpassword123");
    await userEvent.type(screen.getByLabelText("New password"), "newpassword123");
    await userEvent.type(screen.getByLabelText("Confirm new password"), "newpassword123");
    fireEvent.click(screen.getByRole("button", { name: "Change password" }));
    await waitFor(() => {
      expect(mockChangePassword).toHaveBeenCalledWith({
        currentPassword: "oldpassword123",
        newPassword: "newpassword123",
      });
    });
    await waitFor(() => {
      expect(screen.getByText("Password changed")).toBeInTheDocument();
    });
    expect(screen.getByLabelText("Current password")).toHaveValue("");
    expect(screen.getByLabelText("New password")).toHaveValue("");
  });

  it("shows current-password error when the current password is wrong", async () => {
    mockChangePassword.mockResolvedValue({
      data: null,
      error: { message: "Invalid password", code: "INVALID_PASSWORD" },
    });
    await renderAccount();
    await userEvent.type(screen.getByLabelText("Current password"), "wrongpassword");
    await userEvent.type(screen.getByLabelText("New password"), "newpassword123");
    await userEvent.type(screen.getByLabelText("Confirm new password"), "newpassword123");
    fireEvent.click(screen.getByRole("button", { name: "Change password" }));
    await waitFor(() => {
      expect(screen.getByText("Current password is incorrect")).toBeInTheDocument();
    });
  });

  it("shows validation errors for empty password fields", async () => {
    await renderAccount();
    fireEvent.click(screen.getByRole("button", { name: "Change password" }));
    await waitFor(() => {
      expect(screen.getByText("Current password is required")).toBeInTheDocument();
    });
    expect(screen.getByText("New password is required")).toBeInTheDocument();
    expect(screen.getByText("Please confirm your password")).toBeInTheDocument();
    expect(mockChangePassword).not.toHaveBeenCalled();
  });

  it("calls signOut and shows success toast on sign out", async () => {
    await renderAccount();
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    await waitFor(() => {
      expect(mockSignOut).toHaveBeenCalled();
    });
    expect(mockToast.success).toHaveBeenCalledWith("Signed out");
  });
});
