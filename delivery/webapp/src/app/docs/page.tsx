import { t } from "@/lib/i18n/messages";
import type { Locale } from "@/lib/i18n/messages";
import { resolveUserLocale } from "@/lib/i18n/server";
import { BuildStamp } from "../page/BuildStamp";

export default async function DocsPage() {
  const locale = await resolveUserLocale();
  return <DocsContent locale={locale} />;
}

function DocsContent({ locale }: { locale: Locale }) {
  return (
    <div className="flex flex-col min-h-[60vh]">
      {/* Hero */}
      <section className="gradient-hero border-b border-border">
        <div className="mx-auto max-w-3xl px-4 py-20 md:py-28 text-center">
          <h1 className="text-4xl md:text-5xl font-bold tracking-tight leading-tight">
            {t(locale, "docs.heroTitle")}
          </h1>
          <p className="text-muted-foreground text-lg mt-6 max-w-2xl mx-auto">
            {t(locale, "docs.heroDescription")}
          </p>
        </div>
      </section>

      {/* AirPlay from iPhone / iPad (the controller chip links to /docs#airplay) */}
      <section id="airplay" className="mx-auto max-w-3xl px-4 py-16 scroll-mt-16">
        <h2 className="text-2xl font-bold mb-4">{t(locale, "docs.airplay.title")}</h2>
        <p className="text-muted-foreground leading-relaxed mb-4">
          {t(locale, "docs.airplay.p1")}
        </p>
        <p className="text-muted-foreground leading-relaxed">
          {t(locale, "docs.airplay.p2")}
        </p>
      </section>

      {/* Workarounds */}
      <section className="bg-muted/50 border-y border-border">
        <div className="mx-auto max-w-3xl px-4 py-16">
          <h2 className="text-2xl font-bold mb-6">
            {t(locale, "docs.airplay.workaroundTitle")}
          </h2>
          <ol className="list-decimal space-y-2 text-muted-foreground leading-relaxed">
            <li>{t(locale, "docs.airplay.workaround.1")}</li>
            <li>{t(locale, "docs.airplay.workaround.2")}</li>
            <li>{t(locale, "docs.airplay.workaround.3")}</li>
          </ol>
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