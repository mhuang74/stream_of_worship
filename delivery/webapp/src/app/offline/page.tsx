import { headers } from "next/headers";
import { redirect } from "next/navigation";
import { auth } from "@/lib/auth";
import { OfflineClient } from "./OfflineClient";

/**
 * /offline (issue #211 follow-up): the offline redirect target. Auth gate
 * only — the list itself renders purely from the IndexedDB offline index
 * client-side, and the SW pre-caches this document so it boots with zero
 * connectivity.
 */
export default async function OfflinePage() {
  const session = await auth.api.getSession({ headers: await headers() });

  if (!session?.user) {
    redirect("/login");
  }

  return <OfflineClient />;
}
