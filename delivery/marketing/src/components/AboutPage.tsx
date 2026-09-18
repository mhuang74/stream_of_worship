import Link from "next/link";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { t, type Locale } from "@/messages";
import { APP_URL } from "@/lib/urls";
import { SiteHeader } from "@/components/SiteHeader";
import { SiteFooter } from "@/components/SiteFooter";

export function AboutPage({ locale }: { locale: Locale }) {
  return (
    <div className="flex flex-col min-h-[60vh]">
      <SiteHeader locale={locale} path="/about" />

      {/* Hero */}
      <section className="gradient-hero border-b border-border">
        <div className="mx-auto max-w-3xl px-4 py-20 md:py-28 text-center">
          <h1 className="text-4xl md:text-5xl font-bold tracking-tight leading-tight">
            {t(locale, "about.heroTitle")}
          </h1>
          <p className="text-muted-foreground text-lg mt-6 max-w-2xl mx-auto">
            {t(locale, "about.heroDescription")}
          </p>
        </div>
      </section>

      {/* Why I built this */}
      <section className="mx-auto max-w-3xl px-4 py-16">
        <h2 className="text-2xl font-bold mb-4">{t(locale, "about.whyTitle")}</h2>
        <p className="text-muted-foreground leading-relaxed mb-4">
          {t(locale, "about.whyPara1")}
        </p>
        <p className="text-muted-foreground leading-relaxed mb-4">
          {t(locale, "about.whyPara2")}
        </p>
        <p className="text-muted-foreground leading-relaxed">
          {t(locale, "about.whyPara3")}
        </p>
      </section>

      {/* What this tool does */}
      <section className="bg-muted/50 border-y border-border">
        <div className="mx-auto max-w-3xl px-4 py-16">
          <h2 className="text-2xl font-bold mb-6">{t(locale, "about.whatTitle")}</h2>
          <p className="text-muted-foreground leading-relaxed mb-4">
            {t(locale, "about.whatPara1")}
          </p>
          <p className="text-muted-foreground leading-relaxed">
            {t(locale, "about.whatPara2")}
          </p>
        </div>
      </section>

      {/* Bottom CTA */}
      <section className="mx-auto max-w-3xl px-4 py-16 text-center">
        <h2 className="text-3xl font-bold mb-2">{t(locale, "about.ctaTitle")}</h2>
        <p className="text-muted-foreground mb-6">{t(locale, "about.ctaDescription")}</p>
        <div className="flex flex-wrap justify-center gap-3">
          <Link href={`${APP_URL}/register`} className={cn(buttonVariants())}>
            {t(locale, "home.signedOut.ctaPrimary")}
          </Link>
          <Link href={`${APP_URL}/login`} className={cn(buttonVariants({ variant: "outline" }))}>
            {t(locale, "home.signedOut.ctaSecondary")}
          </Link>
        </div>
      </section>

      <SiteFooter locale={locale} />
    </div>
  );
}
