import Link from "next/link";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { t } from "@/lib/i18n/messages";
import type { Locale } from "@/lib/i18n/messages";
import { FileMusic, Video, Share2 } from "lucide-react";

const FEATURES = [
  { icon: FileMusic, titleKey: "home.signedOut.feature.build", descKey: "home.signedOut.feature.buildDesc" },
  { icon: Video, titleKey: "home.signedOut.feature.render", descKey: "home.signedOut.feature.renderDesc" },
  { icon: Share2, titleKey: "home.signedOut.feature.share", descKey: "home.signedOut.feature.shareDesc" },
] as const;

const STEPS = [
  { titleKey: "home.signedOut.step1", descKey: "home.signedOut.step1Desc" },
  { titleKey: "home.signedOut.step2", descKey: "home.signedOut.step2Desc" },
  { titleKey: "home.signedOut.step3", descKey: "home.signedOut.step3Desc" },
  { titleKey: "home.signedOut.step4", descKey: "home.signedOut.step4Desc" },
] as const;

export function PublicLanding({ locale }: { locale: Locale }) {
  return (
    <div className="flex flex-col min-h-[60vh]">
      {/* Hero */}
      <section className="bg-gradient-to-b from-primary/5 via-background to-background">
        <div className="mx-auto max-w-6xl px-4 py-16 md:py-24 grid lg:grid-cols-2 gap-12 items-center">
          <div className="space-y-6">
            <span className="inline-flex items-center gap-1.5 rounded-full border border-border bg-background px-3 py-1 text-xs font-medium text-muted-foreground">
              ✦ {t(locale, "home.signedOut.heroTag")}
            </span>
            <h1 className="text-4xl md:text-5xl font-bold leading-tight">
              {t(locale, "home.signedOut.heroTitle")}
            </h1>
            <p className="text-muted-foreground max-w-md">
              {t(locale, "home.signedOut.heroDescription")}
            </p>
            <div className="flex flex-wrap items-center gap-3">
              <Link href="/register" className={cn(buttonVariants())}>
                {t(locale, "home.signedOut.ctaPrimary")}
              </Link>
              <Link
                href="/login"
                className={cn(buttonVariants({ variant: "outline" }))}
              >
                {t(locale, "home.signedOut.ctaSecondary")}
              </Link>
            </div>
            <p className="text-xs text-muted-foreground">{t(locale, "home.signedOut.ctaFooter")}</p>
          </div>

          {/* Static CSS mockup of the dashboard */}
          <div className="hidden lg:block rounded-xl border border-border bg-card p-4 shadow-sm">
            <div className="flex items-center gap-2 mb-4">
              <div className="size-8 rounded-full bg-primary/15" />
              <div className="flex-1 h-4 rounded bg-muted" />
              <div className="h-6 w-24 rounded-md bg-muted" />
              <div className="h-6 w-24 rounded-md bg-muted" />
            </div>
            <div className="grid grid-cols-5 gap-2 mb-4">
              {[0, 1, 2, 3, 4].map((i) => (
                <div key={i} className="rounded-md border border-border bg-muted/40 p-2">
                  <div className="size-4 rounded bg-muted mb-1.5" />
                  <div className="h-3 w-3/4 rounded bg-muted" />
                  <div className="h-4 w-1/2 rounded bg-muted mt-1" />
                </div>
              ))}
            </div>
            <div className="space-y-2">
              {[0, 1, 2].map((i) => (
                <div key={i} className="flex items-center gap-3 rounded-md border border-border p-2">
                  <div className="size-8 rounded bg-muted" />
                  <div className="flex-1 space-y-1">
                    <div className="h-3 w-2/3 rounded bg-muted" />
                    <div className="h-2 w-1/3 rounded bg-muted" />
                  </div>
                  <div className="h-5 w-16 rounded-md bg-muted" />
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* Features */}
      <section id="features" className="mx-auto max-w-6xl px-4 py-16">
        <div className="text-center mb-10">
          <h2 className="text-3xl font-bold">{t(locale, "home.signedOut.featuresTitle")}</h2>
          <p className="text-muted-foreground mt-2 max-w-2xl mx-auto">
            {t(locale, "home.signedOut.featuresDescription")}
          </p>
        </div>
        <div className="grid md:grid-cols-3 gap-6">
          {FEATURES.map((feature) => {
            const Icon = feature.icon;
            return (
              <div key={feature.titleKey} className="rounded-xl border border-border bg-card p-6 transition-shadow hover:shadow-md">
                <div className="size-10 rounded-lg bg-muted flex items-center justify-center mb-4">
                  <Icon className="size-5 text-muted-foreground" />
                </div>
                <h3 className="font-semibold mb-2">{t(locale, feature.titleKey)}</h3>
                <p className="text-sm text-muted-foreground">{t(locale, feature.descKey)}</p>
              </div>
            );
          })}
        </div>
      </section>

      {/* How it works */}
      <section id="how-it-works" className="bg-muted/50 border-y border-border">
        <div className="mx-auto max-w-6xl px-4 py-16">
          <h2 className="text-3xl font-bold text-center mb-10">
            {t(locale, "home.signedOut.howItWorksTitle")}
          </h2>
          <div className="grid md:grid-cols-4 gap-6">
            {STEPS.map((step, index) => (
              <div key={step.titleKey} className="text-center">
                <div className="mx-auto size-10 rounded-full bg-primary text-primary-foreground font-semibold flex items-center justify-center mb-3">
                  {index + 1}
                </div>
                <h3 className="font-semibold mb-1">{t(locale, step.titleKey)}</h3>
                <p className="text-sm text-muted-foreground">{t(locale, step.descKey)}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Bottom CTA */}
      <section className="mx-auto max-w-6xl px-4 py-16 text-center">
        <h2 className="text-3xl font-bold mb-2">{t(locale, "home.signedOut.ctaBottomTitle")}</h2>
        <p className="text-muted-foreground mb-6">{t(locale, "home.signedOut.ctaBottomDesc")}</p>
        <div className="flex flex-wrap justify-center gap-3">
          <Link href="/register" className={cn(buttonVariants())}>
            {t(locale, "home.signedOut.ctaBottomPrimary")}
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
          <span>© {new Date().getFullYear()}</span>
        </div>
      </footer>
    </div>
  );
}
