import Link from "next/link";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { t } from "@/lib/i18n/messages";
import type { Locale } from "@/lib/i18n/messages";
import { resolveUserLocale } from "@/lib/i18n/server";
import { BuildStamp } from "../page/BuildStamp";

export default async function AboutPage() {
  const locale = await resolveUserLocale();
  return <AboutContent locale={locale} />;
}

function AboutContent({ locale }: { locale: Locale }) {
  return (
    <div className="flex flex-col min-h-[60vh]">
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
          <Link href="/register" className={cn(buttonVariants())}>
            {t(locale, "home.signedOut.ctaPrimary")}
          </Link>
          <Link href="/login" className={cn(buttonVariants({ variant: "outline" }))}>
            {t(locale, "home.signedOut.ctaSecondary")}
          </Link>
        </div>
      </section>

      {/* Footer */}
      <footer className="border-t border-border">
        <div className="mx-auto max-w-6xl px-4 py-6 flex items-center justify-between text-sm text-muted-foreground">
          <span>{t(locale, "brand.name")}</span>
          <BuildStamp />
        </div>
      </footer>
    </div>
  );
}
