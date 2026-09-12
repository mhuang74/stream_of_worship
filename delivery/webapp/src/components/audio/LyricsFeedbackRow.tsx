"use client";

import { useState } from "react";
import { Smile, Frown } from "lucide-react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { useLocale } from "@/hooks/useLocale";
import type { TranslationKey } from "@/lib/i18n/messages";
import { useLyricsFeedback, type FeedbackReason } from "@/hooks/useLyricsFeedback";

export type LyricsSituationKind = "synced" | "unsynced" | "none";

export interface LyricsFeedbackRowProps {
  recordingContentHash: string;
  /** What lyrics content is on screen — filters the sad-reason chips. */
  situation: LyricsSituationKind;
  className?: string;
}

/** Chips offered per lyrics situation; must agree with the server's
 * state-aware validation matrix (issue #194). */
function chipsForSituation(
  situation: LyricsSituationKind,
  t: (key: TranslationKey) => string
): Array<{ reason: FeedbackReason; label: string }> {
  const chips: Array<{ reason: FeedbackReason; label: string }> = [];
  if (situation !== "synced") {
    chips.push({ reason: "missing", label: t("audio.feedback.reasonMissing") });
  }
  if (situation === "synced") {
    chips.push({ reason: "timing", label: t("audio.feedback.reasonTiming") });
  }
  chips.push(
    { reason: "wrong_text", label: t("audio.feedback.reasonWrongText") },
    { reason: "other", label: t("audio.feedback.reasonOther") }
  );
  return chips;
}

/**
 * Slim one-row Lyrics Feedback affordance (issue #194): happy + sad icons,
 * right-aligned, with an inline reason-chip expansion on sad. Tapping the
 * active icon again retracts; switching happy↔sad overwrites.
 */
export function LyricsFeedbackRow({
  recordingContentHash,
  situation,
  className,
}: LyricsFeedbackRowProps) {
  const { t } = useLocale();
  const { feedback, submit, retract } = useLyricsFeedback(recordingContentHash);
  const [sadExpanded, setSadExpanded] = useState(false);

  const happyActive = feedback?.rating === "happy";
  const sadActive = feedback?.rating === "sad";

  const runFeedback = (op: Promise<boolean>) =>
    void op.then((ok) => {
      if (!ok) toast.error(t("audio.feedback.saveFailed"));
    });

  const handleHappy = () => {
    if (happyActive) {
      runFeedback(retract());
    } else {
      setSadExpanded(false);
      runFeedback(submit("happy"));
    }
  };

  const handleSad = () => {
    if (sadActive) {
      runFeedback(retract());
      setSadExpanded(false);
    } else {
      setSadExpanded((open) => !open);
    }
  };

  const handleChip = (reason: FeedbackReason) => {
    setSadExpanded(false);
    runFeedback(submit("sad", reason));
  };

  const chips = chipsForSituation(situation, t);

  return (
    <div
      className={cn("flex items-center justify-end gap-1 px-3 lg:px-4 py-1.5", className)}
      data-testid="lyrics-feedback-row"
    >
      {situation !== "none" && (
        <button
          type="button"
          onClick={handleHappy}
          aria-pressed={happyActive}
          aria-label={t("audio.feedback.happyAriaLabel")}
          className={cn(
            "rounded-full p-1.5 transition-colors",
            happyActive ? "text-primary" : "text-muted-foreground hover:text-foreground"
          )}
        >
          <Smile
            className={cn("size-4", happyActive && "fill-current/35")}
            aria-hidden="true"
          />
        </button>
      )}
      <button
        type="button"
        onClick={handleSad}
        aria-pressed={sadActive}
        aria-label={t("audio.feedback.sadAriaLabel")}
        className={cn(
          "rounded-full p-1.5 transition-colors",
          sadActive ? "text-destructive" : "text-muted-foreground hover:text-foreground"
        )}
      >
        <Frown className={cn("size-4", sadActive && "fill-current/35")} aria-hidden="true" />
      </button>
      {sadExpanded && (
        <div className="flex items-center gap-1.5">
          {chips.map((chip) => (
            <button
              key={chip.reason}
              type="button"
              onClick={() => handleChip(chip.reason)}
              className="rounded-full border border-border px-2.5 py-0.5 text-xs text-muted-foreground hover:bg-accent hover:text-accent-foreground transition-colors"
            >
              {chip.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}