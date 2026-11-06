/** Shared by the navigation and server guards; server checks are authoritative. */
export function canReviewLyrics(user?: { email?: string; emailVerified?: boolean } | null) {
  return user?.emailVerified === true && user.email?.toLowerCase() === "mhuang74@yahoo.com";
}
