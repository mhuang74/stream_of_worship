"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const navItems = [
  { href: "/songsets", label: "Songsets" },
  { href: "/settings", label: "Settings" },
];

export function BottomNav() {
  const pathname = usePathname();

  if (pathname.includes("/play/controller") || pathname.startsWith("/share/")) {
    return null;
  }

  return (
    <nav
      className="lg:hidden fixed bottom-0 left-0 right-0 z-50 border-t border-border bg-background"
      aria-label="Main navigation"
    >
      <div className="flex h-16">
        {navItems.map((item) => {
          const isActive = pathname.startsWith(item.href);
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
              {item.label}
            </Link>
          );
        })}
      </div>
    </nav>
  );
}
