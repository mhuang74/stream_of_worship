import { headers } from "next/headers";
import { redirect } from "next/navigation";
import { auth } from "@/lib/auth";
import { WorshipClient } from "./WorshipClient";

/**
 * /worship (issue #211 follow-up descope): the offline redirect target and
 * the nav's Worship surface. Auth gate only — the list boots from the
 * IndexedDB offline index and fetches all songsets when online, client-side.
 * The SW pre-caches this document so it boots with zero connectivity.
 */
export default async function WorshipPage() {
  const session = await auth.api.getSession({ headers: await headers() });

  if (!session?.user) {
    redirect("/login");
  }

  return <WorshipClient />;
}
