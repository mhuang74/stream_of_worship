"use client";

import Link from "next/link";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useLocale } from "@/hooks/useLocale";

export default function HomePage() {
  const { t } = useLocale();

  return (
    <div className="flex flex-col items-center justify-center min-h-[60vh] gap-6 px-4">
      <h1 className="text-3xl font-bold text-center">{t("home.title")}</h1>
      <p className="text-muted-foreground text-center max-w-md">
        {t("home.subtitle")}
      </p>
      <Link href="/songsets" className={cn(buttonVariants())}>
        {t("home.viewSongsets")}
      </Link>
    </div>
  );
}
