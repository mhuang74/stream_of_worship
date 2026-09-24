"use client";

import { useState } from "react";
import { useLocale } from "@/hooks/useLocale";
import { BUILD_COMMIT_DATE, BUILD_COMMIT_HASH } from "@/lib/build-info";

export function BuildStamp() {
  const { t } = useLocale();
  const [revealed, setRevealed] = useState(false);

  // Non-git environment: generator writes empty strings; nothing to show.
  if (!BUILD_COMMIT_HASH) return null;

  return (
    <div className="flex flex-col items-center gap-1">
      <button
        type="button"
        onClick={() => setRevealed((v) => !v)}
        className="cursor-pointer text-xs text-muted-foreground transition-colors hover:text-foreground"
        aria-expanded={revealed}
        aria-controls="build-stamp-details"
      >
        {t("build.info")}
      </button>
      {revealed && (
        <span
          id="build-stamp-details"
          className="font-mono text-xs text-muted-foreground"
          data-testid="build-stamp-details"
        >
          {BUILD_COMMIT_HASH} {BUILD_COMMIT_DATE}
        </span>
      )}
    </div>
  );
}