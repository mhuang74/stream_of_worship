"use client";

import { useEffect } from "react";
import { useConnectivity } from "@/hooks/useConnectivity";

/**
 * Offline redirect guard (issue #211 follow-up, Q11): online-dependent
 * surfaces (dashboard, songsets, detail, favorites, settings) redirect to
 * /offline when the OS reports no network. `replace` — no history junk; a
 * full document navigation so the browser drops the dead SPA context and the
 * SW serves the pre-cached /offline document.
 *
 * Fires only on definitive Offline (`navigator.onLine === false`); never on
 * Unknown (in-flight/failed probe) — a cold boot must not bounce users whose
 * connectivity just has not been confirmed yet. Pages that must boot offline
 * (/offline, controllers, share, docs, login, projection) do not mount this.
 */
export function useOfflineRedirect(): void {
  const connectivity = useConnectivity();

  useEffect(() => {
    if (connectivity === "offline") {
      window.location.replace("/offline");
    }
  }, [connectivity]);
}
