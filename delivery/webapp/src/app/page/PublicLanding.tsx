import Link from "next/link";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { t } from "@/lib/i18n/messages";
import type { Locale } from "@/lib/i18n/messages";
import { BuildStamp } from "./BuildStamp";
import { FileMusic, Video, Cast } from "lucide-react";

const FEATURES = [
  { icon: FileMusic, titleKey: "home.signedOut.feature.build", descKey: "home.signedOut.feature.buildDesc" },
  { icon: Video, titleKey: "home.signedOut.feature.render", descKey: "home.signedOut.feature.renderDesc" },
  { icon: Cast, titleKey: "home.signedOut.feature.cast", descKey: "home.signedOut.feature.castDesc" },
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
      <section className="gradient-hero border-b border-border">
        <div className="mx-auto max-w-6xl px-4 py-20 md:py-28 grid md:grid-cols-2 gap-12 items-center">
          <div className="space-y-6">
            <span className="inline-flex items-center gap-1.5 rounded-full border border-border bg-background/80 px-3 py-1 text-xs font-medium text-muted-foreground">
              ✦ {t(locale, "home.signedOut.heroTag")}
            </span>
            <h1 className="text-4xl md:text-5xl font-bold tracking-tight leading-tight">
              {t(locale, "home.signedOut.heroTitleLead")}
              <br />
              <span className="gradient-text">
                {t(locale, "home.signedOut.heroTitleAccent")}
              </span>
            </h1>
            <p className="text-muted-foreground text-lg max-w-md">
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

          {/* Static CSS mockup of the projected lyrics screen */}
          <div className="relative">
            <div className="rounded-2xl border-4 border-border bg-card shadow-xl overflow-hidden aspect-video">
              <div className="flex items-center gap-2 px-4 py-2 border-b border-border bg-muted/50">
                <Cast className="size-4 text-primary" />
                <span className="text-xs text-muted-foreground">Casting to Living Room TV</span>
              </div>
              <div className="px-6 py-8 space-y-3 min-h-[160px] flex flex-col justify-center">
                <p className="text-sm text-muted-foreground/60">奇妙十架與主恩</p>
                <p className="text-xl font-semibold gradient-text">奇異恩典 何等甘甜</p>
                <p className="text-sm text-muted-foreground/60">我罪已得赦免</p>
              </div>
              <div className="h-1 bg-muted">
                <div className="h-1 bg-primary w-2/3" />
              </div>
            </div>
            <div className="mt-3 flex items-center gap-2 text-xs text-muted-foreground">
              <span>▶</span>
              <span>小組敬拜 2026-08-23</span>
            </div>
            <div className="absolute -bottom-4 -left-4 rounded-lg border border-border bg-card shadow-lg px-3 py-2 flex items-center gap-2">
              <Cast className="size-4 text-primary" />
              <div>
                <div className="text-xs font-semibold">Lyrics on the TV</div>
                <div className="text-[10px] text-muted-foreground">No awkward interruptions</div>
              </div>
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
          <BuildStamp />
        </div>
      </footer>
    </div>
  );
}
