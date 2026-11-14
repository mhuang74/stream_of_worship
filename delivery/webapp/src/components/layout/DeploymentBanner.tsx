"use client";

import { usePathname } from "next/navigation";
import { AlertTriangle } from "lucide-react";
import { useLocale } from "@/hooks/useLocale";
import { isProjectionRoute } from "@/lib/routes";
import { getDeploymentEnv } from "@/lib/deployment-env";
import { BUILD_COMMIT_HASH } from "@/lib/build-info";

/**
 * Unmistakable non-production marker: a full-width amber strip above the
 * header on every route except projection. Production renders nothing.
 *
 * Deliberately in normal document flow (not sticky/fixed): the header already
 * owns `sticky top-0 z-50`, and a second sticky element at `top-0` would
 * overlap it. The strip is the first thing on every page load/navigation.
 */
export function DeploymentBanner() {
  const pathname = usePathname();
  const { t } = useLocale();
  const env = getDeploymentEnv();

  if (env === "production" || isProjectionRoute(pathname)) return null;

  const label = env === "preview" ? t("deployment.preview") : t("deployment.development");

  return (
    <div
      role="status"
      data-testid="deployment-banner"
      data-deployment={env}
      className="flex w-full items-center justify-center gap-2 bg-amber-500 px-3 py-1 text-xs font-semibold uppercase tracking-wide text-black"
    >
      <AlertTriangle className="size-3.5 shrink-0" aria-hidden="true" />
      <span>{label}</span>
      {BUILD_COMMIT_HASH && (
        <span className="font-mono normal-case opacity-80">{BUILD_COMMIT_HASH}</span>
      )}
    </div>
  );
}
