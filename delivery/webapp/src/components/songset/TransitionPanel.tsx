"use client";

import { useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Slider } from "@/components/ui/slider";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Volume2, Music, ArrowRightLeft, Gauge, Piano } from "lucide-react";
import { cn } from "@/lib/utils";

export interface TransitionSettings {
  gapBeats: number;
  crossfadeEnabled: boolean;
  crossfadeDurationSeconds: number;
  keyShiftSemitones: number;
  tempoRatio: number;
}

interface TransitionPanelProps {
  fromSong?: {
    title: string;
    key?: string | null;
    tempoBpm?: number | null;
  };
  toSong?: {
    title: string;
    key?: string | null;
    tempoBpm?: number | null;
  };
  settings: TransitionSettings;
  onChange: (settings: TransitionSettings) => void;
  onPreview?: () => void;
  isOpen?: boolean;
  onOpenChange?: (open: boolean) => void;
  className?: string;
}

// Key options for key shift (-6 to +6 semitones)
const KEY_SHIFT_OPTIONS = [
  { value: -6, label: "-6 (down)" },
  { value: -5, label: "-5" },
  { value: -4, label: "-4" },
  { value: -3, label: "-3" },
  { value: -2, label: "-2" },
  { value: -1, label: "-1" },
  { value: 0, label: "No shift" },
  { value: 1, label: "+1" },
  { value: 2, label: "+2" },
  { value: 3, label: "+3" },
  { value: 4, label: "+4" },
  { value: 5, label: "+5" },
  { value: 6, label: "+6 (up)" },
];

// Tempo ratio options
const TEMPO_OPTIONS = [
  { value: 0.8, label: "80% (slower)" },
  { value: 0.85, label: "85%" },
  { value: 0.9, label: "90%" },
  { value: 0.95, label: "95%" },
  { value: 1.0, label: "100% (normal)" },
  { value: 1.05, label: "105%" },
  { value: 1.1, label: "110%" },
  { value: 1.15, label: "115%" },
  { value: 1.2, label: "120% (faster)" },
];

// Phone layout content
function PhoneLayout({
  settings,
  onChange,
  onPreview,
}: {
  settings: TransitionSettings;
  onChange: (settings: TransitionSettings) => void;
  onPreview?: () => void;
}) {
  const handleChange = (updates: Partial<TransitionSettings>) => {
    onChange({ ...settings, ...updates });
  };

  return (
    <div className="space-y-6">
      {/* Gap control */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <Label className="flex items-center gap-2">
            <ArrowRightLeft className="size-4" />
            Gap Between Songs
          </Label>
          <span className="text-sm font-medium">{settings.gapBeats} beats</span>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="icon-sm"
            onClick={() => handleChange({ gapBeats: Math.max(0, settings.gapBeats - 0.5) })}
            aria-label="Decrease gap"
          >
            -
          </Button>
          <Slider
            value={[settings.gapBeats]}
            onValueChange={(value) => handleChange({ gapBeats: (Array.isArray(value) ? value[0] : value) as number })}
            min={0}
            max={8}
            step={0.5}
            className="flex-1"
          />
          <Button
            variant="outline"
            size="icon-sm"
            onClick={() => handleChange({ gapBeats: Math.min(8, settings.gapBeats + 0.5) })}
            aria-label="Increase gap"
          >
            +
          </Button>
        </div>
        <p className="text-xs text-muted-foreground">
          Time between songs: ~{Math.round(settings.gapBeats * 0.5)} seconds
        </p>
      </div>

      {/* Crossfade toggle */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Volume2 className="size-4" />
          <Label htmlFor="crossfade">Crossfade</Label>
        </div>
        <Switch
          id="crossfade"
          checked={settings.crossfadeEnabled}
          onCheckedChange={(checked) => handleChange({ crossfadeEnabled: checked })}
        />
      </div>

      {/* Preview button */}
      {onPreview && (
        <Button onClick={onPreview} className="w-full" variant="outline">
          <Music className="size-4 mr-2" />
          Preview Transition
        </Button>
      )}
    </div>
  );
}

// Desktop layout content
function DesktopLayout({
  fromSong,
  toSong,
  settings,
  onChange,
  onPreview,
}: {
  fromSong?: { title: string; key?: string | null; tempoBpm?: number | null };
  toSong?: { title: string; key?: string | null; tempoBpm?: number | null };
  settings: TransitionSettings;
  onChange: (settings: TransitionSettings) => void;
  onPreview?: () => void;
}) {
  const handleChange = (updates: Partial<TransitionSettings>) => {
    onChange({ ...settings, ...updates });
  };

  const formatKeyShift = (semitones: number) => {
    if (semitones === 0) return "No shift";
    return semitones > 0 ? `+${semitones}` : `${semitones}`;
  };

  const formatTempo = (ratio: number) => {
    return `${Math.round(ratio * 100)}%`;
  };

  return (
    <div className="space-y-6">
      {/* Song info header */}
      {(fromSong || toSong) && (
        <div className="flex items-center gap-4 text-sm">
          {fromSong && (
            <div className="flex-1 min-w-0">
              <p className="text-muted-foreground text-xs">From</p>
              <p className="font-medium truncate">{fromSong.title}</p>
              <p className="text-xs text-muted-foreground">
                {fromSong.key && `Key: ${fromSong.key}`}
                {fromSong.key && fromSong.tempoBpm && " • "}
                {fromSong.tempoBpm && `${Math.round(fromSong.tempoBpm)} BPM`}
              </p>
            </div>
          )}
          <ArrowRightLeft className="size-4 text-muted-foreground shrink-0" />
          {toSong && (
            <div className="flex-1 min-w-0">
              <p className="text-muted-foreground text-xs">To</p>
              <p className="font-medium truncate">{toSong.title}</p>
              <p className="text-xs text-muted-foreground">
                {toSong.key && `Key: ${toSong.key}`}
                {toSong.key && toSong.tempoBpm && " • "}
                {toSong.tempoBpm && `${Math.round(toSong.tempoBpm)} BPM`}
              </p>
            </div>
          )}
        </div>
      )}

      {/* Gap control */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <Label className="flex items-center gap-2">
            <ArrowRightLeft className="size-4" />
            Gap Between Songs
          </Label>
          <span className="text-sm font-medium">{settings.gapBeats} beats</span>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="icon-sm"
            onClick={() => handleChange({ gapBeats: Math.max(0, settings.gapBeats - 0.5) })}
            aria-label="Decrease gap"
          >
            -
          </Button>
          <Slider
            value={[settings.gapBeats]}
            onValueChange={(value) => handleChange({ gapBeats: (Array.isArray(value) ? value[0] : value) as number })}
            min={0}
            max={8}
            step={0.5}
            className="flex-1"
          />
          <Button
            variant="outline"
            size="icon-sm"
            onClick={() => handleChange({ gapBeats: Math.min(8, settings.gapBeats + 0.5) })}
            aria-label="Increase gap"
          >
            +
          </Button>
        </div>
        <p className="text-xs text-muted-foreground">
          Time between songs: ~{Math.round(settings.gapBeats * 0.5)} seconds
        </p>
      </div>

      {/* Crossfade */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Volume2 className="size-4" />
            <Label htmlFor="crossfade-desktop">Crossfade</Label>
          </div>
          <Switch
            id="crossfade-desktop"
            checked={settings.crossfadeEnabled}
            onCheckedChange={(checked) => handleChange({ crossfadeEnabled: checked })}
          />
        </div>
        {settings.crossfadeEnabled && (
          <div className="pl-6 space-y-2">
            <Label className="text-xs">Duration</Label>
            <Slider
              value={[settings.crossfadeDurationSeconds || 2]}
              onValueChange={(value) => handleChange({ crossfadeDurationSeconds: (Array.isArray(value) ? value[0] : value) as number })}
              min={0.5}
              max={5}
              step={0.5}
            />
            <p className="text-xs text-muted-foreground">
              {settings.crossfadeDurationSeconds || 2} seconds
            </p>
          </div>
        )}
      </div>

      {/* Key shift (desktop only) */}
      <div className="space-y-3">
        <Label className="flex items-center gap-2">
          <Piano className="size-4" />
          Key Shift
        </Label>
        <Select
          value={settings.keyShiftSemitones.toString()}
          onValueChange={(value) => handleChange({ keyShiftSemitones: parseInt(value ?? "0", 10) })}
        >
          <SelectTrigger>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {KEY_SHIFT_OPTIONS.map((option) => (
              <SelectItem key={option.value} value={option.value.toString()}>
                {option.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <p className="text-xs text-muted-foreground">
          Current: {formatKeyShift(settings.keyShiftSemitones)}
        </p>
      </div>

      {/* Tempo nudge (desktop only) */}
      <div className="space-y-3">
        <Label className="flex items-center gap-2">
          <Gauge className="size-4" />
          Tempo Adjustment
        </Label>
        <Select
          value={settings.tempoRatio.toString()}
          onValueChange={(value) => handleChange({ tempoRatio: parseFloat(value ?? "1.0") })}
        >
          <SelectTrigger>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {TEMPO_OPTIONS.map((option) => (
              <SelectItem key={option.value} value={option.value.toString()}>
                {option.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <p className="text-xs text-muted-foreground">
          Current: {formatTempo(settings.tempoRatio)}
        </p>
      </div>

      {/* Preview button */}
      {onPreview && (
        <Button onClick={onPreview} className="w-full" variant="outline">
          <Music className="size-4 mr-2" />
          Preview Transition
        </Button>
      )}
    </div>
  );
}

export function TransitionPanel({
  fromSong,
  toSong,
  settings,
  onChange,
  onPreview,
  isOpen,
  onOpenChange,
  className,
}: TransitionPanelProps) {
  // If used as a sheet (mobile)
  if (isOpen !== undefined && onOpenChange) {
    return (
      <Sheet open={isOpen} onOpenChange={onOpenChange}>
        <SheetContent side="bottom" className="h-[85vh]">
          <SheetHeader>
            <SheetTitle>Edit Transition</SheetTitle>
          </SheetHeader>
          <div className="mt-6">
            <PhoneLayout
              settings={settings}
              onChange={onChange}
              onPreview={onPreview}
            />
          </div>
        </SheetContent>
      </Sheet>
    );
  }

  // Inline panel (desktop or embedded)
  return (
    <Card className={cn("w-full", className)}>
      <CardHeader>
        <CardTitle className="text-base flex items-center gap-2">
          <ArrowRightLeft className="size-4" />
          Transition Settings
        </CardTitle>
      </CardHeader>
      <CardContent>
        {/* Responsive layout: phone vs desktop */}
        <div className="lg:hidden">
          <PhoneLayout
            settings={settings}
            onChange={onChange}
            onPreview={onPreview}
          />
        </div>
        <div className="hidden lg:block">
          <DesktopLayout
            fromSong={fromSong}
            toSong={toSong}
            settings={settings}
            onChange={onChange}
            onPreview={onPreview}
          />
        </div>
      </CardContent>
    </Card>
  );
}

// Hook for using transition panel as a sheet
export function useTransitionSheet() {
  const [isOpen, setIsOpen] = useState(false);
  const [selectedItemId, setSelectedItemId] = useState<string | null>(null);

  const open = (itemId: string) => {
    setSelectedItemId(itemId);
    setIsOpen(true);
  };

  const close = () => {
    setIsOpen(false);
    setSelectedItemId(null);
  };

  return {
    isOpen,
    selectedItemId,
    open,
    close,
    setIsOpen,
  };
}
