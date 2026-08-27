"use client";

import { useState } from "react";
import { BUILD_COMMIT_DATE, BUILD_COMMIT_HASH } from "@/lib/build-info";

export function BuildStamp() {
  const [revealed, setRevealed] = useState(false);

  return (
    <div className="flex flex-col items-end gap-1">
      <button
        type="button"
        onClick={() => setRevealed((v) => !v)}
        className="cursor-pointer transition-colors hover:text-foreground"
        aria-expanded={revealed}
        aria-controls="build-stamp-details"
        aria-label="Toggle build info"
      >
        © {new Date().getFullYear()}
      </button>
      {revealed && (
        <span
          id="build-stamp-details"
          className="font-mono text-xs"
          data-testid="build-stamp-visible"
        >
          {BUILD_COMMIT_HASH} {BUILD_COMMIT_DATE}
        </span>
      )}
      <span className="sr-only" data-testid="build-stamp">
        {BUILD_COMMIT_HASH} {BUILD_COMMIT_DATE}
      </span>
    </div>
  );
}
