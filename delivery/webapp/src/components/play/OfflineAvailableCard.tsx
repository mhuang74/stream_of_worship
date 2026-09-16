"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Play, WifiOff } from "lucide-react";
import { useLocale } from "@/hooks/useLocale";

export interface OfflineAvailableCardProps {
  /** Songset name from the offline index record (the API is unreachable). */
  songsetName: string;
  onStartWorship: () => void;
}

/**
 * Play page offline entry (issue #206): the songset fetch failed with no
 * network and an offline index record exists — surface the downloaded set
 * instead of the error screen. Start Worship is a full document navigation,
 * which the service worker's document route serves from the pre-cached
 * controller HTML.
 */
export function OfflineAvailableCard({ songsetName, onStartWorship }: OfflineAvailableCardProps) {
  const { t } = useLocale();

  return (
    <div className="flex min-h-screen flex-col items-center justify-center p-4">
      <Card className="w-full max-w-md">
        <CardHeader className="items-center space-y-2 text-center">
          <div
            className="flex size-12 items-center justify-center rounded-full bg-muted"
            aria-hidden="true"
          >
            <WifiOff className="size-6 text-muted-foreground" />
          </div>
          <CardTitle>{t("play.offline.heading")}</CardTitle>
          <p className="text-sm text-muted-foreground">{t("play.offline.hint")}</p>
        </CardHeader>
        <CardContent className="flex flex-col items-center gap-4">
          <p className="text-center font-semibold">{songsetName}</p>
          <Button size="lg" className="w-full" onClick={onStartWorship}>
            <Play className="size-4" aria-hidden="true" />
            {t("preplay.startWorship")}
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}
