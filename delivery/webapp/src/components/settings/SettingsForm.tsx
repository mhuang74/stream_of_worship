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
import { FONT_FAMILIES, TEMPLATES, RESOLUTIONS } from "@/lib/constants";
import { useLocale } from "@/hooks/useLocale";
import { optionKey } from "@/lib/i18n/messages";
import type { Locale, TranslationKey } from "@/lib/i18n/messages";

export interface UserSettingsData {
  offlineAutoCache: boolean;
  defaultGapBeats: number;
  defaultVideoTemplate: string;
  defaultResolution: string;
  lyricsLoopWindowSeconds: number;
  defaultFontSizePreset: string;
  defaultFontFamily: string;
  defaultKeyShiftSemitones: number;
  timingReviewFont: string;
  locale: Locale;
}

interface SettingsFormProps {
  initialSettings: UserSettingsData;
  onSave: (settings: UserSettingsData) => Promise<void>;
  isSaving?: boolean;
}

const FONT_PRESETS = [
  { value: "S", key: "settings.option.fontPreset.S" as const },
  { value: "M", key: "settings.option.fontPreset.M" as const },
  { value: "L", key: "settings.option.fontPreset.L" as const },
  { value: "XL", key: "settings.option.fontPreset.XL" as const },
] as const;

const TIMING_FONTS = [
  { value: "sans", key: "settings.option.timingFont.sans" as const },
  { value: "mono", key: "settings.option.timingFont.mono" as const },
  { value: "serif", key: "settings.option.timingFont.serif" as const },
] as const;

const LOCALE_OPTIONS: { value: Locale; key: TranslationKey }[] = [
  { value: "en", key: "settings.language.en" },
  { value: "zh-Hant", key: "settings.language.zhHant" },
];

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
  const { t } = useLocale();
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
        {/* Language */}
        <Card>
          <CardHeader>
            <CardTitle>{t("settings.language")}</CardTitle>
            <CardDescription>{t("settings.language.description")}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="locale">{t("settings.language")}</Label>
              <Select
                value={settings.locale}
                onValueChange={(v) => {
                  if (v === "en" || v === "zh-Hant") update("locale", v);
                }}
              >
                <SelectTrigger id="locale">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {LOCALE_OPTIONS.map((opt) => (
                    <SelectItem key={opt.value} value={opt.value}>
                      {t(opt.key)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </CardContent>
        </Card>

        {/* Transitions */}
        <Card>
          <CardHeader>
            <CardTitle>{t("settings.section.transitions")}</CardTitle>
            <CardDescription>{t("settings.transitions.description")}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="defaultGapBeats">{t("settings.defaultGapBeats")}</Label>
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
                      {b} {t(b === 1 ? "settings.unit.beat" : "settings.unit.beats")}
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
            <CardTitle>{t("settings.section.video")}</CardTitle>
            <CardDescription>{t("settings.video.description")}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="defaultVideoTemplate">{t("settings.defaultTemplate")}</Label>
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
                  {TEMPLATES.map((tmpl) => (
                    <SelectItem key={tmpl.value} value={tmpl.value}>
                      {t(optionKey.template(tmpl.value))}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-2">
              <Label htmlFor="defaultResolution">{t("settings.defaultResolution")}</Label>
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
                      {t(optionKey.resolution(r.value))}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-2">
              <Label htmlFor="defaultFontSizePreset">{t("settings.defaultFontSize")}</Label>
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
                      {t(f.key)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-2">
              <Label htmlFor="defaultFontFamily">{t("settings.defaultFontFamily")}</Label>
              <Select
                value={settings.defaultFontFamily}
                onValueChange={(v) => {
                  if (v !== null) update("defaultFontFamily", v);
                }}
              >
                <SelectTrigger id="defaultFontFamily">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {FONT_FAMILIES.map((f) => (
                    <SelectItem key={f.value} value={f.value}>
                      {t(optionKey.fontFamily(f.value))}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <div
                className="rounded-md border border-muted-foreground/20 bg-muted/50 p-3 text-center"
                style={{
                  fontFamily: `var(${FONT_FAMILIES.find((f) => f.value === settings.defaultFontFamily)?.cssVariable ?? "--font-noto-serif-tc"})`,
                }}
              >
                <p className="text-lg">耶和華是我的牧者</p>
                <p className="text-sm text-muted-foreground">我必不至缺乏</p>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Playback */}
        <Card>
          <CardHeader>
            <CardTitle>{t("settings.section.playback")}</CardTitle>
            <CardDescription>{t("settings.playback.description")}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="lyricsLoopWindowSeconds">{t("settings.lyricsLoopWindow")}</Label>
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
                      {s} {t(s === 1 ? "settings.unit.second" : "settings.unit.seconds")}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-sm text-muted-foreground">{t("settings.lyricsLoopWindowHint")}</p>
            </div>
          </CardContent>
        </Card>

        {/* Offline */}
        <Card>
          <CardHeader>
            <CardTitle>{t("settings.section.offline")}</CardTitle>
            <CardDescription>{t("settings.offline.description")}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex items-center justify-between">
              <div className="space-y-0.5">
                <div className="flex items-center gap-2">
                  <Label htmlFor="offlineAutoCache">{t("settings.autoCacheAfterRender")}</Label>
                  {showIOSNote && (
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Info className="size-4 text-muted-foreground cursor-help" />
                      </TooltipTrigger>
                      <TooltipContent>
                        <p>{t("settings.iosNote")}</p>
                      </TooltipContent>
                    </Tooltip>
                  )}
                </div>
                <p className="text-sm text-muted-foreground">{t("settings.autoCacheHint")}</p>
                {showIOSNote && (
                  <p
                    className="text-sm text-yellow-600 dark:text-yellow-400"
                    data-testid="ios-offline-note"
                  >
                    {t("settings.iosNote")}
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
              <CardTitle>{t("settings.section.advanced")}</CardTitle>
              <CardDescription>{t("settings.advanced.description")}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="defaultKeyShiftSemitones">{t("settings.defaultKeyShift")}</Label>
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
                        {s > 0 ? `+${s}` : s === 0 ? t("settings.noKeyShift") : s} {t("settings.unit.semitones")}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <p className="text-sm text-muted-foreground">{t("settings.keyShiftHint")}</p>
              </div>

              <div className="space-y-2">
                <Label htmlFor="timingReviewFont">{t("settings.timingReviewFont")}</Label>
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
                        {t(f.key)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <p className="text-sm text-muted-foreground">{t("settings.timingReviewFontHint")}</p>
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
            {t("settings.reset")}
          </button>
          <button
            type="submit"
            disabled={isSaving || !isDirty}
            className="flex-1 rounded-lg bg-primary px-4 py-3 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-50"
          >
            {isSaving ? t("settings.saving") : t("settings.save")}
          </button>
        </div>
      </form>
    </TooltipProvider>
  );
}
