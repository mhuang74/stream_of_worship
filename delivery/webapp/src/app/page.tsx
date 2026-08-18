import Link from "next/link";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { resolveUserLocale } from "@/lib/i18n/server";
import { t } from "@/lib/i18n/messages";

export default async function HomePage() {
  const locale = await resolveUserLocale();

  return (
    <div className="flex flex-col items-center justify-center min-h-[60vh] gap-6 px-4">
      <h1 className="text-3xl font-bold text-center">{t(locale, "home.title")}</h1>
      <p className="text-muted-foreground text-center max-w-md">
        {t(locale, "home.subtitle")}
      </p>
      <Link href="/songsets" className={cn(buttonVariants())}>
        {t(locale, "home.viewSongsets")}
      </Link>
    </div>
  );
}
