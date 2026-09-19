"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { isProjectionRoute } from "@/lib/routes";
import { useLocale } from "@/hooks/useLocale";
import { getMarketingUrl } from "@/lib/marketing-url";
import { useSession } from "@/lib/auth-client";
import { useConnectivity } from "@/hooks/useConnectivity";
import { LanguageSwitcher } from "@/components/layout/LanguageSwitcher";

export function BottomNav() {
  const pathname = usePathname();
  const { t, locale } = useLocale();
  const { data: session } = useSession();
  const user = session?.user;
  const connectivity = useConnectivity();

  const navItems = [
    { href: "/", key: "nav.dashboard" as const },
    { href: "/songsets", key: "nav.songsets" as const },
    { href: "/worship", key: "nav.worship" as const },
    { href: "/favorites", key: "nav.favorites" as const },
  ];

  if (
    pathname?.includes("/play/controller") ||
    pathname?.startsWith("/share/") ||
    // /worship is the offline boot target: the SW pre-caches its document on
    // authed page boots only, so an offline boot implies a signed-in user
    // (and useSession cannot resolve offline — it would pin the signed-out
    // About bar). Online, show the nav once the session settles signed-in;
    // the genuinely signed-out are server-redirected to /login and get
    // nothing (this also suppresses the unresolved-session flash).
    (pathname === "/worship" && !(user || connectivity === "offline")) ||
    isProjectionRoute(pathname)
  ) {
    return null;
  }
  // Offline, useSession cannot resolve — treat offline boots as signed-in
  // (see /worship note above) and render the full nav instead of the
  // signed-out About bar, which would be permanently wrong offline.
  if (!user && connectivity !== "offline") {
    return (
      <nav
        className="lg:hidden fixed bottom-0 left-0 right-0 z-50 border-t border-border bg-background"
        aria-label={t("nav.main.ariaLabel")}
      >
        <div className="flex h-16 items-center justify-between px-4">
          <Link
            href={`${getMarketingUrl(locale)}/about`}
            className="pl-2 text-xs font-medium text-muted-foreground hover:text-foreground transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded"
          >
            {t("nav.about")}
          </Link>
          <LanguageSwitcher />
        </div>
      </nav>
    );
  }

  return (
    <nav
      className="lg:hidden fixed bottom-0 left-0 right-0 z-50 border-t border-border bg-background"
      aria-label={t("nav.main.ariaLabel")}
    >
      <div className="flex h-16">
        {navItems.map((item) => {
          const isActive =
            item.href === "/" ? pathname === "/" : pathname?.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              aria-current={isActive ? "page" : undefined}
              className={`flex-1 flex flex-col items-center justify-center text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
                isActive
                  ? "text-primary"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              {t(item.key)}
            </Link>
          );
        })}
      </div>
    </nav>
  );
}
