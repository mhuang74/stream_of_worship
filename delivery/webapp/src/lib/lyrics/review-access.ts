/**
 * Accounts allowed to review lyrics: the owner plus the shared test account
 * used for manual QA and screenshot capture.
 */
const REVIEWER_EMAILS = ["mhuang74@yahoo.com", "tester@streamofworship.com"];

/** Shared by the navigation and server guards; server checks are authoritative. */
export function canReviewLyrics(user?: { email?: string; emailVerified?: boolean } | null) {
  if (user?.emailVerified !== true || !user.email) return false;
  return REVIEWER_EMAILS.includes(user.email.toLowerCase());
}
