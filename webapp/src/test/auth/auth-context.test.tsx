import { render, screen, act } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";

const { mockSignOut, mockUseSession } = vi.hoisted(() => ({
  mockSignOut: vi.fn(),
  mockUseSession: vi.fn(),
}));

vi.mock("@/lib/auth-client", () => ({
  signOut: mockSignOut,
  useSession: mockUseSession,
  signIn: { email: vi.fn() },
}));

import { AuthProvider, useAuth } from "@/contexts/AuthContext";

function TestConsumer() {
  const { session, isPending, signOut } = useAuth();
  return (
    <div>
      <span data-testid="pending">{String(isPending)}</span>
      <span data-testid="session">{session ? "logged-in" : "no-session"}</span>
      <button onClick={signOut}>Sign out</button>
    </div>
  );
}

describe("AuthProvider", () => {
  it("provides pending state", () => {
    mockUseSession.mockReturnValue({ data: null, isPending: true });
    render(
      <AuthProvider>
        <TestConsumer />
      </AuthProvider>
    );
    expect(screen.getByTestId("pending").textContent).toBe("true");
    expect(screen.getByTestId("session").textContent).toBe("no-session");
  });

  it("provides session data when authenticated", () => {
    mockUseSession.mockReturnValue({
      data: { user: { id: "1", email: "user@example.com" }, session: {} },
      isPending: false,
    });
    render(
      <AuthProvider>
        <TestConsumer />
      </AuthProvider>
    );
    expect(screen.getByTestId("session").textContent).toBe("logged-in");
  });

  it("calls signOut when triggered", async () => {
    mockUseSession.mockReturnValue({
      data: { user: { id: "1" }, session: {} },
      isPending: false,
    });
    mockSignOut.mockResolvedValue(undefined);
    render(
      <AuthProvider>
        <TestConsumer />
      </AuthProvider>
    );
    await act(async () => {
      screen.getByRole("button", { name: /sign out/i }).click();
    });
    expect(mockSignOut).toHaveBeenCalled();
  });
});

describe("useAuth", () => {
  it("throws when used outside AuthProvider", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    expect(() => render(<TestConsumer />)).toThrow("useAuth must be used within an AuthProvider");
    spy.mockRestore();
  });
});
