import Link from "next/link";
import { t, type Locale } from "@/messages";

/**
 * Marketing nav. Plain links only — the language toggle links to the
 * counterpart-locale page (no auto-detection, per issue #213).
 */
export function SiteHeader({ locale, path }: { locale: Locale; path: string }) {
  const other: Locale = locale === "en" ? "zh-Hant" : "en";
  const otherHref = locale === "en" ? `/zh-Hant${path === "/" ? "" : path}` : path === "/" ? "/" : path.replace(/^\/zh-Hant/, "");
  const base = locale === "en" ? "" : "/zh-Hant";

  return (
    <header className="sticky top-0 z-50 w-full border-b border-border bg-background/95 backdrop-blur">
      <div className="flex h-14 items-center gap-2 px-2">
        <Link
          href={base === "" ? "/" : base}
          className="flex items-center gap-2 font-semibold whitespace-nowrap"
        >
          <span className="text-primary">{t(locale, "brand.name")}</span>
        </Link>
        <nav className="flex items-center gap-3 ml-2" aria-label={t(locale, "nav.main.ariaLabel")}>
          <Link
            href={`${base}/`}
            className="text-sm font-medium text-muted-foreground hover:text-foreground transition-colors whitespace-nowrap"
          >
            {t(locale, "nav.home")}
          </Link>
          <Link
            href={`${base}/about`}
            className="text-sm font-medium text-muted-foreground hover:text-foreground transition-colors whitespace-nowrap"
          >
            {t(locale, "nav.about")}
          </Link>
          <Link
            href={`${base}/docs`}
            className="text-sm font-medium text-muted-foreground hover:text-foreground transition-colors whitespace-nowrap"
          >
            {t(locale, "nav.docs")}
          </Link>
          <Link
            href={otherHref}
            className="text-sm font-medium text-muted-foreground hover:text-foreground transition-colors whitespace-nowrap rounded border border-border px-2 py-0.5"
          >
            {other === "zh-Hant" ? "繁體中文" : "English"}
          </Link>
        </nav>
      </div>
    </header>
  );
}
