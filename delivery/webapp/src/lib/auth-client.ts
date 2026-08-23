import { createAuthClient } from "better-auth/react";

export const authClient = createAuthClient({
  // No baseURL — uses current browser origin, works on any host/port
});

export const {
  signIn,
  signOut,
  signUp,
  useSession,
  requestPasswordReset,
  resetPassword,
  changePassword,
  updateUser,
  sendVerificationEmail: requestVerificationEmail,
} = authClient;
