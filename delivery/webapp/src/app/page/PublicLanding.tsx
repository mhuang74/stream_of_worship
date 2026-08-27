import Link from "next/link";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { t } from "@/lib/i18n/messages";
import type { Locale } from "@/lib/i18n/messages";
import { BuildStamp } from "./BuildStamp";
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

          {/* Static CSS mockup of the dashboard */}
          <div className="relative">
            <div className="rounded-xl border border-border bg-card shadow-xl overflow-hidden">
              <div className="flex items-center gap-1.5 px-3 py-2 border-b border-border bg-muted/50">
                <div className="size-2.5 rounded-full bg-red-400" />
                <div className="size-2.5 rounded-full bg-yellow-400" />
                <div className="size-2.5 rounded-full bg-green-400" />
                <span className="ml-2 text-xs text-muted-foreground">streamofworship.app</span>
              </div>
              <div className="p-4 space-y-3">
                <div className="flex items-center justify-between">
                  <div className="text-sm font-semibold">Welcome back, Michael</div>
                  <div className="text-xs text-muted-foreground">Dashboard</div>
                </div>
                <div className="grid grid-cols-5 gap-2">
                  {[
                    { value: "12", label: "Created" },
                    { value: "8", label: "Rendered" },
                    { value: "5", label: "Shared" },
                    { value: "23", label: "Favorites" },
                    { value: "340", label: "Catalog" },
                  ].map((stat) => (
                    <div key={stat.label} className="rounded-lg bg-muted p-2 text-center">
                      <div className="text-lg font-bold">{stat.value}</div>
                      <div className="text-[10px] text-muted-foreground">{stat.label}</div>
                    </div>
                  ))}
                </div>
                <div className="rounded-lg border border-border p-3">
                  <div className="text-xs font-medium mb-2">Recent songsets</div>
                  <div className="space-y-1.5">
                    <div className="flex items-center justify-between rounded-md bg-muted/50 px-2 py-1.5">
                      <div className="text-xs font-medium">主日敬拜 2026-08-23</div>
                      <div className="text-[10px] text-muted-foreground">6 songs · 24:30</div>
                    </div>
                    <div className="flex items-center justify-between rounded-md bg-muted/50 px-2 py-1.5">
                      <div className="text-xs font-medium">Youth Night Set</div>
                      <div className="text-[10px] text-muted-foreground">4 songs · 18:12</div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
            <div className="absolute -bottom-4 -left-4 rounded-lg border border-border bg-card shadow-lg px-3 py-2 flex items-center gap-2">
              <span className="text-lg">🎵</span>
              <div>
                <div className="text-xs font-semibold">Smooth transitions</div>
                <div className="text-[10px] text-muted-foreground">Key & tempo matched</div>
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
