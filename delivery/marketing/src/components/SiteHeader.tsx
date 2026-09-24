import Link from "next/link";
import { t, type Locale } from "@/messages";
import { APP_URL } from "@/lib/urls";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * Marketing nav. Plain links only — the language toggle links to the
 * counterpart-locale page (no auto-detection, per issue #213).
 */
export function SiteHeader({ locale, path }: { locale: Locale; path: string }) {
  const otherHref = locale === "en" ? `/zh-Hant${path === "/" ? "" : path}` : path === "/" ? "/" : path.replace(/^\/zh-Hant/, "");
  const base = locale === "en" ? "" : "/zh-Hant";
  const currentLangCls = "font-medium underline underline-offset-4";
  const otherLangCls = "text-muted-foreground hover:text-foreground underline-offset-4 hover:underline";

  return (
    <header className="sticky top-0 z-50 w-full border-b border-border bg-background/95 backdrop-blur">
      <div className="flex min-h-14 flex-wrap items-center gap-x-3 gap-y-1 px-3 py-1.5 sm:px-4">
        <Link
          href={base === "" ? "/" : base}
          className="flex items-center gap-2 font-semibold whitespace-nowrap"
        >
          <span className="text-primary">{t(locale, "brand.name")}</span>
        </Link>
        <nav className="flex items-center gap-6" aria-label={t(locale, "nav.main.ariaLabel")}>
          <Link
            href={`${base}/about`}
            className="text-sm font-medium text-muted-foreground hover:text-foreground transition-colors whitespace-nowrap"
          >
            {t(locale, "nav.about")}
          </Link>
        </nav>
        <div className="ml-auto flex items-center gap-2">
          <Link href={`${APP_URL}/login`} className={cn(buttonVariants({ variant: "outline", size: "sm" }))}>
            {t(locale, "nav.signIn")}
          </Link>
          <Link
            href={`${APP_URL}/register`}
            className={cn(buttonVariants({ size: "sm" }), "hidden sm:inline-flex")}
          >
            {t(locale, "nav.register")}
          </Link>
          <nav
            aria-label={t(locale, "nav.language.ariaLabel")}
            className="ml-2 flex items-center gap-1 text-xs"
          >
            {locale === "en" ? (
              <span className={currentLangCls}>{t(locale, "nav.language.en")}</span>
            ) : (
              <Link href={otherHref} className={otherLangCls}>
                {t(locale, "nav.language.en")}
              </Link>
            )}
            <span aria-hidden className="text-muted-foreground">|</span>
            {locale === "zh-Hant" ? (
              <span className={currentLangCls}>{t(locale, "nav.language.zhHant")}</span>
            ) : (
              <Link href={otherHref} className={otherLangCls}>
                {t(locale, "nav.language.zhHant")}
              </Link>
            )}
          </nav>
        </div>
      </div>
    </header>
  );
}
