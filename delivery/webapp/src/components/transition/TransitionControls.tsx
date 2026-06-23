"use client";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Volume2, Play, Loader2, ArrowRightLeft, Piano, Gauge } from "lucide-react";
import { TransitionSettings } from "@/components/songset/TransitionPanel";

// Deterministic waveform bars using sine waves (avoids hydration mismatch)
const WAVEFORM_BARS = Array.from({ length: 40 }, (_, i) => {
  const h = 20 + Math.sin(i * 0.4) * 10 + Math.sin(i * 1.1) * 8 + Math.sin(i * 2.3) * 4;
  return Math.max(4, Math.min(48, h));
});

const KEY_SHIFT_OPTIONS = [
  { value: -6, label: "-6 (tritone down)" },
  { value: -5, label: "-5" },
  { value: -4, label: "-4" },
  { value: -3, label: "-3 (minor third down)" },
  { value: -2, label: "-2" },
  { value: -1, label: "-1" },
  { value: 0, label: "No shift" },
  { value: 1, label: "+1" },
  { value: 2, label: "+2" },
  { value: 3, label: "+3 (minor third up)" },
  { value: 4, label: "+4" },
  { value: 5, label: "+5" },
  { value: 6, label: "+6 (tritone up)" },
];

function gapToSeconds(beats: number, tempoBpm?: number | null): number {
  const bpm = tempoBpm || 120;
  return (beats / bpm) * 60;
}

export interface TransitionControlsProps {
  settings: TransitionSettings;
  fromSong?: { title: string; key?: string | null; tempoBpm?: number | null };
  toSong?: { title: string; key?: string | null; tempoBpm?: number | null };
  onChange: (settings: TransitionSettings) => void;
  onPreview?: () => void;
  isPreviewLoading?: boolean;
}

export function TransitionControls({
  settings,
  fromSong,
  toSong,
  onChange,
  onPreview,
  isPreviewLoading = false,
}: TransitionControlsProps) {
  const update = (patch: Partial<TransitionSettings>) => onChange({ ...settings, ...patch });

  const refBpm = fromSong?.tempoBpm || toSong?.tempoBpm;
  const gapSeconds = gapToSeconds(settings.gapBeats, refBpm);
  const currentBpm = refBpm ? Math.round(refBpm * settings.tempoRatio) : null;
  const bpmDelta = refBpm ? Math.round(refBpm * settings.tempoRatio) - refBpm : 0;

  const nudgeTempo = (deltaBpm: number) => {
    if (!refBpm) return;
    const newBpm = Math.max(40, Math.min(240, refBpm * settings.tempoRatio + deltaBpm));
    update({ tempoRatio: newBpm / refBpm });
  };

  return (
    <div className="space-y-5">
      {/* Gap control */}
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <Label className="flex items-center gap-2 text-sm">
            <ArrowRightLeft className="size-4" />
            Gap
          </Label>
          <span className="text-sm font-medium tabular-nums" aria-label="gap value">
            {settings.gapBeats} beats ({gapSeconds.toFixed(1)}s)
          </span>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="icon-sm"
            onClick={() => update({ gapBeats: Math.max(0, settings.gapBeats - 0.5) })}
            aria-label="Decrease gap by 0.5 beats"
            disabled={settings.gapBeats <= 0}
          >
            -
          </Button>
          <div className="flex-1 h-2 bg-muted rounded-full relative overflow-hidden">
            <div
              className="h-full bg-primary rounded-full transition-all"
              style={{ width: `${Math.min(100, (settings.gapBeats / 8) * 100)}%` }}
              role="progressbar"
              aria-valuenow={settings.gapBeats}
              aria-valuemin={0}
              aria-valuemax={8}
            />
          </div>
          <Button
            variant="outline"
            size="icon-sm"
            onClick={() => update({ gapBeats: Math.min(8, settings.gapBeats + 0.5) })}
            aria-label="Increase gap by 0.5 beats"
            disabled={settings.gapBeats >= 8}
          >
            +
          </Button>
        </div>
      </div>

      {/* Crossfade toggle */}
      <div className="flex items-center justify-between">
        <Label htmlFor="crossfade-ctrl" className="flex items-center gap-2 text-sm">
          <Volume2 className="size-4" />
          Crossfade
        </Label>
        <Switch
          id="crossfade-ctrl"
          checked={settings.crossfadeEnabled}
          onCheckedChange={(checked) => update({ crossfadeEnabled: checked })}
        />
      </div>

      {/* Audio preview button */}
      {onPreview && (
        <Button
          onClick={onPreview}
          variant="outline"
          className="w-full"
          disabled={isPreviewLoading}
          aria-label="Preview transition audio"
        >
          {isPreviewLoading ? (
            <Loader2 className="size-4 mr-2 animate-spin" />
          ) : (
            <Play className="size-4 mr-2" />
          )}
          Preview Transition
        </Button>
      )}

      {/* Desktop-only controls */}
      <div className="hidden lg:space-y-5 lg:block">
        {/* Key shift */}
        <div className="space-y-2">
          <Label className="flex items-center gap-2 text-sm">
            <Piano className="size-4" />
            Key Shift
          </Label>
          <Select
            value={settings.keyShiftSemitones.toString()}
            onValueChange={(v) => update({ keyShiftSemitones: parseInt(v ?? "0", 10) })}
          >
            <SelectTrigger aria-label="Key shift selector">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {KEY_SHIFT_OPTIONS.map((opt) => (
                <SelectItem key={opt.value} value={opt.value.toString()}>
                  {opt.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        {/* Tempo nudge */}
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <Label className="flex items-center gap-2 text-sm">
              <Gauge className="size-4" />
              Tempo
              {currentBpm && (
                <span className="text-muted-foreground font-normal">
                  {currentBpm} BPM
                  {bpmDelta !== 0 && (
                    <span
                      className={bpmDelta > 0 ? "text-blue-500" : "text-orange-500"}
                      aria-label={`tempo delta ${bpmDelta > 0 ? "+" : ""}${bpmDelta} BPM`}
                    >
                      {" "}
                      ({bpmDelta > 0 ? "+" : ""}
                      {bpmDelta})
                    </span>
                  )}
                </span>
              )}
            </Label>
            {bpmDelta !== 0 && (
              <button
                onClick={() => update({ tempoRatio: 1.0 })}
                className="text-xs text-muted-foreground hover:text-foreground underline"
                aria-label="Reset tempo to original"
              >
                Reset
              </button>
            )}
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="icon-sm"
              onClick={() => nudgeTempo(-1)}
              aria-label="Decrease tempo by 1 BPM"
              disabled={!refBpm}
            >
              -
            </Button>
            <div className="flex-1 text-center text-xs text-muted-foreground">
              {refBpm ? `${Math.round(settings.tempoRatio * 100)}%` : "No BPM data"}
            </div>
            <Button
              variant="outline"
              size="icon-sm"
              onClick={() => nudgeTempo(1)}
              aria-label="Increase tempo by 1 BPM"
              disabled={!refBpm}
            >
              +
            </Button>
          </div>
        </div>

        {/* Waveform preview panel */}
        <div className="space-y-1">
          <Label className="text-xs text-muted-foreground">Waveform</Label>
          <div
            className="flex items-center gap-[2px] h-12 px-2 bg-muted/50 rounded-md overflow-hidden"
            aria-label="Waveform preview"
            role="img"
          >
            {WAVEFORM_BARS.map((h, i) => (
              <div
                key={i}
                className="w-1 bg-primary/40 rounded-sm flex-shrink-0"
                style={{ height: `${h}px` }}
              />
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
