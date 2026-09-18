import { t, type Locale } from "@/messages";

export function SiteFooter({ locale }: { locale: Locale }) {
  return (
    <footer className="border-t border-border">
      <div className="mx-auto max-w-6xl px-4 py-6 flex items-center justify-between text-sm text-muted-foreground">
        <span>{t(locale, "brand.name")}</span>
        <span>© {new Date().getFullYear()}</span>
      </div>
    </footer>
  );
}
