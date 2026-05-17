"use client";

import { useState } from "react";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { Info } from "lucide-react";

export interface UserSettingsData {
  offlineAutoCache: boolean;
  defaultGapBeats: number;
  defaultVideoTemplate: string;
  defaultResolution: string;
  lyricsLoopWindowSeconds: number;
  defaultFontSizePreset: string;
  defaultKeyShiftSemitones: number;
  timingReviewFont: string;
}

interface SettingsFormProps {
  initialSettings: UserSettingsData;
  onSave: (settings: UserSettingsData) => Promise<void>;
  isSaving?: boolean;
}

const TEMPLATES = [
  { value: "dark", label: "Dark" },
  { value: "gradient_warm", label: "Gradient Warm" },
  { value: "gradient_blue", label: "Gradient Blue" },
] as const;

const RESOLUTIONS = [
  { value: "720p", label: "720p (HD)" },
  { value: "1080p", label: "1080p (Full HD)" },
] as const;

const FONT_PRESETS = [
  { value: "S", label: "Small (32px)" },
  { value: "M", label: "Medium (48px)" },
  { value: "L", label: "Large (64px)" },
  { value: "XL", label: "Extra Large (80px)" },
] as const;

const TIMING_FONTS = [
  { value: "sans", label: "Sans-serif" },
  { value: "mono", label: "Monospace" },
  { value: "serif", label: "Serif" },
] as const;

const GAP_BEATS_OPTIONS = [0, 0.5, 1, 1.5, 2, 2.5, 3, 4, 6, 8];
const LOOP_WINDOW_OPTIONS = [1, 2, 3, 5, 7, 10, 15, 20, 30];
const KEY_SHIFT_OPTIONS = [-6, -5, -4, -3, -2, -1, 0, 1, 2, 3, 4, 5, 6];

function isIOSLessThan174(): boolean {
  if (typeof navigator === "undefined") return false;
  const ua = navigator.userAgent;
  if (!/iPad|iPhone|iPod/.test(ua)) return false;
  const match = ua.match(/OS (\d+)_(\d+)/);
  if (!match) return true;
  const major = parseInt(match[1], 10);
  const minor = parseInt(match[2], 10);
  return !(major > 17 || (major === 17 && minor >= 4));
}

export function SettingsForm({ initialSettings, onSave, isSaving = false }: SettingsFormProps) {
  const [settings, setSettings] = useState<UserSettingsData>(initialSettings);
  const [isDirty, setIsDirty] = useState(false);

  const showIOSNote = isIOSLessThan174();

  function update<K extends keyof UserSettingsData>(key: K, value: UserSettingsData[K]) {
    setSettings((prev) => ({ ...prev, [key]: value }));
    setIsDirty(true);
  }

  function withSelectValue<T>(value: string | null, transform: (next: string) => T): T | null {
    return value === null ? null : transform(value);
  }

  function handleReset() {
    setSettings(initialSettings);
    setIsDirty(false);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    await onSave(settings);
    setIsDirty(false);
  }

  return (
    <TooltipProvider>
      <form onSubmit={handleSubmit} className="space-y-6">
        {/* Transitions */}
        <Card>
          <CardHeader>
            <CardTitle>Transitions</CardTitle>
            <CardDescription>Default transition parameters for new songs</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="defaultGapBeats">Default gap beats</Label>
              <Select
                value={settings.defaultGapBeats.toString()}
                onValueChange={(v) => {
                  const next = withSelectValue(v, (value) => parseFloat(value));
                  if (next !== null) update("defaultGapBeats", next);
                }}
              >
                <SelectTrigger id="defaultGapBeats">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {GAP_BEATS_OPTIONS.map((b) => (
                    <SelectItem key={b} value={b.toString()}>
                      {b} {b === 1 ? "beat" : "beats"}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </CardContent>
        </Card>

        {/* Video */}
        <Card>
          <CardHeader>
            <CardTitle>Video</CardTitle>
            <CardDescription>Default render settings for lyrics videos</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="defaultVideoTemplate">Default template</Label>
              <Select
                value={settings.defaultVideoTemplate}
                onValueChange={(v) => {
                  if (v !== null) update("defaultVideoTemplate", v);
                }}
              >
                <SelectTrigger id="defaultVideoTemplate">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {TEMPLATES.map((t) => (
                    <SelectItem key={t.value} value={t.value}>
                      {t.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-2">
              <Label htmlFor="defaultResolution">Default resolution</Label>
              <Select
                value={settings.defaultResolution}
                onValueChange={(v) => {
                  if (v !== null) update("defaultResolution", v);
                }}
              >
                <SelectTrigger id="defaultResolution">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {RESOLUTIONS.map((r) => (
                    <SelectItem key={r.value} value={r.value}>
                      {r.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-2">
              <Label htmlFor="defaultFontSizePreset">Default font size</Label>
              <Select
                value={settings.defaultFontSizePreset}
                onValueChange={(v) => {
                  if (v !== null) update("defaultFontSizePreset", v);
                }}
              >
                <SelectTrigger id="defaultFontSizePreset">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {FONT_PRESETS.map((f) => (
                    <SelectItem key={f.value} value={f.value}>
                      {f.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </CardContent>
        </Card>

        {/* Playback */}
        <Card>
          <CardHeader>
            <CardTitle>Playback</CardTitle>
            <CardDescription>Lyrics display and playback behavior</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="lyricsLoopWindowSeconds">Lyrics loop window</Label>
              <Select
                value={settings.lyricsLoopWindowSeconds.toString()}
                onValueChange={(v) => {
                  const next = withSelectValue(v, (value) => parseFloat(value));
                  if (next !== null) update("lyricsLoopWindowSeconds", next);
                }}
              >
                <SelectTrigger id="lyricsLoopWindowSeconds">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {LOOP_WINDOW_OPTIONS.map((s) => (
                    <SelectItem key={s} value={s.toString()}>
                      {s} {s === 1 ? "second" : "seconds"}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-sm text-muted-foreground">
                How many seconds of upcoming lyrics to display
              </p>
            </div>
          </CardContent>
        </Card>

        {/* Offline */}
        <Card>
          <CardHeader>
            <CardTitle>Offline</CardTitle>
            <CardDescription>Offline caching preferences</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex items-center justify-between">
              <div className="space-y-0.5">
                <div className="flex items-center gap-2">
                  <Label htmlFor="offlineAutoCache">Auto-cache after render</Label>
                  {showIOSNote && (
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Info className="size-4 text-muted-foreground cursor-help" />
                      </TooltipTrigger>
                      <TooltipContent>
                        <p>Offline caching requires iOS 17.4 or later</p>
                      </TooltipContent>
                    </Tooltip>
                  )}
                </div>
                <p className="text-sm text-muted-foreground">
                  Automatically cache rendered files for offline playback
                </p>
                {showIOSNote && (
                  <p
                    className="text-sm text-yellow-600 dark:text-yellow-400"
                    data-testid="ios-offline-note"
                  >
                    Offline caching requires iOS 17.4 or later
                  </p>
                )}
              </div>
              <Switch
                id="offlineAutoCache"
                checked={settings.offlineAutoCache}
                onCheckedChange={(checked) => update("offlineAutoCache", checked)}
                disabled={showIOSNote}
              />
            </div>
          </CardContent>
        </Card>

        {/* Desktop-only settings */}
        <div className="hidden lg:block">
          <Card>
            <CardHeader>
              <CardTitle>Advanced</CardTitle>
              <CardDescription>Desktop-only settings</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="defaultKeyShiftSemitones">Default key shift</Label>
                <Select
                  value={settings.defaultKeyShiftSemitones.toString()}
                  onValueChange={(v) => {
                    const next = withSelectValue(v, (value) => parseInt(value, 10));
                    if (next !== null) update("defaultKeyShiftSemitones", next);
                  }}
                >
                  <SelectTrigger id="defaultKeyShiftSemitones">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {KEY_SHIFT_OPTIONS.map((s) => (
                      <SelectItem key={s} value={s.toString()}>
                        {s > 0 ? `+${s}` : s === 0 ? "0 (no shift)" : s} semitones
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <p className="text-sm text-muted-foreground">
                  Default semitone shift applied to each transition
                </p>
              </div>

              <div className="space-y-2">
                <Label htmlFor="timingReviewFont">Timing review font</Label>
                <Select
                  value={settings.timingReviewFont}
                  onValueChange={(v) => {
                    if (v !== null) update("timingReviewFont", v);
                  }}
                >
                  <SelectTrigger id="timingReviewFont">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {TIMING_FONTS.map((f) => (
                      <SelectItem key={f.value} value={f.value}>
                        {f.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <p className="text-sm text-muted-foreground">
                  Font used in the timing editor for LRC review
                </p>
              </div>
            </CardContent>
          </Card>
        </div>

        {/* Action buttons */}
        <div className="flex gap-3 pt-4">
          <button
            type="button"
            onClick={handleReset}
            disabled={isSaving || !isDirty}
            className="flex-1 rounded-lg border border-input bg-background px-4 py-3 text-sm font-medium transition-colors hover:bg-muted disabled:opacity-50"
          >
            Reset
          </button>
          <button
            type="submit"
            disabled={isSaving || !isDirty}
            className="flex-1 rounded-lg bg-primary px-4 py-3 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-50"
          >
            {isSaving ? "Saving..." : "Save"}
          </button>
        </div>
      </form>
    </TooltipProvider>
  );
}
