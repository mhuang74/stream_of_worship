"use client";

import { createContext, useContext } from "react";
import { useSession, signOut } from "@/lib/auth-client";

type SessionData = ReturnType<typeof useSession>["data"];

interface AuthContextValue {
  session: SessionData;
  isPending: boolean;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const { data: session, isPending } = useSession();

  async function handleSignOut() {
    await signOut();
  }

  return (
    <AuthContext.Provider value={{ session, isPending, signOut: handleSignOut }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return ctx;
}
