"use client";

import { useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Save, Loader2, ArrowRightLeft } from "lucide-react";
import { TransitionControls } from "./TransitionControls";
import { TransitionSettings } from "@/components/songset/TransitionPanel";
import { useAudioPlayerContext } from "@/contexts/AudioPlayerContext";
import { toast } from "sonner";
import { cn } from "@/lib/utils";

export interface TransitionSheetProps {
  isOpen: boolean;
  onOpenChange: (open: boolean) => void;
  fromSong?: { title: string; key?: string | null; tempoBpm?: number | null };
  toSong?: { title: string; key?: string | null; tempoBpm?: number | null };
  fromRecordingHash?: string;
  toRecordingHash?: string;
  settings: TransitionSettings;
  onSave: (settings: TransitionSettings) => Promise<void>;
  className?: string;
}

export function TransitionSheet({
  isOpen,
  onOpenChange,
  fromSong,
  toSong,
  fromRecordingHash,
  toRecordingHash,
  settings: initialSettings,
  onSave,
  className,
}: TransitionSheetProps) {
  const [settings, setSettings] = useState<TransitionSettings>(initialSettings);
  const [isSaving, setIsSaving] = useState(false);
  const [isPreviewLoading, setIsPreviewLoading] = useState(false);
  const { play } = useAudioPlayerContext();

  const hasChanges = JSON.stringify(settings) !== JSON.stringify(initialSettings);
  const hasAudio = !!(fromRecordingHash || toRecordingHash);

  const handlePreview = async () => {
    setIsPreviewLoading(true);
    try {
      const res = await fetch("/api/transitions/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          fromHash: fromRecordingHash,
          toHash: toRecordingHash,
          settings,
        }),
      });

      if (!res.ok) {
        throw new Error("Failed to get preview URL");
      }

      const data = await res.json();

      play({
        id: `transition-${fromRecordingHash ?? ""}-${toRecordingHash ?? ""}`,
        title: `${fromSong?.title ?? "Song"} → ${toSong?.title ?? "Song"}`,
        artist: "Transition Preview",
        src: data.url,
        type: "transition",
      });
    } catch {
      toast.error("Failed to load preview audio");
    } finally {
      setIsPreviewLoading(false);
    }
  };

  const handleSave = async () => {
    setIsSaving(true);
    try {
      await onSave(settings);
      onOpenChange(false);
    } catch {
      toast.error("Failed to save transition settings");
    } finally {
      setIsSaving(false);
    }
  };

  const handleCancel = () => {
    setSettings(initialSettings);
    onOpenChange(false);
  };

  if (!isOpen) return null;

  return (
    <Card className={cn("border-primary/20 bg-muted/30", className)}>
      <CardHeader className="pb-3">
        <CardTitle className="text-sm flex items-center gap-2 text-muted-foreground">
          <ArrowRightLeft className="size-4" />
          {fromSong && toSong ? (
            <span>
              <span className="text-foreground font-medium truncate max-w-[8rem] inline-block align-bottom">
                {fromSong.title}
              </span>
              {" → "}
              <span className="text-foreground font-medium truncate max-w-[8rem] inline-block align-bottom">
                {toSong.title}
              </span>
            </span>
          ) : (
            "Transition Settings"
          )}
        </CardTitle>
      </CardHeader>

      <CardContent className="space-y-4">
        <TransitionControls
          settings={settings}
          fromSong={fromSong}
          toSong={toSong}
          onChange={setSettings}
          onPreview={hasAudio ? handlePreview : undefined}
          isPreviewLoading={isPreviewLoading}
        />

        <div className="flex gap-2 pt-2 border-t">
          <Button
            variant="outline"
            size="sm"
            className="flex-1"
            onClick={handleCancel}
            disabled={isSaving}
          >
            Cancel
          </Button>
          <Button
            size="sm"
            className="flex-1"
            onClick={handleSave}
            disabled={isSaving || !hasChanges}
            aria-label="Save transition settings"
          >
            {isSaving ? (
              <Loader2 className="size-4 mr-2 animate-spin" />
            ) : (
              <Save className="size-4 mr-2" />
            )}
            Save
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
