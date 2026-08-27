"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { isProjectionRoute } from "@/lib/routes";
import { useLocale } from "@/hooks/useLocale";
import { useSignOut } from "@/hooks/useSignOut";
import { useSession } from "@/lib/auth-client";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Button, buttonVariants } from "@/components/ui/button";
import { LogOut, Settings, User } from "lucide-react";
import { cn } from "@/lib/utils";

export function Header() {
  const pathname = usePathname();
  const router = useRouter();
  const { t } = useLocale();
  const { signOutAndRedirect } = useSignOut();
  const { data: session } = useSession();
  const user = session?.user;

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
          {user ? (
            <>
              <Link
                href="/"
                className="text-sm font-medium text-muted-foreground hover:text-foreground transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded"
              >
                {t("nav.dashboard")}
              </Link>
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
            </>
          ) : (
            <>
              <Link
                href="/about"
                className="text-sm font-medium text-muted-foreground hover:text-foreground transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded"
              >
                {t("nav.about")}
              </Link>
            </>
          )}
        </nav>
        <div className="ml-auto flex items-center gap-2">
          {user ? (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" size="icon-sm" className="rounded-full size-8">
                  <Avatar className="size-8">
                    <AvatarFallback className="bg-primary text-primary-foreground text-xs font-semibold">
                      {user.name?.charAt(0).toUpperCase() ?? "?"}
                    </AvatarFallback>
                  </Avatar>
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-48">
                <DropdownMenuItem disabled className="text-xs text-muted-foreground">
                  <User className="size-4 mr-2" />
                  {user.name}
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem onClick={() => router.push("/settings")}>
                  <Settings className="size-4 mr-2" />
                  {t("nav.settings")}
                </DropdownMenuItem>
                <DropdownMenuItem onClick={signOutAndRedirect}>
                  <LogOut className="size-4 mr-2" />
                  {t("nav.signOut")}
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          ) : (
            <>
              <Link
                href="/login"
                className={cn(buttonVariants({ variant: "outline", size: "sm" }))}
              >
                {t("auth.signIn.submit")}
              </Link>
              <Link href="/register" className={cn(buttonVariants({ size: "sm" }))}>
                {t("auth.register.submit")}
              </Link>
            </>
          )}
        </div>
      </div>
    </header>
  );
}
