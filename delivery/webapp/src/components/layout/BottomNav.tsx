"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { isProjectionRoute } from "@/lib/routes";
import { useLocale } from "@/hooks/useLocale";
import { useSession } from "@/lib/auth-client";
import { LanguageSwitcher } from "@/components/layout/LanguageSwitcher";

export function BottomNav() {
  const pathname = usePathname();
  const { t } = useLocale();
  const { data: session } = useSession();
  const user = session?.user;

  const navItems = [
    { href: "/", key: "nav.dashboard" as const },
    { href: "/songsets", key: "nav.songsets" as const },
    { href: "/favorites", key: "nav.favorites" as const },
  ];

  if (
    pathname?.includes("/play/controller") ||
    pathname?.startsWith("/share/") ||
    isProjectionRoute(pathname)
  ) {
    return null;
  }
  if (!user) {
    return (
      <nav
        className="lg:hidden fixed bottom-0 left-0 right-0 z-50 border-t border-border bg-background"
        aria-label={t("nav.about")}
      >
        <div className="flex h-16 items-center justify-between px-4">
          <Link
            href="/about"
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
