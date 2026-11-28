"use client"

import { useState } from "react"
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { TooltipProvider } from "@/components/ui/tooltip"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { AlertCircle, Info } from "lucide-react"
import Link from "next/link"
import {
  FONT_FAMILIES,
  FONT_SIZES,
  TEMPLATES,
  RESOLUTIONS,
  TITLE_CARD_DURATIONS,
  TITLE_CARD_DURATION_DEFAULT,
  normalizeTitleCardDuration,
  type FontFamilyValue,
} from "@/lib/constants"
import { formatDuration } from "@/lib/format"
import { useLocale } from "@/hooks/useLocale"
import { optionKey, type TranslationKey } from "@/lib/i18n/messages"
import { resumeGlueJobRequest } from "@/lib/glue/client"
import { glueStaleMessage } from "@/lib/glue/stale-copy"
import type {
  GlueRenderPreflight,
  GlueRenderPreflightGlue,
} from "@/lib/db/songsets"
import { arrangedVideoLeadIn } from "@/lib/render/opening-timing"

export interface RenderFormData {
  audioEnabled: boolean
  videoEnabled: boolean
  template: "dark" | "gradient_warm" | "gradient_blue"
  resolution: "720p" | "1080p"
  fontSizePreset: "S" | "M" | "L" | "XL"
  fontFamily: FontFamilyValue
  includeTitleCard: boolean
  titleCardDurationSeconds: number
  titleCardLines: string[]
}

interface RenderFormProps {
  songsetId: string
  initialData?: Partial<RenderFormData>
  markedLineCount?: number
  songsetName?: string
  songs?: { id: string; title: string }[]
  onSubmit: (data: RenderFormData) => void
  onCancel: () => void
  isSubmitting?: boolean
  previousRenderJob?: PreviousRenderJobData
  currentSongCount?: number
  currentSongsetDurationSeconds?: number | null
  arrangedOpening?: boolean
  openingSilenceSeconds?: number
  /** Blocking AI-transition preflight; non-null disables Start Render. */
  gluePreflight?: GlueRenderPreflight | null
  /** Re-resolve the page after a retry/disable action. */
  onGluePreflightChanged?: () => void
}

export interface PreviousRenderJobData {
  id: string
  createdAt: string
  template: string
  fontFamily: string
  fontSizePreset: string
  includeTitleCard: boolean
  titleCardDurationSeconds?: number
  resolution?: string
  totalDurationSeconds?: number | null
  songCount?: number | null
  songsetDurationSeconds?: number | null
}

// Stable preflight reason codes -> user copy. Internal Glue vocabulary never
// reaches the UI; unknown codes fall back to a generic unavailable message.
const GLUE_PREFLIGHT_REASON_KEYS: Readonly<Record<string, TranslationKey>> = {
  GLUE_NOT_READY: "render.gluePreflight.reason.notReady",
  GLUE_ACTIVE_JOB: "render.gluePreflight.reason.activeJob",
  GLUE_STALE_SNAPSHOT: "render.gluePreflight.reason.staleSnapshot",
  GLUE_QUOTA_EXHAUSTED: "render.gluePreflight.reason.quotaExhausted",
  GLUE_DAILY_LIMIT: "render.gluePreflight.reason.quotaExhausted",
  GLUE_TEMPO_UNAVAILABLE: "render.gluePreflight.reason.tempoUnavailable",
  GLUE_NOT_ANALYZED: "render.gluePreflight.reason.notAnalyzed",
  GLUE_ARRANGEMENT_INVALID: "render.gluePreflight.reason.arrangementInvalid",
  GLUE_UNSAVED_DRAFT: "render.gluePreflight.reason.unsavedDraft",
  GLUE_WORKER_FAILED: "render.gluePreflight.reason.workerFailed",
  GLUE_RETRY_PAID: "render.gluePreflight.reason.retryPaid",
  GLUE_RETRY_PROCESSING_FREE: "render.gluePreflight.reason.retryFree",
  GLUE_PROVIDER_UNKNOWN: "render.gluePreflight.reason.providerUnknown",
  GLUE_PROVIDER_ENTITLEMENT: "render.gluePreflight.reason.entitlement",
  GLUE_PROVIDER_PLAN: "render.gluePreflight.reason.plan",
  GLUE_COPYRIGHT_REJECTED: "render.gluePreflight.reason.copyright",
  GLUE_EXCERPT_TOO_SHORT: "render.gluePreflight.reason.tooShort",
  GLUE_AUDIO_UNAVAILABLE: "render.gluePreflight.reason.audioUnavailable",
  GLUE_JUNCTION_AUDIO_UNAVAILABLE: "render.gluePreflight.reason.junctionAudioUnavailable",
  GLUE_EXECUTION_DEADLINE: "render.gluePreflight.reason.executionDeadline",
  GLUE_DISPATCH_FAILED: "render.gluePreflight.reason.dispatchFailed",
  GLUE_INPUTS_CHANGED: "render.gluePreflight.reason.inputsChanged",
  GLUE_NOT_CONFIGURED: "render.gluePreflight.reason.notConfigured",
}

const GLUE_PREFLIGHT_PHASE_KEYS: Readonly<Record<string, TranslationKey>> = {
  queued: "glue.state.queued",
  preparing: "glue.state.preparing",
  uploading: "glue.state.uploading",
  composing: "glue.state.composing",
  "storing-response": "glue.state.storing-response",
  staging: "glue.state.staging",
  ready: "glue.state.ready",
  failed: "glue.state.failed",
  cancelled: "glue.state.failed",
  "dispatch-failed": "glue.state.failed",
}

function formatDurationSafe(seconds: number | null | undefined): string {
  if (seconds == null || seconds <= 0) return "—"
  return formatDuration(seconds)
}

function isDifferent(prev: unknown, curr: unknown): boolean {
  const p = prev ?? null
  const c = curr ?? null
  if (p === null && c === null) return false
  if (p === null || c === null) return true
  if (typeof p === "number" && typeof c === "number") {
    return Math.round(p) !== Math.round(c)
  }
  return String(p) !== String(c)
}

export function RenderForm({
  songsetId,
  initialData,
  markedLineCount = 0,
  songsetName,
  songs,
  onSubmit,
  onCancel,
  isSubmitting = false,
  previousRenderJob,
  currentSongCount,
  currentSongsetDurationSeconds,
  arrangedOpening,
  openingSilenceSeconds,
  gluePreflight = null,
  onGluePreflightChanged,
}: RenderFormProps) {
  const { t } = useLocale()
  // Mirror the render worker's title card: repeated Items of one Song collapse
  // to the first occurrence, while two distinct Songs sharing a title both show.
  const previewSongTitles = (() => {
    if (!songs) return [] as string[]
    const seenSongIds = new Set<string>()
    const titles: string[] = []
    for (const song of songs) {
      if (!song.title || seenSongIds.has(song.id)) continue
      seenSongIds.add(song.id)
      titles.push(song.title)
    }
    return titles
  })()
  const [formData, setFormData] = useState<RenderFormData>({
    audioEnabled: initialData?.audioEnabled ?? true,
    videoEnabled: initialData?.videoEnabled ?? true,
    template: initialData?.template ?? "dark",
    resolution: initialData?.resolution ?? "720p",
    fontSizePreset: initialData?.fontSizePreset ?? "M",
    fontFamily: initialData?.fontFamily ?? "noto_serif_tc",
    includeTitleCard: initialData?.includeTitleCard ?? false,
    titleCardDurationSeconds: normalizeTitleCardDuration(initialData?.titleCardDurationSeconds),
    titleCardLines: initialData?.titleCardLines ?? [],
  })

  const [showConfirmDialog, setShowConfirmDialog] = useState(false)
  const [busyGlueKey, setBusyGlueKey] = useState<string | null>(null)
  const [glueErrorKey, setGlueErrorKey] = useState<TranslationKey | null>(null)
  const [disableTarget, setDisableTarget] = useState<GlueRenderPreflightGlue | null>(null)

  const glueBlocked = (gluePreflight?.glues.length ?? 0) > 0

  const editorHref = (glue: GlueRenderPreflightGlue) => glue.target
    ? `/songsets/${songsetId}#ai-transitions`
    : `/songsets/${songsetId}?glueItem=${encodeURIComponent(glue.fromItemId)}`

  const retryGlue = async (glue: GlueRenderPreflightGlue) => {
    if (!glue.jobId) return
    const key = glue.target ?? `${glue.fromItemId}:${glue.toItemId}`
    setGlueErrorKey(null)
    setBusyGlueKey(key)
    try {
      const result = await resumeGlueJobRequest(glue.jobId)
      if (!result.ok) {
        setGlueErrorKey("render.gluePreflight.retryFailed")
        return
      }
      onGluePreflightChanged?.()
    } catch {
      setGlueErrorKey("render.gluePreflight.retryFailed")
    } finally {
      setBusyGlueKey(null)
    }
  }

  const disableGlue = async (glue: GlueRenderPreflightGlue) => {
    setDisableTarget(null)
    const key = glue.target ?? `${glue.fromItemId}:${glue.toItemId}`
    setGlueErrorKey(null)
    setBusyGlueKey(key)
    try {
      const response = await fetch(`/api/songsets/${songsetId}${glue.target ? "" : "/items"}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(glue.target
          ? { boundary: { target: glue.target, enabled: false } }
          : { itemId: glue.fromItemId, glueEnabled: false }),
      })
      if (!response.ok) {
        setGlueErrorKey("render.gluePreflight.disableFailed")
        return
      }
      onGluePreflightChanged?.()
    } catch {
      setGlueErrorKey("render.gluePreflight.disableFailed")
    } finally {
      setBusyGlueKey(null)
    }
  }

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (glueBlocked) return
    if (previousRenderJob) {
      setShowConfirmDialog(true)
    } else {
      onSubmit(formData)
    }
  }

  const handleConfirmRender = () => {
    setShowConfirmDialog(false)
    onSubmit(formData)
  }

  const updateField = <K extends keyof RenderFormData>(
    field: K,
    value: RenderFormData[K]
  ) => {
    setFormData((prev) => ({ ...prev, [field]: value }))
  }

  return (
    <TooltipProvider>
      <form onSubmit={handleSubmit} className="space-y-6">
        {glueBlocked && gluePreflight && (
          <Card data-testid="render-glue-preflight" className="border-destructive/40">
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <AlertCircle className="size-4 text-destructive" />
                {t("render.gluePreflight.heading")}
              </CardTitle>
              <CardDescription>{t("render.gluePreflight.body")}</CardDescription>
            </CardHeader>
            <CardContent>
              <ul className="space-y-4">
                {gluePreflight.glues.map((glue) => {
                  const key = glue.target ?? `${glue.fromItemId}:${glue.toItemId}`
                  const busy = busyGlueKey === key
                  const phaseLabel = glue.jobPhase
                    ? t(GLUE_PREFLIGHT_PHASE_KEYS[glue.jobPhase] ?? "glue.state.failed")
                    : null
                  return (
                    <li
                      key={key}
                      data-testid={`render-glue-preflight-${glue.target ?? glue.fromItemId}`}
                      className="space-y-2 rounded-lg border border-border/60 p-3"
                    >
                      <div className="flex flex-wrap items-center gap-2 text-sm font-medium">
                        {glue.target ? <span>{t(`glue.boundary.${glue.target}`)}</span> : <>
                          <span>{glue.fromTitle}</span>
                          <span aria-hidden="true">→</span>
                          <span>{glue.toTitle}</span>
                        </>}
                      </div>
                      <p className="text-sm text-muted-foreground">
                        {glue.reasonCode === "GLUE_STALE_SNAPSHOT" &&
                        glue.staleChange !== null
                          ? glueStaleMessage(glue.staleChange, t, {
                              from: glue.fromTitle,
                              to: glue.toTitle,
                            })
                          : t(
                              GLUE_PREFLIGHT_REASON_KEYS[glue.reasonCode] ??
                                "render.gluePreflight.reason.unknown"
                            )}
                      </p>
                      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
                        {phaseLabel && (
                          <span data-testid="render-glue-preflight-phase">
                            {t("render.gluePreflight.phaseLabel")} {phaseLabel}
                          </span>
                        )}
                        {glue.jobId && (
                          <span data-testid="render-glue-preflight-job">
                            {t("render.gluePreflight.jobLabel")} {glue.jobId}
                          </span>
                        )}
                      </div>
                      <div className="flex flex-wrap items-center gap-3 text-sm">
                        {glue.retryKind === "free" && glue.jobId ? (
                          <button
                            type="button"
                            className="font-medium text-primary underline-offset-4 hover:underline disabled:opacity-50"
                            onClick={() => void retryGlue(glue)}
                            disabled={busy}
                          >
                            {t("render.gluePreflight.retryFree")}
                          </button>
                        ) : (
                          <Link
                            href={editorHref(glue)}
                            className="font-medium text-primary underline-offset-4 hover:underline"
                          >
                            {t("render.gluePreflight.retryPaid")}
                          </Link>
                        )}
                        <Link
                          href={editorHref(glue)}
                          className="font-medium text-primary underline-offset-4 hover:underline"
                        >
                          {t("render.gluePreflight.openEditor")}
                        </Link>
                        <button
                          type="button"
                          className="font-medium text-destructive underline-offset-4 hover:underline disabled:opacity-50"
                          onClick={() => setDisableTarget(glue)}
                          disabled={busy}
                        >
                          {t("render.gluePreflight.disable")}
                        </button>
                      </div>
                    </li>
                  )
                })}
              </ul>
              {glueErrorKey && (
                <p role="alert" className="mt-3 text-sm text-destructive">
                  {t(glueErrorKey)}
                </p>
              )}
            </CardContent>
          </Card>
        )}
        {previousRenderJob && (
          <div className="flex items-center gap-2 rounded-lg border border-blue-500/20 bg-blue-500/10 px-4 py-2.5">
            <Info className="size-4 shrink-0 text-blue-600 dark:text-blue-400" />
            <span className="text-sm text-blue-900 dark:text-blue-100">
              {t("render.previousRender.notice")}{" "}
              {new Intl.DateTimeFormat(undefined, {
                dateStyle: "medium",
                timeStyle: "short",
              }).format(new Date(previousRenderJob.createdAt))}
            </span>
          </div>
        )}

        {/* Output Options */}
        <Card>
          <CardHeader>
            <CardTitle>{t("render.output.title")}</CardTitle>
            <CardDescription>{t("render.output.description")}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex items-center justify-between">
              <div className="space-y-0.5">
                <Label htmlFor="audio">{t("render.output.audioLabel")}</Label>
                <p className="text-sm text-muted-foreground">
                  {t("render.output.audioDescription")}
                </p>
              </div>
              <Switch
                id="audio"
                checked={formData.audioEnabled}
                onCheckedChange={(checked) =>
                  updateField("audioEnabled", checked)
                }
              />
            </div>

            <div className="flex items-center justify-between">
              <div className="space-y-0.5">
                <Label htmlFor="video">{t("render.output.videoLabel")}</Label>
                <p className="text-sm text-muted-foreground">
                  {t("render.output.videoDescription")}
                </p>
              </div>
              <Switch
                id="video"
                checked={formData.videoEnabled}
                onCheckedChange={(checked) =>
                  updateField("videoEnabled", checked)
                }
              />
            </div>
          </CardContent>
        </Card>

        {/* Video Settings */}
        {formData.videoEnabled && (
          <Card>
            <CardHeader>
              <CardTitle>{t("render.video.title")}</CardTitle>
              <CardDescription>{t("render.video.description")}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="template">{t("render.video.template")}</Label>
                <Select
                  value={formData.template}
                  onValueChange={(value) =>
                    updateField("template", value as RenderFormData["template"])
                  }
                >
                  <SelectTrigger id="template">
                    <SelectValue>{(v: string) => t(optionKey.template(v))}</SelectValue>
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
                <Label htmlFor="resolution">{t("render.video.resolution")}</Label>
                <Select
                  value={formData.resolution}
                  onValueChange={(value) =>
                    updateField("resolution", value as RenderFormData["resolution"])
                  }
                >
                  <SelectTrigger id="resolution">
                    <SelectValue>{(v: string) => t(optionKey.resolution(v))}</SelectValue>
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
                <Label htmlFor="fontSize">{t("render.video.fontSize")}</Label>
                <Select
                  value={formData.fontSizePreset}
                  onValueChange={(value) =>
                    updateField("fontSizePreset", value as RenderFormData["fontSizePreset"])
                  }
                >
                  <SelectTrigger id="fontSize">
                    <SelectValue>{(v: string) => t(optionKey.fontPreset(v))}</SelectValue>
                  </SelectTrigger>
                  <SelectContent>
                    {FONT_SIZES.map((f) => (
                      <SelectItem key={f.value} value={f.value}>
                        {t(optionKey.fontPreset(f.value))}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="space-y-2">
                <Label htmlFor="fontFamily">{t("render.video.fontFamily")}</Label>
                <Select
                  value={formData.fontFamily}
                  onValueChange={(value) =>
                    updateField("fontFamily", value as RenderFormData["fontFamily"])
                  }
                >
                  <SelectTrigger id="fontFamily">
                    <SelectValue>{(v: string) => t(optionKey.fontFamily(v))}</SelectValue>
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
                    fontFamily: `var(${FONT_FAMILIES.find((f) => f.value === formData.fontFamily)?.cssVariable ?? "--font-noto-serif-tc"})`,
                  }}
                >
                  <p className="text-lg">耶和華是我的牧者</p>
                  <p className="text-sm text-muted-foreground">我必不至缺乏</p>
                </div>
              </div>
            </CardContent>
          </Card>
        )}

        {/* Title Card */}
        <Card>
          <CardHeader>
            <CardTitle>{t("render.titleCard.title")}</CardTitle>
            <CardDescription>{t("render.titleCard.description")}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex items-center space-x-2">
              <Checkbox
                id="includeTitleCard"
                checked={formData.includeTitleCard}
                onCheckedChange={(checked) =>
                  updateField("includeTitleCard", checked as boolean)
                }
              />
              <Label htmlFor="includeTitleCard">{t("render.titleCard.include")}</Label>
            </div>

            {formData.includeTitleCard && (
              <div className="space-y-4 pl-6">
                <div className="space-y-2">
                  <Label htmlFor="titleCardDuration">{t("render.titleCard.duration")}</Label>
                  <Select
                    value={(formData.titleCardDurationSeconds ?? TITLE_CARD_DURATION_DEFAULT).toString()}
                    onValueChange={(value) =>
                      updateField(
                        "titleCardDurationSeconds",
                        parseInt(value ?? String(TITLE_CARD_DURATION_DEFAULT), 10)
                      )
                    }
                  >
                    <SelectTrigger id="titleCardDuration">
                      <SelectValue>{(v: string) => t(optionKey.titleCardDuration(parseInt(v, 10)))}</SelectValue>
                    </SelectTrigger>
                    <SelectContent>
                      {TITLE_CARD_DURATIONS.map((d) => (
                        <SelectItem key={d} value={d.toString()}>
                          {t(optionKey.titleCardDuration(d))}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>

                <div className="space-y-2">
                  <Label htmlFor="titleCardLines">{t("render.titleCard.customText")}</Label>
                  <p className="text-sm text-muted-foreground">
                    {t("render.titleCard.customTextHint")}
                  </p>
                  <textarea
                    id="titleCardLines"
                    className="flex min-h-[100px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                    placeholder={t("render.titleCard.placeholder")}
                    value={formData.titleCardLines.join("\n")}
                    onChange={(e) => {
                      const lines = e.target.value.split("\n").filter((line) => line.trim() !== "")
                      updateField("titleCardLines", lines)
                    }}
                  />
                </div>

                {formData.titleCardLines.length === 0 && previewSongTitles.length > 0 && (
                  <div className="rounded-md border border-dashed border-muted-foreground/25 bg-muted/50 p-3">
                    <p className="text-xs text-muted-foreground mb-1">{t("render.titleCard.defaultLines")}</p>
                    <p
                      className="text-sm text-muted-foreground whitespace-pre-line"
                      data-testid="title-card-preview"
                    >
                      {songsetName || t("render.titleCard.worshipSet")}{"\n"}
                      {/* A Song occupying several Items is one title-card line,
                          matching the render worker's deduped title card. */}
                      {previewSongTitles.join("\n")}
                    </p>
                  </div>
                )}
              </div>
            )}
          </CardContent>
        </Card>

        {/* Marked Lines Warning */}
        {markedLineCount > 0 && (
          <div className="flex items-start gap-3 rounded-lg border border-yellow-500/20 bg-yellow-500/10 p-4">
            <AlertCircle className="size-5 shrink-0 text-yellow-600" />
            <div className="flex-1 space-y-1">
              <p className="font-medium text-yellow-900 dark:text-yellow-100">
                {markedLineCount} {t(markedLineCount !== 1 ? "render.markedLines.plural" : "render.markedLines.singular")}
              </p>
              <p className="text-sm text-yellow-800 dark:text-yellow-200">
                {t("render.markedLines.hint")}
              </p>
            </div>
            <Link
              href={`/songsets/${songsetId}`}
              className="text-sm font-medium text-yellow-900 underline underline-offset-4 hover:text-yellow-800 dark:text-yellow-100 dark:hover:text-yellow-200"
            >
              {t("render.markedLines.review")}
            </Link>
          </div>
        )}

        {/* Action Buttons */}
        <div className="flex gap-3 pt-4">
          <button
            type="button"
            onClick={onCancel}
            disabled={isSubmitting}
            className="flex-1 rounded-lg border border-input bg-background px-4 py-3 text-sm font-medium transition-colors hover:bg-muted disabled:opacity-50"
          >
            {t("render.action.cancel")}
          </button>
          <button
            type="submit"
            disabled={
              isSubmitting ||
              glueBlocked ||
              (!formData.audioEnabled && !formData.videoEnabled)
            }
            className="flex-1 rounded-lg bg-primary px-4 py-3 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-50"
          >
            {isSubmitting ? t("render.action.starting") : t("render.action.start")}
          </button>
        </div>
      </form>

      <AlertDialog open={showConfirmDialog} onOpenChange={setShowConfirmDialog}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("render.confirm.title")}</AlertDialogTitle>
            <AlertDialogDescription>
              {t("render.confirm.description")}
            </AlertDialogDescription>
          </AlertDialogHeader>
          {previousRenderJob && (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b">
                    <th className="py-1.5 pr-3 text-left font-medium text-muted-foreground">{t("render.confirm.parameter")}</th>
                    <th className="py-1.5 px-3 text-left font-medium text-muted-foreground">{t("render.confirm.previous")}</th>
                    <th className="py-1.5 pl-3 text-left font-medium text-muted-foreground">{t("render.confirm.current")}</th>
                  </tr>
                </thead>
                <tbody>
                  {(() => {
                    const fontLabel = (v: string) => FONT_FAMILIES.some((f) => f.value === v) ? t(optionKey.fontFamily(v)) : v
                    const fontSizeLabel = (v: string) => FONT_SIZES.some((f) => f.value === v) ? t(optionKey.fontPreset(v)) : v
                    const templateLabel = (v: string) => TEMPLATES.some((tmpl) => tmpl.value === v) ? t(optionKey.template(v)) : v
                    const resolutionLabel = (v?: string) => v && RESOLUTIONS.some((r) => r.value === v) ? t(optionKey.resolution(v)) : v ?? "—"
                    const prevTitleCard = previousRenderJob.includeTitleCard
                      ? `${t("render.compare.titleCardOn")} (${previousRenderJob.titleCardDurationSeconds ?? 10}s)`
                      : t("render.compare.titleCardOff")
                    const currTitleCard = formData.includeTitleCard
                      ? `${t("render.compare.titleCardOn")} (${formData.titleCardDurationSeconds ?? TITLE_CARD_DURATION_DEFAULT}s)`
                      : t("render.compare.titleCardOff")
                    const prevSongCount = previousRenderJob.songCount != null ? String(previousRenderJob.songCount) : "—"
                    const currSongCount = currentSongCount != null ? String(currentSongCount) : "—"
                    const prevSongsetDuration = formatDurationSafe(previousRenderJob.songsetDurationSeconds)
                    const currSongsetDuration = formatDurationSafe(currentSongsetDurationSeconds)
                    const prevTotalDuration = formatDurationSafe(previousRenderJob.totalDurationSeconds)
                    const estimatedTotalDuration = (currentSongsetDurationSeconds ?? 0) + (arrangedOpening
                      ? arrangedVideoLeadIn({ arrangedOpening, openingSilenceSeconds }, formData)
                      : formData.videoEnabled && formData.includeTitleCard ? formData.titleCardDurationSeconds : 0)
                    const currTotalDuration = estimatedTotalDuration > 0 ? `${t("render.compare.estimatedPrefix")}${formatDuration(estimatedTotalDuration)}` : "—"

                    const rows: { id: string; label: string; prev: string; curr: string; diff: boolean }[] = [
                      { id: "font", label: t("render.compare.font"), prev: fontLabel(previousRenderJob.fontFamily), curr: fontLabel(formData.fontFamily), diff: isDifferent(previousRenderJob.fontFamily, formData.fontFamily) },
                      { id: "fontSize", label: t("render.compare.fontSize"), prev: fontSizeLabel(previousRenderJob.fontSizePreset), curr: fontSizeLabel(formData.fontSizePreset), diff: isDifferent(previousRenderJob.fontSizePreset, formData.fontSizePreset) },
                      { id: "background", label: t("render.compare.background"), prev: templateLabel(previousRenderJob.template), curr: templateLabel(formData.template), diff: isDifferent(previousRenderJob.template, formData.template) },
                      { id: "resolution", label: t("render.compare.resolution"), prev: resolutionLabel(previousRenderJob.resolution), curr: resolutionLabel(formData.resolution), diff: isDifferent(previousRenderJob.resolution, formData.resolution) },
                      { id: "titleCard", label: t("render.compare.titleCard"), prev: prevTitleCard, curr: currTitleCard, diff: isDifferent(previousRenderJob.includeTitleCard, formData.includeTitleCard) || (formData.includeTitleCard && isDifferent(previousRenderJob.titleCardDurationSeconds ?? 10, formData.titleCardDurationSeconds)) },
                      { id: "songs", label: t("render.compare.songs"), prev: prevSongCount, curr: currSongCount, diff: isDifferent(previousRenderJob.songCount, currentSongCount) },
                      { id: "songsetDuration", label: t("render.compare.songsetDuration"), prev: prevSongsetDuration, curr: currSongsetDuration, diff: isDifferent(previousRenderJob.songsetDurationSeconds || null, currentSongsetDurationSeconds || null) },
                      { id: "totalDuration", label: t("render.compare.totalDuration"), prev: prevTotalDuration, curr: currTotalDuration, diff: isDifferent(previousRenderJob.totalDurationSeconds || null, estimatedTotalDuration || null) },
                    ]

                    return rows.map((row) => (
                      <tr key={row.id} className="border-b last:border-0">
                        <td className="py-1.5 pr-3 font-medium">{row.label}</td>
                        <td className="py-1.5 px-3">{row.prev}</td>
                        <td className={`py-1.5 pl-3${row.diff ? " text-amber-600 dark:text-amber-400" : ""}`}>
                          {row.diff && <span className="mr-1 inline-block size-1.5 rounded-full bg-amber-500 align-middle" />}
                          {row.curr}
                        </td>
                      </tr>
                    ))
                  })()}
                </tbody>
              </table>
            </div>
          )}
          <AlertDialogFooter>
            <AlertDialogCancel>{t("render.confirm.cancel")}</AlertDialogCancel>
            <AlertDialogAction onClick={handleConfirmRender}>
              {t("render.action.start")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog
        open={disableTarget !== null}
        onOpenChange={(open) => {
          if (!open) setDisableTarget(null)
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("render.gluePreflight.disableConfirmTitle")}</AlertDialogTitle>
            <AlertDialogDescription>
              {disableTarget ? (
                <span className="font-medium">
                  {disableTarget.fromTitle} → {disableTarget.toTitle}
                </span>
              ) : null}{" "}
              {t("render.gluePreflight.disableConfirmBody")}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t("render.gluePreflight.disableConfirmCancel")}</AlertDialogCancel>
            <AlertDialogAction onClick={() => void disableGlue(disableTarget!)}>
              {t("render.gluePreflight.disableConfirmAction")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </TooltipProvider>
  )
}
