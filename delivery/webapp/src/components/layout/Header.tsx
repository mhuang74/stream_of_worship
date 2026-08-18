"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { isProjectionRoute } from "@/lib/routes";
import { useLocale } from "@/hooks/useLocale";

export function Header() {
  const pathname = usePathname();
  const { t } = useLocale();

  if (pathname?.startsWith("/share/") || isProjectionRoute(pathname)) {
    return null;
  }

  return (
    <header className="sticky top-0 z-50 w-full border-b border-border bg-background/95 backdrop-blur">
      <div className="flex h-14 items-center gap-4 px-4">
        <Link href="/" className="flex items-center gap-2 font-semibold">
          <span className="text-primary">{t("brand.name")}</span>
        </Link>
        <nav className="hidden lg:flex items-center gap-6 ml-6" aria-label={t("nav.main.ariaLabel")}>
          <Link
            href="/songsets"
            className="text-sm font-medium text-muted-foreground hover:text-foreground transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded"
          >
            {t("nav.songsets")}
          </Link>
          <Link
            href="/favorites"
            className="text-sm font-medium text-muted-foreground hover:text-foreground transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded"
          >
            {t("nav.favorites")}
          </Link>
          <Link
            href="/settings"
            className="text-sm font-medium text-muted-foreground hover:text-foreground transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded"
          >
            {t("nav.settings")}
          </Link>
        </nav>
        <div className="ml-auto" />
      </div>
    </header>
  );
}
